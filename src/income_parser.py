"""원천징수영수증(PDF/Excel) → 이자·배당 내역 파싱."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import pandas as pd

from .import_export import read_tabular


@dataclass
class IncomeParseResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    source: str = "excel"


def empty_income_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "지급일",
            "금융상품명",
            "증권사",
            "소득구분",
            "지급액",
            "법인세",
            "지방소득세",
            "메모",
        ]
    )


def _to_number(value: Any) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return 0.0
    text = text.replace(",", "").replace("원", "").replace(" ", "")
    text = re.sub(r"[^\d.\-]", "", text)
    if not text or text in {".", "-", "-."}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _to_date(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return ""
    # Excel serial
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if 20000 < float(value) < 60000:
                return pd.to_datetime(float(value), unit="D", origin="1899-12-30").strftime(
                    "%Y-%m-%d"
                )
    except Exception:  # noqa: BLE001
        pass
    digits = re.sub(r"[^\d]", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        return pd.to_datetime(text).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return text[:10]


def _income_type(value: Any) -> str:
    text = str(value or "").strip()
    if "배당" in text or text.upper() in {"DIVIDEND", "D"}:
        return "DIVIDEND"
    return "INTEREST"


def _find_col(columns: list[str], candidates: list[str]) -> str | None:
    normalized = {str(c).strip(): c for c in columns}
    lower_map = {str(c).strip().lower().replace(" ", ""): c for c in columns}
    for cand in candidates:
        if cand in normalized:
            return normalized[cand]
        key = cand.lower().replace(" ", "")
        if key in lower_map:
            return lower_map[key]
    for col in columns:
        cl = str(col).lower().replace(" ", "")
        for cand in candidates:
            if cand.lower().replace(" ", "") in cl:
                return col
    return None


def parse_income_excel(file_bytes: bytes, filename: str = "") -> IncomeParseResult:
    """표준/유사 컬럼 Excel·CSV 파싱."""
    notes: list[str] = []
    try:
        df = read_tabular(file_bytes, filename or "income.xlsx")
    except Exception as exc:  # noqa: BLE001
        return IncomeParseResult(notes=[f"엑셀 읽기 실패: {exc}"], source="excel")

    if df is None or df.empty:
        return IncomeParseResult(notes=["파일이 비어 있습니다."], source="excel")

    cols = [str(c) for c in df.columns]
    col_date = _find_col(cols, ["지급일", "지급일자", "소득귀속일", "일자", "날짜", "pay_date"])
    col_amt = _find_col(
        cols, ["지급액", "소득금액", "수입금액", "이자", "배당금", "gross", "금액"]
    )
    col_corp = _find_col(cols, ["법인세", "원천징수법인세", "법인세액", "corp_tax"])
    col_local = _find_col(
        cols, ["지방소득세", "지방세", "원천징수지방소득세", "local_tax"]
    )
    col_product = _find_col(
        cols, ["금융상품명", "상품명", "종목명", "적요", "product_name", "내용"]
    )
    col_broker = _find_col(
        cols, ["증권사", "금융기관", "지급기관", "은행", "broker", "거래처"]
    )
    col_type = _find_col(cols, ["소득구분", "구분", "유형", "income_type"])
    col_memo = _find_col(cols, ["메모", "비고", "memo"])

    if not col_date or not col_amt:
        notes.append(
            "필수 컬럼(지급일, 지급액)을 찾지 못했습니다. "
            "헤더 예: 지급일, 지급액, 법인세, 지방소득세, 금융상품명, 증권사"
        )
        return IncomeParseResult(notes=notes, source="excel")

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        pay_date = _to_date(r.get(col_date))
        gross = _to_number(r.get(col_amt))
        if not pay_date and gross <= 0:
            continue
        rows.append(
            {
                "지급일": pay_date,
                "금융상품명": str(r.get(col_product) or "").strip() if col_product else "",
                "증권사": str(r.get(col_broker) or "").strip() if col_broker else "",
                "소득구분": (
                    "배당" if _income_type(r.get(col_type) if col_type else "") == "DIVIDEND" else "이자"
                ),
                "지급액": gross,
                "법인세": _to_number(r.get(col_corp)) if col_corp else 0.0,
                "지방소득세": _to_number(r.get(col_local)) if col_local else 0.0,
                "메모": str(r.get(col_memo) or "").strip() if col_memo else "",
            }
        )

    if not rows:
        notes.append("유효한 데이터 행이 없습니다.")
    else:
        notes.append(f"엑셀에서 {len(rows)}건을 인식했습니다.")
    return IncomeParseResult(rows=rows, notes=notes, source="excel")


def _extract_pdf_text(file_bytes: bytes, *, include_tables: bool = True) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
            if not include_tables:
                continue
            # 표도 시도
            for table in page.extract_tables() or []:
                for row in table:
                    cells = [str(c or "").strip() for c in row]
                    if any(cells):
                        parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pick_amount_near(text: str, labels: list[str]) -> float:
    for label in labels:
        patterns = [
            rf"{re.escape(label)}\s*[:：]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
            rf"{re.escape(label)}\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*원?",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return _to_number(m.group(1))
    return 0.0


def _pick_date(text: str) -> str:
    patterns = [
        r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            return f"{y}-{mo:02d}-{d:02d}"
    return ""


def _pick_broker(text: str) -> str:
    brokers = [
        "KB증권",
        "미래에셋증권",
        "삼성증권",
        "NH투자증권",
        "한국투자증권",
        "키움증권",
        "신한투자증권",
        "대신증권",
        "유안타증권",
        "하나증권",
        "교보증권",
        "메리츠증권",
        "토스증권",
        "카카오페이증권",
    ]
    for name in brokers:
        if name in text:
            return name
    m = re.search(r"([가-힣A-Za-z]+증권)", text)
    if m:
        return m.group(1)
    return ""


# KB증권 계좌별 과세내역/원천징수영수증 거래 라인
# 예) 2025/01/06 2025/01 C 이 NN A10 D1200507Q192 00 2024/11/26~2025/01/06 2.82 54,387 14.00 7,610 760 8,370
# 예) 2025/01/06 2025/01 W 이 A10 D1200507Q192 00 2024/12/05~2025/01/06 2.78 1,898 0.00
_KB_TX_LINE = re.compile(
    r"(?P<pay>\d{4}/\d{2}/\d{2})\s+"
    r"(?P<ym>\d{4}/\d{2})\s+"
    r"(?P<tax>[CW])\s+"
    r"(?P<itype>[이배])\s+"
    r"(?:NN\s+)?"
    r"(?P<code>\S+)\s+"
    r"(?P<std>\S+)\s+"
    r"(?P<bond>\S+)\s+"
    r"(?P<period>\d{4}/\d{2}/\d{2}\s*[~～\-]\s*\d{4}/\d{2}/\d{2})\s+"
    r"(?P<rate>\d+(?:\.\d+)?)\s+"
    r"(?P<amount>[\d,]+)\s+"
    r"(?P<taxrate>\d+(?:\.\d+)?)"
    r"(?:\s+(?P<corp>[\d,]+)\s+(?P<local>[\d,]+)(?:\s+(?P<total>[\d,]+))?)?",
    re.MULTILINE,
)

_KB_MARKERS = (
    "KB증권",
    "계좌별과세",
    "이자·배당소득 원천징수",
    "이자·배당소득 지 급 명 세 서",
    "과세 구분",
)


def _is_kb_withholding_pdf(text: str, filename: str = "") -> bool:
    name = (filename or "").lower()
    if "kb" in name and ("과세" in (filename or "") or "원천" in (filename or "") or "cma" in name):
        return True
    hits = sum(1 for m in _KB_MARKERS if m in text)
    if hits >= 2:
        return True
    # 본문 라인 패턴이 충분히 보이면 KB 서식으로 간주
    return len(_KB_TX_LINE.findall(text)) >= 3


def _normalize_kb_pdf_text(text: str) -> str:
    """줄바꿈으로 끊긴 거래 라인을 한 줄로 복구하고 공백을 정리."""
    # 날짜~날짜 사이 공백/줄바꿈 보정
    text = re.sub(
        r"(\d{4}/\d{2}/\d{2})\s*[~\n～\-]+\s*(\d{4}/\d{2}/\d{2})",
        r"\1~\2",
        text,
    )
    lines = text.splitlines()
    merged: list[str] = []
    buf = ""
    date_start = re.compile(r"^\d{4}/\d{2}/\d{2}\b")
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if date_start.match(line):
            if buf:
                merged.append(buf)
            buf = line
        elif buf and (
            re.search(r"[CW]\s+[이배]", buf)
            or re.search(r"\d{4}/\d{2}/\d{2}", buf)
        ):
            # 거래 라인 후속 조각(이자율·금액 등) 이어붙이기
            if not date_start.match(line) and (
                re.search(r"\d", line) or line in {"이", "배", "C", "W", "NN"}
            ):
                buf = f"{buf} {line}"
            else:
                merged.append(buf)
                buf = ""
                merged.append(line)
        else:
            if buf:
                merged.append(buf)
                buf = ""
            merged.append(line)
    if buf:
        merged.append(buf)
    return "\n".join(merged)


def _kb_row_from_match(m: re.Match[str], filename: str = "") -> dict[str, Any]:
    pay = m.group("pay").replace("/", "-")
    itype = "배당" if m.group("itype") == "배" else "이자"
    amount = _to_number(m.group("amount"))
    corp = _to_number(m.group("corp")) if m.group("corp") else 0.0
    local = _to_number(m.group("local")) if m.group("local") else 0.0
    tax = m.group("tax")
    period = (m.group("period") or "").replace(" ", "")
    rate = m.group("rate")
    memo_bits = [
        "KB증권 CMA",
        f"과세구분={tax}",
        f"소득기간={period}",
        f"이자율={rate}%",
    ]
    if filename:
        memo_bits.append(filename)
    return {
        "지급일": pay,
        "금융상품명": "KB증권 CMA",
        "증권사": "KB증권",
        "소득구분": itype,
        "지급액": amount,
        "법인세": corp,
        "지방소득세": local,
        "메모": " / ".join(memo_bits),
    }


def parse_kb_interest_pdf(file_bytes: bytes, filename: str = "") -> IncomeParseResult:
    """KB증권 CMA 계좌별 과세내역/원천징수영수증 PDF → 전체 거래 라인 추출."""
    notes: list[str] = []
    try:
        # 표 셀( | 구분)을 섞으면 라인 정규식이 깨질 수 있어 본문 텍스트만 사용
        raw = _extract_pdf_text(file_bytes, include_tables=False)
    except Exception as exc:  # noqa: BLE001
        return IncomeParseResult(
            notes=[f"PDF 읽기 실패: {exc}"],
            source="pdf-kb",
        )

    if not raw.strip():
        return IncomeParseResult(
            notes=["PDF에서 텍스트를 추출하지 못했습니다. (스캔본이면 OCR 필요)"],
            source="pdf-kb",
        )

    text = _normalize_kb_pdf_text(raw)
    rows: list[dict[str, Any]] = []
    for m in _KB_TX_LINE.finditer(text):
        # PDF 하단 합계(지급액·세액)와 일치하도록 동일 내용 라인도 모두 유지
        # (페이지 넘어가며 보이는 동일 행·같은 날 동일 금액 다건 등)
        rows.append(_kb_row_from_match(m, filename=filename))

    if not rows:
        return IncomeParseResult(
            notes=[
                "KB증권 과세내역 거래 라인을 찾지 못했습니다. "
                "일반 PDF 파서로 재시도합니다."
            ],
            source="pdf-kb",
        )

    gross_sum = sum(float(r["지급액"]) for r in rows)
    corp_sum = sum(float(r["법인세"]) for r in rows)
    local_sum = sum(float(r["지방소득세"]) for r in rows)
    notes.append(
        f"KB증권 계좌별 과세내역에서 {len(rows)}건을 추출했습니다. "
        f"(지급액 합계 {gross_sum:,.0f} / 법인세 {corp_sum:,.0f} / 지방소득세 {local_sum:,.0f})"
    )
    zero_amt = sum(1 for r in rows if float(r["지급액"]) <= 0)
    if zero_amt:
        notes.append(f"지급액 0원 행 {zero_amt}건 — 금액 컬럼을 확인해 주세요.")
    return IncomeParseResult(rows=rows, notes=notes, source="pdf-kb")


def parse_income_pdf(file_bytes: bytes, filename: str = "") -> IncomeParseResult:
    """원천징수영수증 PDF 파싱. KB 계좌별과세내역은 전용 파서 우선."""
    notes: list[str] = []
    try:
        text = _extract_pdf_text(file_bytes)
    except Exception as exc:  # noqa: BLE001
        return IncomeParseResult(
            notes=[f"PDF 읽기 실패: {exc}", "엑셀 양식으로 업로드하거나 수동 입력해 주세요."],
            source="pdf",
        )

    if not text.strip():
        return IncomeParseResult(
            notes=["PDF에서 텍스트를 추출하지 못했습니다. (스캔본이면 OCR 필요)"],
            source="pdf",
        )

    if _is_kb_withholding_pdf(text, filename):
        kb = parse_kb_interest_pdf(file_bytes, filename)
        if kb.rows:
            return kb
        notes.extend(kb.notes)

    pay_date = _pick_date(text)
    gross = _pick_amount_near(
        text,
        ["지급액", "소득금액", "수입금액", "이자소득", "배당소득", "총지급액"],
    )
    corp = _pick_amount_near(text, ["법인세", "원천징수법인세", "법인세액"])
    local = _pick_amount_near(text, ["지방소득세", "지방세", "원천징수지방소득세"])
    broker = _pick_broker(text)
    product = ""
    for label in ("금융상품명", "상품명", "종목명", "계좌명"):
        m = re.search(rf"{label}\s*[:：]?\s*(.+)", text)
        if m:
            product = m.group(1).split("\n")[0].strip()[:80]
            break
    if not product:
        product = "이자소득" if "배당" not in text else "배당소득"

    itype = "배당" if ("배당" in text and "이자" not in text[:200]) or "배당소득" in text else "이자"

    if gross <= 0:
        notes.append(
            "PDF에서 지급액을 자동으로 찾지 못했습니다. "
            "아래 표를 직접 수정하거나 표준 엑셀로 올려 주세요."
        )
        rows = [
            {
                "지급일": pay_date or "",
                "금융상품명": product,
                "증권사": broker,
                "소득구분": itype,
                "지급액": 0.0,
                "법인세": corp,
                "지방소득세": local,
                "메모": filename,
            }
        ]
        return IncomeParseResult(rows=rows, notes=notes, source="pdf")

    rows = [
        {
            "지급일": pay_date,
            "금융상품명": product,
            "증권사": broker,
            "소득구분": itype,
            "지급액": gross,
            "법인세": corp,
            "지방소득세": local,
            "메모": filename,
        }
    ]
    notes.append(f"PDF에서 {len(rows)}건을 추정 추출했습니다. 금액·날짜를 확인해 주세요.")
    return IncomeParseResult(rows=rows, notes=notes, source="pdf")


def parse_income_file(file_bytes: bytes, filename: str) -> IncomeParseResult:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return parse_income_pdf(file_bytes, filename)
    return parse_income_excel(file_bytes, filename)


def rows_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return empty_income_frame()
    df = pd.DataFrame(rows)
    for col in empty_income_frame().columns:
        if col not in df.columns:
            df[col] = "" if col not in {"지급액", "법인세", "지방소득세"} else 0.0
    return df[list(empty_income_frame().columns)]
