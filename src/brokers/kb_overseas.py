"""KB증권 해외주식 거래내역 Excel 파서.

지원 양식:
1) 해외주식거래내역 (1행 1건): 결제일자·거래구분·종목코드·기준환율·수수료(국외) …
2) 증권계좌거래내역 (2행 1건): 거래일자·거래종류·국외수수료·환율 …
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

import pandas as pd

from src.models import coerce_fx_rate

# 신규 양식(해외주식거래내역) 시그니처 컬럼
_KB_FLAT_MARKERS = {"결제일자", "거래구분", "기준환율"}
_KB_FLAT_EXTRA = {"수수료(국외)", "외화정산금액", "원화정산금액", "유가잔고수량"}

# 구 양식(증권계좌거래내역 2행) 시그니처
_KB_PAIR_MARKERS = ("거래종류", "국외수수료", "환율")


def _num(value: object) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text.lower() in {"nan", "none", "-", "."}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    return text


def _to_date(value: object) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    text = _text(value).replace(".", "-").replace("/", "-")
    digits = re.sub(r"[^\d]", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return pd.to_datetime(value).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return text[:10]


def _find_col(cols: list[str], aliases: list[str]) -> str | None:
    for alias in aliases:
        for c in cols:
            if alias == c or alias in str(c):
                return c
    return None


def _is_header_pair(row0: list[object], row1: list[object]) -> bool:
    joined = " ".join(_text(x) for x in row0 + row1)
    return "거래일자" in joined and "거래종류" in joined and "종목명" in joined


# 엑셀 종목명이 잘리므로 알려진 약칭 → 티커
_NAME_TICKERS: list[tuple[str, str, str]] = [
    ("NEOS NASDAQ 100 HIGH", "QQQI", "NEOS Nasdaq-100 High Income ETF"),
    ("INVESCO NASDAQ 100", "QQQM", "Invesco NASDAQ 100 ETF"),
    ("JP MORGAN NASDAQ EQU", "JEPQ", "JPMorgan Nasdaq Equity Premium Income ETF"),
    ("JPMORGAN EQUITY PREMIUM", "JEPI", "JPMorgan Equity Premium Income ETF"),
    ("SCHWAB US DIVIDEND", "SCHD", "Schwab US Dividend Equity ETF"),
]


def resolve_ticker(name: str, code: str = "") -> tuple[str, str]:
    """종목코드 우선, 없으면 잘린 종목명 → (종목코드, 표시명)."""
    code = _text(code).upper()
    if code and re.fullmatch(r"[A-Z0-9.\-]{1,12}", code):
        # 거래소 접미사 제거
        ticker = re.sub(r"\.[A-Z]{1,3}$", "", code)
        display = _text(name) or ticker
        return ticker, display

    raw = (name or "").strip()
    if not raw:
        return "", ""
    key = re.sub(r"\s+", " ", raw).upper()
    for prefix, ticker, full in _NAME_TICKERS:
        if key.startswith(prefix) or prefix in key:
            return ticker, full
    slug = re.sub(r"[^A-Z0-9]", "", key)[:12] or "OVERSEAS"
    return slug, raw


def is_kb_flat_overseas_df(df: pd.DataFrame | None) -> bool:
    """KB 해외주식거래내역(1행 1건) 여부 — 결제일자·거래구분·기준환율."""
    if df is None or df.empty:
        return False
    cols = {str(c).strip() for c in df.columns}
    if not _KB_FLAT_MARKERS.issubset(cols):
        return False
    # 메리츠와 구분: 거래단가(외화)/매매금액(외화) 없어야 함
    if "거래단가(외화)" in cols or "매매금액(외화)" in cols:
        return False
    # KB 특유 컬럼 1개 이상
    return bool(cols & _KB_FLAT_EXTRA) or "수수료(국외)" in cols or "종목코드" in cols


def is_kb_pair_overseas_df(df: pd.DataFrame | None) -> bool:
    """KB 증권계좌거래내역(2행 1건) — 헤더/본문에 거래종류·국외수수료·환율."""
    if df is None or df.empty or df.shape[1] < 8:
        return False
    cols = {str(c).strip() for c in df.columns}
    if all(m in cols for m in _KB_PAIR_MARKERS):
        return True
    head = " ".join(
        str(c)
        for c in list(df.columns)
        + list(df.iloc[0])
        + list(df.iloc[1] if len(df) > 1 else [])
    )
    return "거래종류" in head and "국외수수료" in head and "환율" in head


def is_kb_overseas_excel(filename: str = "", df: pd.DataFrame | None = None) -> bool:
    """KB증권 해외주식 엑셀 여부 (신규 flat / 구 pair)."""
    name = filename or ""
    lower = name.lower()
    if "kb" in lower or "kb증권" in name or "증권계좌거래" in name:
        return True
    # 파일명만으로 확정하지 않음 — 메리츠도 '해외주식거래내역' 사용 가능
    if df is not None:
        return is_kb_flat_overseas_df(df) or is_kb_pair_overseas_df(df)
    return False


def map_kb_flat_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """KB flat 양식 컬럼 → 내부 키 매핑."""
    cols = [str(c) for c in df.columns]
    return {
        "date": _find_col(cols, ["결제일자", "거래일자"]),
        "kind": _find_col(cols, ["거래구분", "거래종류"]),
        "code": _find_col(cols, ["종목코드"]),
        "name": _find_col(cols, ["종목명"]),
        "qty": _find_col(cols, ["수량", "거래수량"]),
        "price": _find_col(cols, ["단가", "거래단가"]),
        "amt": _find_col(cols, ["거래금액", "매매금액"]),
        "fee": _find_col(cols, ["수수료(국외)", "국외수수료", "수수료"]),
        "tax": _find_col(cols, ["거래세", "제세금"]),
        "fx": _find_col(cols, ["기준환율", "적용환율", "환율"]),
        "ccy": _find_col(cols, ["통화", "통화코드"]),
        "settle_fx": _find_col(cols, ["외화정산금액"]),
    }


def _classify_side(kind: str) -> str | None:
    """매수/매도만. 배당·원천세·대체·이용료·입출금 등은 None."""
    k = re.sub(r"\s+", "", kind or "")
    if not k:
        return None
    if any(
        tok in k
        for tok in ("배당", "원천세", "대체", "이용료", "환전", "이체", "입금", "출금")
    ):
        return None
    if k == "매수" or k.startswith("매수"):
        return "BUY"
    if k == "매도" or k.startswith("매도"):
        return "SELL"
    return None


def _parse_kb_flat(df: pd.DataFrame, filename: str = "") -> dict[str, Any]:
    """결제일자·거래구분 1행 1건 양식."""
    notes: list[str] = []
    col = map_kb_flat_columns(df)
    if not col["date"] or not col["kind"]:
        return {
            "rows": [],
            "notes": ["KB증권 엑셀에서 결제일자/거래구분 컬럼을 찾지 못했습니다."],
            "source": "kb-overseas-xlsx",
        }

    rows: list[dict[str, Any]] = []
    skipped = 0
    for _, r in df.iterrows():
        kind = _text(r.get(col["kind"]))
        side = _classify_side(kind)
        if side is None:
            skipped += 1
            continue

        qty = abs(_num(r.get(col["qty"])) if col["qty"] else 0.0)
        price = abs(_num(r.get(col["price"])) if col["price"] else 0.0)
        if qty <= 0 or price <= 0:
            skipped += 1
            continue

        fee = abs(_num(r.get(col["fee"])) if col["fee"] else 0.0)
        tax = abs(_num(r.get(col["tax"])) if col["tax"] else 0.0)
        fx = coerce_fx_rate(r.get(col["fx"])) if col["fx"] else 0.0
        ccy = (_text(r.get(col["ccy"])) if col["ccy"] else "") or "USD"
        code_raw = _text(r.get(col["code"])) if col["code"] else ""
        name_raw = _text(r.get(col["name"])) if col["name"] else ""
        ticker, display = resolve_ticker(name_raw, code_raw)
        amt_fx = abs(_num(r.get(col["amt"])) if col["amt"] else 0.0)
        if amt_fx <= 0:
            amt_fx = qty * price

        rows.append(
            {
                "거래일자": _to_date(r.get(col["date"])),
                "거래유형": "해외매수" if side == "BUY" else "해외매도",
                "종목코드": ticker or code_raw or name_raw,
                "종목명": display or name_raw or ticker,
                "수량": qty,
                "외화단가": price,
                "외화수수료": fee,
                "외화제세금": tax,
                "외화거래금액_수치": amt_fx,
                "통화코드": ccy.upper(),
                "적용환율": fx,
                "증권사": "KB증권",
                "메모": f"KB증권 {kind}",
            }
        )

    zero_fx = sum(1 for x in rows if float(x.get("적용환율") or 0) <= 0)
    notes.append(
        f"KB증권 해외주식거래내역에서 해외 매매 {len(rows)}건을 추출했습니다"
        + (f" ({filename})" if filename else "")
        + f". 배당·대체·원천세 등 {skipped}건은 제외했습니다."
    )
    if zero_fx:
        notes.append(
            f"적용환율이 비어 0으로 둔 행 {zero_fx}건 — "
            "미리보기에서 직접 입력하세요."
        )
    return {"rows": rows, "notes": notes, "source": "kb-overseas-xlsx"}


def _parse_kb_pair(raw: pd.DataFrame, filename: str = "") -> dict[str, Any]:
    """증권계좌거래내역 2행 1건 양식."""
    notes: list[str] = []
    start = 0
    for i in range(min(6, len(raw) - 1)):
        if _is_header_pair(list(raw.iloc[i]), list(raw.iloc[i + 1])):
            start = i + 2
            break
    else:
        notes.append("헤더(거래일자·거래종류·종목명)를 찾지 못했습니다. 2행부터 읽습니다.")
        start = 2

    pairs: list[tuple[pd.Series, pd.Series]] = []
    i = start
    while i < len(raw):
        r1 = raw.iloc[i]
        date_txt = _text(r1.iloc[0]) if len(r1) else ""
        kind = _text(r1.iloc[1]) if len(r1) > 1 else ""
        if re.match(r"^\d{4}", date_txt) and kind:
            r2 = raw.iloc[i + 1] if i + 1 < len(raw) else pd.Series(dtype=object)
            pairs.append((r1, r2))
            i += 2
            continue
        i += 1

    rows: list[dict[str, Any]] = []
    skipped = 0

    def _pair_fields(r1: pd.Series, r2: pd.Series) -> dict[str, Any]:
        name = _text(r2.iloc[1] if len(r2) > 1 else "")
        ticker, full_name = resolve_ticker(name)
        ccy = _text(r1.iloc[11] if len(r1) > 11 else "") or "USD"
        if ccy.upper() in {"NAN", ""}:
            ccy = "USD"
        return {
            "date": _to_date(r1.iloc[0]),
            "kind": _text(r1.iloc[1] if len(r1) > 1 else ""),
            "qty": _num(r1.iloc[2] if len(r1) > 2 else 0),
            "price": _num(r2.iloc[2] if len(r2) > 2 else 0),
            "fee_fx": _num(r1.iloc[13] if len(r1) > 13 else 0),
            "fx": coerce_fx_rate(r1.iloc[12] if len(r1) > 12 else 0),
            "ccy": ccy.upper(),
            "name": full_name or name,
            "raw_name": name,
            "ticker": ticker,
        }

    for r1, r2 in pairs:
        f = _pair_fields(r1, r2)
        kind = f["kind"]
        side = _classify_side(kind)
        if side is None:
            skipped += 1
            continue
        if f["qty"] <= 0 or f["price"] <= 0:
            skipped += 1
            continue
        rows.append(
            {
                "거래일자": f["date"],
                "거래유형": "해외매수" if side == "BUY" else "해외매도",
                "종목코드": f["ticker"] or f["raw_name"],
                "종목명": f["name"],
                "수량": f["qty"],
                "외화단가": f["price"],
                "외화수수료": f["fee_fx"],
                "외화제세금": 0.0,
                "외화거래금액_수치": abs(float(f["qty"]) * float(f["price"])),
                "통화코드": f["ccy"],
                "적용환율": f["fx"],
                "증권사": "KB증권",
                "메모": f"KB증권 {kind}",
            }
        )

    zero_fx = sum(1 for x in rows if float(x.get("적용환율") or 0) <= 0)
    notes.append(
        f"KB증권 증권계좌거래내역에서 해외 매매 {len(rows)}건을 추출했습니다"
        + (f" ({filename})" if filename else "")
        + f". 배당·입출금·환전 등 {skipped}건은 제외했습니다."
    )
    if zero_fx:
        notes.append(
            f"적용환율이 비어 0으로 둔 행 {zero_fx}건 — "
            "매수/매도 행에 환율이 없으면 추정하지 않습니다. 미리보기에서 직접 입력하세요."
        )
    return {"rows": rows, "notes": notes, "source": "kb-overseas-xlsx"}


def parse_kb_overseas_excel(file_bytes: bytes, filename: str = "") -> dict[str, Any]:
    """KB증권 해외주식 엑셀 → 해외주식 미리보기 행."""
    # 1) flat 양식 (header=0)
    try:
        flat = pd.read_excel(BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        return {"rows": [], "notes": [f"엑셀 읽기 실패: {exc}"], "source": "kb-overseas-xlsx"}

    if flat is not None and not flat.empty and is_kb_flat_overseas_df(flat):
        return _parse_kb_flat(flat, filename)

    # header 후보 재탐색
    for header in range(0, 5):
        try:
            cand = pd.read_excel(BytesIO(file_bytes), header=header)
        except Exception:  # noqa: BLE001
            continue
        if is_kb_flat_overseas_df(cand):
            return _parse_kb_flat(cand, filename)

    # 2) pair 양식
    try:
        raw = pd.read_excel(BytesIO(file_bytes), header=None)
    except Exception as exc:  # noqa: BLE001
        return {"rows": [], "notes": [f"엑셀 읽기 실패: {exc}"], "source": "kb-overseas-xlsx"}

    if raw is None or raw.empty:
        return {"rows": [], "notes": ["파일이 비어 있습니다."], "source": "kb-overseas-xlsx"}

    return _parse_kb_pair(raw, filename)
