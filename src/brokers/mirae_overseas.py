"""미래에셋증권 해외주식 거래내역서 PDF 파서."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any


def _num(text: str | None) -> float:
    if text is None:
        return 0.0
    s = str(text).strip().replace(",", "").replace(" ", "")
    if not s or s in {".", "-", "-."}:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _fx_or_zero(value: object) -> float:
    """문서에 환율이 없으면 추정하지 않고 0."""
    try:
        from src.models import coerce_fx_rate

        return coerce_fx_rate(value)
    except Exception:  # noqa: BLE001
        v = _num(str(value) if value is not None else None)
        return v if v > 0 else 0.0


def _extract_text(file_bytes: bytes) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            if t.strip():
                parts.append(t)
    return "\n".join(parts)


_HEADER = re.compile(
    r"(?P<date>\d{4}/\d{2}/\d{2})\s+"
    r"(?P<kind>해외주식매수입고|해외주식매수출금|해외주식매도출고|해외주식매도입금|배당금외화입금)\s+"
    r"(?P<ticker>\S+)"
    r"(?:\s+(?P<n1>[\d,\.]+))?"
    r"(?:\s+(?P<n2>[\d,\.]+))?"
    r"(?:\s+(?P<n3>[\d,\.]+))?"
    r"(?:\s+(?P<n4>[\d,\.]+))?"
)

# 환율 컬럼: `1,313.70 수지WM 07:23:10` (공백)
# 거래내역서: `1,442Direct7 07:16:04` / `1,424.5Direct7 07:15:41` / `1,384.6증권결제팀 16:44:05`
_FX_LINE = re.compile(
    r"(?P<fx>\d{1,3},\d{3}(?:\.\d+)?|\d{3,4}(?:\.\d+)?)"
    r"\s*"
    r"(?P<branch>Direct\d*|[가-힣A-Za-z][가-힣A-Za-z0-9]*)"
    r"\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)",
    re.I,
)


def _sanitize_fx(raw: str | None) -> float:
    """원/외화 환율만 채택. 수수료·수량·금액과 구분."""
    v = _fx_or_zero(raw)
    if 500.0 <= v <= 4000.0:
        return v
    return 0.0


def _find_fx_in_block(block: list[str]) -> float:
    """블록에서 '환율 처리점 처리시각' 라인만 읽어 환율을 반환. 없으면 0.

    단가·거래금액·외화금액 등 다른 숫자로 환율을 추정·역산하지 않는다.
    """
    for bl in block:
        if re.match(r"^\d{4}/\d{2}/\d{2}\b", bl):
            break
        # 환율 없는 처리점만 있는 줄 (예: `증권결제팀 16:41:34`, `Direct7 07:16:04`)
        if re.match(r"^(?:Direct\d*|[가-힣A-Za-z]+)\s+\d{1,2}:\d{2}", bl, re.I):
            continue
        fm = _FX_LINE.search(bl)
        if fm:
            fx = _sanitize_fx(fm.group("fx"))
            if fx > 0:
                return fx
    return 0.0


def _is_num_token(tok: str) -> bool:
    t = tok.replace(",", "")
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", t))


def _parse_trade_detail(line: str) -> dict[str, Any] | None:
    """매수/매도 상세 라인 토큰 파싱.

    거래내역서 예)
      `1 0 40 247.67 INVESCO NASDAQ 100 67 USD`
      → qty=40, price=247.67, name=INVESCO NASDAQ 100
      (67은 외화유가잔고. 제세금합 칸은 비어 있음)
    """
    toks = line.split()
    if len(toks) < 5 or not toks[0].isdigit():
        return None
    ccy = toks[-1]
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        return None

    if not (_is_num_token(toks[1]) and _is_num_token(toks[2])):
        return None

    if len(toks) > 4 and _is_num_token(toks[3]) and not _is_num_token(toks[4]):
        # 거래번호 원번호 수량 단가 종목명…
        qty, price = _num(toks[2]), _num(toks[3])
        name_start = 4
    else:
        # 거래번호 수량 단가 종목명…
        qty, price = _num(toks[1]), _num(toks[2])
        name_start = 3

    if qty <= 0 or price <= 0:
        return None

    name_toks = toks[name_start : len(toks) - 1]
    # 마지막 정수는 외화유가잔고. 종목명 숫자(NASDAQ 100, S&P 500)는 유지.
    if name_toks and _is_num_token(name_toks[-1]):
        last = _num(name_toks[-1])
        if last == int(last):
            name_toks = name_toks[:-1]
    name = " ".join(name_toks).strip()
    if not name:
        return None
    return {
        "qty": qty,
        "price": price,
        "name": name,
        "tax": 0.0,
        "ccy": ccy,
    }


def _parse_div_detail(line: str, header_gross: float) -> dict[str, Any] | None:
    """배당 상세. 단가 자리 숫자는 환율로 쓰지 않음.

    예) `1 0 1,308.80 JPMORGAN … ETF 52 0 350 USD`
        → name=…, tax=52, net=350, ccy=USD
    """
    toks = line.split()
    if len(toks) < 5:
        return None
    if not toks[0].isdigit():
        return None
    ccy = toks[-1]
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        return None
    # 끝에서 숫자 3개: tax mid net 또는 mid net
    i = len(toks) - 2
    trailing: list[float] = []
    while i >= 1 and _is_num_token(toks[i]) and len(trailing) < 3:
        trailing.insert(0, _num(toks[i]))
        i -= 1
    if len(trailing) < 1:
        return None
    name = " ".join(toks[1 : i + 1]).strip()
    # 앞쪽 숫자(원거래·단가자리) 제거
    name_toks = name.split()
    while name_toks and _is_num_token(name_toks[0]):
        name_toks.pop(0)
    name = " ".join(name_toks).strip()
    if not name:
        name = ""
    if len(trailing) >= 3:
        tax, _mid, net = trailing[0], trailing[1], trailing[2]
    elif len(trailing) == 2:
        tax, net = trailing[0], trailing[1]
    else:
        tax, net = 0.0, trailing[0]
    if net <= 0 and header_gross > 0:
        net = header_gross
    return {"name": name, "tax": tax, "net": net, "ccy": ccy}


def parse_mirae_overseas_pdf(file_bytes: bytes, filename: str = "") -> dict[str, Any]:
    """미래에셋 해외주식 거래내역서 → 표준 행 리스트."""
    notes: list[str] = []
    try:
        text = _extract_text(file_bytes)
    except Exception as exc:  # noqa: BLE001
        return {
            "rows": [],
            "notes": [f"PDF 읽기 실패: {exc}"],
            "source": "mirae-overseas-pdf",
        }

    if not text.strip():
        return {
            "rows": [],
            "notes": ["PDF에서 텍스트를 추출하지 못했습니다."],
            "source": "mirae-overseas-pdf",
        }

    if (
        "미래에셋" not in text
        and "해외주식" not in text
        and "mirae" not in (filename or "").lower()
    ):
        notes.append("미래에셋 해외주식 거래내역서 서식이 아닐 수 있습니다.")

    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _HEADER.match(line)
        if not m:
            i += 1
            continue

        kind = m.group("kind")
        if kind in {"해외주식매수출금", "해외주식매도입금", "배당금외화입금"}:
            i += 1
            continue

        date = m.group("date").replace("/", "-")
        ticker = m.group("ticker").strip()
        n1 = _num(m.group("n1"))
        n2 = _num(m.group("n2"))

        block = [lines[j].strip() for j in range(i + 1, min(i + 8, len(lines)))]
        qty = 0.0
        price_fx = 0.0
        fee_fx = 0.0
        tax_fx = 0.0
        currency = "USD"
        name = ticker
        side = "BUY"

        if kind == "해외주식매수입고":
            side = "BUY"
            fee_fx = n1 if 0 < n1 < 100 else 0.0
            for bl in block:
                if re.match(r"^\d{4}/\d{2}/\d{2}\b", bl):
                    break
                dm = _parse_trade_detail(bl)
                if dm:
                    qty = float(dm["qty"])
                    price_fx = float(dm["price"])
                    name = str(dm["name"] or ticker)
                    currency = str(dm["ccy"])
                    # 제세금합은 원본에서 비어 있음. 종목명 숫자·유가잔고를 세금으로 쓰지 않음.
                    tax_fx = 0.0
                    break
            if qty <= 0 and n2 > 0 and price_fx > 0:
                qty = n2 / price_fx

        elif kind == "해외주식매도출고":
            side = "SELL"
            fee_fx = n1 if 0 < n1 < 100 else 0.0
            for bl in block:
                if re.match(r"^\d{4}/\d{2}/\d{2}\b", bl):
                    break
                dm = _parse_trade_detail(bl)
                if dm:
                    qty = float(dm["qty"])
                    price_fx = float(dm["price"])
                    name = str(dm["name"] or ticker)
                    currency = str(dm["ccy"])
                    break
            if qty <= 0 and n2 > 0 and price_fx > 0:
                qty = n2 / price_fx

        elif kind == "배당금외화입금":
            side = "DIVIDEND"
            gross = n1
            qty = 1.0
            price_fx = gross
            for bl in block:
                if re.match(r"^\d{4}/\d{2}/\d{2}\b", bl):
                    break
                dm = _parse_div_detail(bl, gross)
                if dm:
                    if dm["name"]:
                        name = str(dm["name"])
                    currency = str(dm["ccy"])
                    tax_fx = float(dm["tax"] or 0)
                    net = float(dm["net"] or 0)
                    if net > 0:
                        price_fx = net
                    if net > 0 and gross > net:
                        tax_fx = max(tax_fx, gross - net) if tax_fx <= 0 else tax_fx
                    break

        # ★ 환율: PDF '환율' 컬럼 라인만 사용. 없으면 0 (단가·금액으로 추정 금지)
        fx_rate = _find_fx_in_block(block)

        if qty <= 0 or (side != "DIVIDEND" and price_fx <= 0 and n2 <= 0):
            i += 1
            continue

        if price_fx <= 0 and n2 > 0 and qty > 0:
            price_fx = n2 / qty

        fx_rate = _fx_or_zero(fx_rate)

        rows.append(
            {
                "거래일자": date,
                "거래유형": (
                    "해외매수"
                    if side == "BUY"
                    else ("해외매도" if side == "SELL" else "외화배당")
                ),
                "side": side,
                "종목코드": ticker,
                "종목명": name,
                "수량": qty,
                "외화단가": price_fx,
                "외화수수료": fee_fx,
                "외화제세금": tax_fx,
                "외화거래금액_수치": abs(qty * price_fx) if price_fx > 0 else abs(n2),
                "통화코드": currency,
                "적용환율": fx_rate,
                "메모": f"미래에셋 {kind}",
            }
        )
        i += 1

    zero_fx = sum(1 for r in rows if float(r.get("적용환율") or 0) <= 0)
    notes.append(
        f"미래에셋 해외주식 PDF에서 {len(rows)}건을 추출했습니다."
        + (f" ({filename})" if filename else "")
        + " 배당 행은 증권사 변환기에서 제외했습니다."
    )
    if zero_fx:
        notes.append(
            f"적용환율이 비어 0으로 둔 행 {zero_fx}건 — "
            "단가·거래금액으로 환율을 추정하지 않았습니다. "
            "미리보기에서 적용환율을 직접 입력하면 원화·메모가 재계산됩니다."
        )
    return {"rows": rows, "notes": notes, "source": "mirae-overseas-pdf"}


_OV_PREVIEW_COLS = [
    "거래일자",
    "거래유형",
    "종목코드",
    "종목명",
    "수량",
    "외화단가",
    "외화수수료",
    "외화제세금",
    "통화코드",
    "적용환율",
    "원화단가",
    "원화수수료",
    "원화재세금",
    "외화거래금액",
    "거래금액(원)",
    "증권사",
    "메모",
]

_OV_NUM_COLS = {
    "수량",
    "외화단가",
    "외화수수료",
    "외화제세금",
    "적용환율",
    "원화단가",
    "원화수수료",
    "원화재세금",
    "거래금액(원)",
}


def format_fx_trade_amount(qty: float, price_fx: float, currency: str = "USD") -> str:
    """수량×외화단가 → '5,588.40 USD' 형식."""
    ccy = (currency or "USD").strip().upper() or "USD"
    amt = float(qty or 0) * float(price_fx or 0)
    if abs(amt) < 1e-12:
        return ""
    # 소수 4자리까지 의미 있으면 유지, 아니면 2자리
    if abs(amt - round(amt, 2)) < 1e-9:
        return f"{amt:,.2f} {ccy}"
    return f"{amt:,.4f} {ccy}"


def build_overseas_preview_memo(
    *,
    kind: str,
    ticker: str,
    qty: float,
    price_fx: float,
    fx_rate: float,
    currency: str = "USD",
    broker: str = "",
) -> str:
    """적용환율 반영 미리보기 메모."""
    ccy = (currency or "USD").strip().upper() or "USD"
    label = (ticker or "").strip() or "해외주식"
    house = (broker or "").strip() or "해외주식"
    base = f"{house} {kind}".strip()
    if fx_rate > 0:
        return f"{base} / {label} {qty:g}×{price_fx:g}{ccy}×{fx_rate:,.2f}"
    return f"{base} / {label} {qty:g}×{price_fx:g}{ccy} (환율 미입력→원화 0)"


def ensure_overseas_preview_columns(df):
    """해외주식 변환기 미리보기 컬럼 보장."""
    import pandas as pd

    out = df.copy() if df is not None else pd.DataFrame(columns=_OV_PREVIEW_COLS)
    for c in _OV_PREVIEW_COLS:
        if c not in out.columns:
            out[c] = 0.0 if c in _OV_NUM_COLS else ""
    for c in _OV_NUM_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    # 원본 외화 거래금액(파서 전달)은 표시 컬럼 계산용으로 유지
    keep = list(_OV_PREVIEW_COLS)
    if "외화거래금액_수치" in out.columns:
        out["외화거래금액_수치"] = pd.to_numeric(
            out["외화거래금액_수치"], errors="coerce"
        ).fillna(0.0)
        keep.append("외화거래금액_수치")
    return out[keep]


def apply_overseas_preview_fx(df):
    """적용환율 변경 시 원화 환산·거래금액·메모 재계산.

    - 적용환율 0 → 원화·거래금액 0 (임의 추정 없음)
    - 적용환율 > 0 → 외화×환율
    - 외화거래금액 = 수량 × 외화단가 (표시: '5,588.40 USD')
      원본 `외화거래금액_수치`가 있으면 그 값을 우선
    - 거래금액(원) = 수량 × 외화단가 × 적용환율 (수수료·제세금 제외)
    - 원화수수료·원화재세금은 별도 컬럼으로만 환산
    """
    import pandas as pd

    out = ensure_overseas_preview_columns(df)
    has_raw_amt = "외화거래금액_수치" in out.columns
    for idx in out.index:
        fx = _fx_or_zero(out.at[idx, "적용환율"])
        qty = float(out.at[idx, "수량"] or 0)
        price_fx = float(out.at[idx, "외화단가"] or 0)
        fee_fx = float(out.at[idx, "외화수수료"] or 0)
        tax_fx = float(out.at[idx, "외화제세금"] or 0)
        kind = str(out.at[idx, "거래유형"] or "")
        ticker = str(out.at[idx, "종목코드"] or "")
        ccy = str(out.at[idx, "통화코드"] or "USD")
        broker = str(out.at[idx, "증권사"] or "").strip() if "증권사" in out.columns else ""

        out.at[idx, "적용환율"] = fx
        price_krw = price_fx * fx if fx > 0 else 0.0
        fee_krw = fee_fx * fx if fx > 0 else 0.0
        tax_krw = tax_fx * fx if fx > 0 else 0.0
        out.at[idx, "원화단가"] = float(round(price_krw, 4))
        out.at[idx, "원화수수료"] = float(round(fee_krw, 4))
        out.at[idx, "원화재세금"] = float(round(tax_krw, 4))

        # 외화 총 거래금액 (수수료·제세금 미포함)
        raw_amt = 0.0
        if has_raw_amt:
            raw_amt = float(
                pd.to_numeric(out.at[idx, "외화거래금액_수치"], errors="coerce") or 0
            )
        if raw_amt > 0:
            ccy_u = (ccy or "USD").strip().upper() or "USD"
            if abs(raw_amt - round(raw_amt, 2)) < 1e-9:
                out.at[idx, "외화거래금액"] = f"{raw_amt:,.2f} {ccy_u}"
            else:
                out.at[idx, "외화거래금액"] = f"{raw_amt:,.4f} {ccy_u}"
        else:
            out.at[idx, "외화거래금액"] = format_fx_trade_amount(qty, price_fx, ccy)

        trade_amt_krw = qty * price_krw if fx > 0 else 0.0
        out.at[idx, "거래금액(원)"] = float(round(trade_amt_krw, 0))
        out.at[idx, "메모"] = build_overseas_preview_memo(
            kind=kind,
            ticker=ticker,
            qty=qty,
            price_fx=price_fx,
            fx_rate=fx,
            currency=ccy,
            broker=broker,
        )
    return out


def mirae_rows_to_preview_df(rows: list[dict[str, Any]]):
    import pandas as pd

    if not rows:
        return pd.DataFrame(columns=_OV_PREVIEW_COLS)
    kept = [
        r
        for r in rows
        if "배당" not in str(r.get("거래유형") or "")
        and str(r.get("side") or "").upper() != "DIVIDEND"
    ]
    df = pd.DataFrame(kept) if kept else pd.DataFrame(columns=_OV_PREVIEW_COLS)
    for c in _OV_PREVIEW_COLS:
        if c not in df.columns:
            df[c] = 0.0 if c in _OV_NUM_COLS else ""
    if "증권사" not in df.columns:
        df["증권사"] = "미래에셋증권"
    df["적용환율"] = df["적용환율"].map(_fx_or_zero)
    return apply_overseas_preview_fx(df)
