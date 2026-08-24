"""메리츠증권 해외주식/환율 거래내역 Excel 파서."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any

import pandas as pd

from src.models import coerce_fx_rate


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
    text = _text(value).replace(".", "-").replace("/", "-")
    digits = re.sub(r"[^\d]", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _normalize_ticker(code: str, name: str = "") -> tuple[str, str]:
    """SOXL.AX → SOXL. 표시명은 종목명 우선."""
    raw = _text(code).upper()
    # 거래소 접미사 (.AX, .N, .O 등) 제거
    ticker = re.sub(r"\.[A-Z]{1,3}$", "", raw)
    ticker = re.sub(r"[^A-Z0-9]", "", ticker) or raw.replace(".", "")
    display = _text(name) or ticker
    return ticker, display


def _find_col(cols: list[str], aliases: list[str]) -> str | None:
    for alias in aliases:
        for c in cols:
            if alias == c or alias in c:
                return c
    return None


def is_meritz_overseas_excel(filename: str = "", df: pd.DataFrame | None = None) -> bool:
    """메리츠증권 해외주식/환율 거래내역 엑셀 여부."""
    name = filename or ""
    lower = name.lower()
    if "메리츠" in name or "meritz" in lower:
        return True
    if df is None or df.empty:
        return False
    cols = {str(c).strip() for c in df.columns}
    # KB flat(결제일자·수수료(국외))과 구분
    if "결제일자" in cols and "수수료(국외)" in cols:
        return False
    if "수수료(국외)" in cols or "외화정산금액" in cols:
        return False
    # 메리츠 고유: 거래단가(외화) 또는 매매금액(외화) 필수
    markers = {"거래구분", "거래단가(외화)", "기준환율", "종목코드"}
    if markers.issubset(cols):
        return True
    return {"거래구분", "기준환율", "매매금액(외화)"}.issubset(cols) or (
        {"거래구분", "기준환율", "수수료(외화)"}.issubset(cols)
    )


def _classify_side(kind: str) -> str | None:
    """매수/매도만 반환. 환전·이체·배당 등은 None."""
    k = (kind or "").replace(" ", "")
    if not k:
        return None
    # 환전·이체·배당·이용료는 제외 (해외주식 매수/매도/대금만 통과)
    if any(tok in k for tok in ("환전", "이체", "배당", "이용료")):
        return None
    if "해외주식" not in k and "외화주식" not in k:
        return None
    if "매수" in k:
        return "BUY"
    if "매도" in k:
        return "SELL"
    return None


def parse_meritz_overseas_excel(file_bytes: bytes, filename: str = "") -> dict[str, Any]:
    """메리츠 해외주식 거래내역 → 해외주식 미리보기 행.

    - 해외주식 매수/매도(소수점 포함), 매수대금/매도대금 행만 반영
    - 금액·환율이 0인 잔고 갱신 행(대금과 쌍)은 제외 → 이중 등록 방지
    - 환전·이체·배당·이용료는 제외
    """
    notes: list[str] = []
    try:
        raw = pd.read_excel(BytesIO(file_bytes), engine="xlrd")
    except Exception:
        try:
            raw = pd.read_excel(BytesIO(file_bytes))
        except Exception as exc:  # noqa: BLE001
            return {
                "rows": [],
                "notes": [f"엑셀 읽기 실패: {exc}"],
                "source": "meritz-overseas-xls",
            }

    if raw is None or raw.empty:
        return {
            "rows": [],
            "notes": ["파일이 비어 있습니다."],
            "source": "meritz-overseas-xls",
        }

    # 헤더가 1행에 없는 경우 대비
    if not is_meritz_overseas_excel(filename, raw):
        # header=None 후 첫 행을 헤더로
        try:
            probe = pd.read_excel(BytesIO(file_bytes), header=None, engine="xlrd")
        except Exception:
            probe = pd.read_excel(BytesIO(file_bytes), header=None)
        header_idx = None
        for i in range(min(5, len(probe))):
            joined = " ".join(_text(v) for v in probe.iloc[i].tolist())
            if "거래일자" in joined and "거래구분" in joined and "기준환율" in joined:
                header_idx = i
                break
        if header_idx is not None:
            cols = [_text(v) or f"col{j}" for j, v in enumerate(probe.iloc[header_idx])]
            raw = probe.iloc[header_idx + 1 :].copy()
            raw.columns = cols
            raw = raw.reset_index(drop=True)

    cols = [str(c) for c in raw.columns]
    col_date = _find_col(cols, ["거래일자"])
    col_kind = _find_col(cols, ["거래구분"])
    col_code = _find_col(cols, ["종목코드"])
    col_name = _find_col(cols, ["종목명"])
    col_ccy = _find_col(cols, ["통화"])
    col_qty = _find_col(cols, ["거래수량"])
    col_price = _find_col(cols, ["거래단가(외화)", "외화단가"])
    col_amt = _find_col(cols, ["매매금액(외화)"])
    col_fee = _find_col(cols, ["수수료(외화)"])
    col_tax = _find_col(cols, ["제비용(외화)", "제세금(외화)"])
    col_fx = _find_col(cols, ["기준환율", "적용환율", "환율"])

    if not col_date or not col_kind:
        return {
            "rows": [],
            "notes": ["메리츠 엑셀에서 거래일자/거래구분 컬럼을 찾지 못했습니다."],
            "source": "meritz-overseas-xls",
        }

    rows: list[dict[str, Any]] = []
    skipped = 0
    skipped_balance = 0

    for _, r in raw.iterrows():
        kind = _text(r.get(col_kind))
        side = _classify_side(kind)
        if side is None:
            skipped += 1
            continue

        qty = _num(r.get(col_qty)) if col_qty else 0.0
        price = _num(r.get(col_price)) if col_price else 0.0
        amt = _num(r.get(col_amt)) if col_amt else 0.0
        fx = coerce_fx_rate(r.get(col_fx)) if col_fx else 0.0
        fee = _num(r.get(col_fee)) if col_fee else 0.0
        tax = _num(r.get(col_tax)) if col_tax else 0.0
        code = _text(r.get(col_code)) if col_code else ""
        name = _text(r.get(col_name)) if col_name else ""
        ccy = (_text(r.get(col_ccy)) if col_ccy else "") or "USD"
        date = _to_date(r.get(col_date))

        # 대금과 쌍을 이루는 잔고 갱신 행: 금액·환율 0 → 제외
        if qty <= 0 or price <= 0:
            skipped += 1
            continue
        if amt <= 0 and fx <= 0:
            skipped_balance += 1
            continue
        if not code and not name:
            skipped += 1
            continue

        ticker, display = _normalize_ticker(code, name)
        if not ticker:
            skipped += 1
            continue

        rows.append(
            {
                "거래일자": date,
                "거래유형": "해외매수" if side == "BUY" else "해외매도",
                "종목코드": ticker,
                "종목명": display,
                "수량": qty,
                "외화단가": price,
                "외화수수료": fee,
                "외화제세금": tax,
                "외화거래금액_수치": amt if amt > 0 else qty * price,
                "통화코드": ccy.upper(),
                "적용환율": fx,
                "증권사": "메리츠증권",
                "메모": f"메리츠 {kind}",
            }
        )

    zero_fx = sum(1 for row in rows if float(row.get("적용환율") or 0) <= 0)
    buy_n = sum(1 for row in rows if row["거래유형"] == "해외매수")
    sell_n = sum(1 for row in rows if row["거래유형"] == "해외매도")
    notes.append(
        f"메리츠증권 해외주식 거래내역에서 매매 {len(rows)}건을 추출했습니다 "
        f"(매수 {buy_n} · 매도 {sell_n})"
        + (f" ({filename})" if filename else "")
        + f". 환전·이체·배당 등 {skipped}건"
        + (f", 잔고갱신(금액0) {skipped_balance}건" if skipped_balance else "")
        + "은 제외했습니다."
    )
    if zero_fx:
        notes.append(
            f"적용환율이 비어 0으로 둔 행 {zero_fx}건 — "
            "미리보기에서 적용환율을 입력하면 원화가 재계산됩니다."
        )
    return {"rows": rows, "notes": notes, "source": "meritz-overseas-xls"}
