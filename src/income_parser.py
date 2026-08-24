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
            "종목코드",
            "통화",
            "외화지급액",
            "외화원천세",
            "환율",
            "지급액",
            "법인세",
            "지방소득세",
            "메모",
        ]
    )


_INCOME_NUM_COLS = {
    "외화지급액",
    "외화원천세",
    "환율",
    "지급액",
    "법인세",
    "지방소득세",
}


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


def _cell_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return ""
    return text


def _is_total_row(*values: Any) -> bool:
    joined = " ".join(_cell_text(v) for v in values)
    return "합계" in joined or joined.startswith("**")


def _is_kb_tax_excel(df: pd.DataFrame, filename: str = "") -> bool:
    """KB증권 증권계좌 과세내역조회(원천징수영수증) 엑셀 여부."""
    cols = {str(c).strip() for c in df.columns}
    if {"원거래일자", "과세표준"} <= cols:
        return True
    if {"소득구분코드", "적요명", "과세표준"} <= cols:
        return True
    name = filename or ""
    if "과세내역" in name or "원천징수" in name:
        return "KB" in name.upper() or "과세표준" in cols
    return False


def _kb_product_name(ticker: str, stock_name: str, remark: str) -> str:
    ticker = ticker.strip()
    stock_name = stock_name.strip()
    remark = remark.strip()
    if "환급" in remark:
        base = ticker or stock_name or "해외원천세"
        return f"{base} {remark}".strip()
    if stock_name:
        return f"{ticker} {stock_name}".strip() if ticker and ticker not in stock_name else stock_name
    return remark or ticker or "이자·배당"


def _drop_zero_net_kb_groups(
    items: list[tuple[str, str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    """종목+지급대상기간 합이 0인 조정·환급 묶음은 전표에서 제외."""
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for ticker, period, row in items:
        key = (ticker, period)
        if key not in buckets:
            order.append(key)
        buckets[key].append(row)

    kept: list[dict[str, Any]] = []
    skipped = 0
    for key in order:
        group = buckets[key]
        total = sum(float(r["지급액"]) for r in group)
        if len(group) >= 2 and abs(total) < 1:
            skipped += len(group)
            continue
        kept.extend(group)
    return kept, skipped


def parse_kb_tax_excel(df: pd.DataFrame, filename: str = "") -> IncomeParseResult:
    """KB증권 과세내역조회 엑셀 → 이자·배당 내역.

    헤더: 원거래일자(지급일), 과세표준(지급액), 소득법인세, 주민세,
    종목코드/종목명/적요명, 소득구분코드.
    """
    notes: list[str] = []
    cols = [str(c) for c in df.columns]
    col_date = _find_col(cols, ["원거래일자", "지급일", "지급일자", "거래일자"])
    col_amt = _find_col(cols, ["과세표준", "지급액", "소득금액"])
    col_corp = _find_col(cols, ["소득법인세", "법인세", "원천징수법인세"])
    col_local = _find_col(cols, ["주민세", "지방소득세", "지방세"])
    col_ticker = _find_col(cols, ["종목코드"])
    col_name = _find_col(cols, ["종목명"])
    col_remark = _find_col(cols, ["적요명", "적요"])
    col_type = _find_col(cols, ["소득구분코드", "소득구분"])
    col_period = _find_col(cols, ["지급대상기간"])
    col_ym = _find_col(cols, ["귀속년월"])

    if not col_date or not col_amt:
        return IncomeParseResult(
            notes=[
                "KB 과세내역 엑셀에서 원거래일자/과세표준 컬럼을 찾지 못했습니다."
            ],
            source="excel-kb",
        )

    pending: list[tuple[str, str, dict[str, Any]]] = []
    skipped_total = 0
    skipped_zero = 0
    for _, r in df.iterrows():
        type_text = _cell_text(r.get(col_type)) if col_type else ""
        remark = _cell_text(r.get(col_remark)) if col_remark else ""
        ticker = _cell_text(r.get(col_ticker)) if col_ticker else ""
        stock_name = _cell_text(r.get(col_name)) if col_name else ""
        if _is_total_row(type_text, remark, stock_name):
            skipped_total += 1
            continue
        pay_date = _to_date(r.get(col_date))
        gross = _to_number(r.get(col_amt))
        if not pay_date:
            skipped_zero += 1
            continue
        if gross == 0:
            skipped_zero += 1
            continue
        itype = "배당" if _income_type(type_text or remark) == "DIVIDEND" else "이자"
        product = _kb_product_name(ticker, stock_name, remark)
        period = _cell_text(r.get(col_period)) if col_period else ""
        ym = _cell_text(r.get(col_ym)) if col_ym else ""
        if ym.endswith(".0"):
            ym = ym[:-2]
        memo_bits = ["KB증권 과세내역"]
        if remark:
            memo_bits.append(remark)
        if period:
            memo_bits.append(f"지급대상기간={period}")
        if ym:
            memo_bits.append(f"귀속년월={ym}")
        if filename:
            memo_bits.append(filename)
        pending.append(
            (
                ticker,
                period,
                {
                    "지급일": pay_date,
                    "금융상품명": product,
                    "증권사": "KB증권",
                    "소득구분": itype,
                    "종목코드": ticker,
                    "통화": "KRW",
                    "외화지급액": 0.0,
                    "외화원천세": 0.0,
                    "환율": 0.0,
                    "지급액": gross,
                    "법인세": _to_number(r.get(col_corp)) if col_corp else 0.0,
                    "지방소득세": _to_number(r.get(col_local)) if col_local else 0.0,
                    "메모": " / ".join(memo_bits),
                },
            )
        )

    rows, skipped_offset = _drop_zero_net_kb_groups(pending)
    negative_left = [r for r in rows if float(r["지급액"]) < 0]
    if negative_left:
        rows = [r for r in rows if float(r["지급액"]) > 0]

    if not rows:
        notes.append("KB 과세내역에서 유효한 행이 없습니다.")
        return IncomeParseResult(notes=notes, source="excel-kb")

    gross_sum = sum(float(r["지급액"]) for r in rows)
    div_n = sum(1 for r in rows if r["소득구분"] == "배당")
    int_n = sum(1 for r in rows if r["소득구분"] == "이자")
    notes.append(
        f"KB증권 과세내역조회에서 {len(rows)}건을 추출했습니다 "
        f"(배당 {div_n} · 이자 {int_n}, 과세표준 합계 {gross_sum:,.0f}원). "
        "지급일=원거래일자, 지급액=과세표준 입니다."
    )
    if skipped_offset:
        notes.append(
            f"음수 과세표준과 해외원천세 환급이 상쇄되는 조정 묶음 {skipped_offset}건은 "
            "전표 금액이 왜곡되지 않도록 제외했습니다."
        )
    if negative_left:
        notes.append(f"상쇄되지 않은 음수 과세표준 {len(negative_left)}건은 제외했습니다.")
    if skipped_total:
        notes.append(f"합계 행 {skipped_total}건은 제외했습니다.")
    return IncomeParseResult(rows=rows, notes=notes, source="excel-kb")


def parse_income_excel(file_bytes: bytes, filename: str = "") -> IncomeParseResult:
    """표준/유사 컬럼 Excel·CSV 파싱. KB 과세내역조회 서식은 전용 파서 우선."""
    notes: list[str] = []
    try:
        df = read_tabular(file_bytes, filename or "income.xlsx")
    except Exception as exc:  # noqa: BLE001
        return IncomeParseResult(notes=[f"엑셀 읽기 실패: {exc}"], source="excel")

    if df is None or df.empty:
        return IncomeParseResult(notes=["파일이 비어 있습니다."], source="excel")

    if _is_kb_tax_excel(df, filename):
        return parse_kb_tax_excel(df, filename)

    cols = [str(c) for c in df.columns]
    col_date = _find_col(
        cols,
        ["지급일", "지급일자", "원거래일자", "소득귀속일", "거래일자", "일자", "날짜", "pay_date"],
    )
    col_amt = _find_col(
        cols,
        ["지급액", "과세표준", "소득금액", "수입금액", "배당금", "gross", "금액"],
    )
    col_corp = _find_col(
        cols, ["소득법인세", "법인세", "원천징수법인세", "법인세액", "corp_tax"]
    )
    col_local = _find_col(
        cols, ["지방소득세", "지방세", "주민세", "원천징수지방소득세", "local_tax"]
    )
    col_product = _find_col(
        cols, ["금융상품명", "상품명", "종목명", "적요명", "적요", "product_name", "내용"]
    )
    col_broker = _find_col(
        cols, ["증권사", "금융기관", "지급기관", "은행", "broker", "거래처"]
    )
    col_type = _find_col(cols, ["소득구분코드", "소득구분", "구분", "유형", "income_type"])
    col_memo = _find_col(cols, ["메모", "비고", "memo"])

    if not col_date or not col_amt:
        notes.append(
            "필수 컬럼(지급일, 지급액)을 찾지 못했습니다. "
            "헤더 예: 지급일, 지급액, 법인세, 지방소득세, 금융상품명, 증권사 "
            "(KB 과세내역은 원거래일자·과세표준)"
        )
        return IncomeParseResult(notes=notes, source="excel")

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        type_text = str(r.get(col_type) or "") if col_type else ""
        product_raw = str(r.get(col_product) or "") if col_product else ""
        if _is_total_row(type_text, product_raw):
            continue
        pay_date = _to_date(r.get(col_date))
        gross = _to_number(r.get(col_amt))
        if not pay_date:
            continue
        rows.append(
            {
                "지급일": pay_date,
                "금융상품명": product_raw.strip() if col_product else "",
                "증권사": str(r.get(col_broker) or "").strip() if col_broker else "",
                "소득구분": (
                    "배당" if _income_type(type_text) == "DIVIDEND" else "이자"
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


_MIRAE_CERT_MARKERS = (
    "거래실적 증명서",
    "거래실적증명서",
    "배당금외화입금",
    "예탁금이용료입금",
)

_MIRAE_DIV_HEADER = re.compile(
    r"(?P<date>\d{4}/\d{2}/\d{2})\s+배당금외화입금\s+(?P<ticker>\S+)"
    r"(?:\s+(?P<n1>[\d,\.]+))?"
    r"(?:\s+(?P<n2>[\d,\.]+))?"
)
_MIRAE_DIV_DETAIL = re.compile(
    r"^\d+\s+\d+\s+(?P<field>[\d,\.]+)\s+(?P<name>.+?)\s+"
    r"(?P<tax>[\d,\.]+)\s+(?P<mid>[\d,\.]+)\s+(?P<net>[\d,\.]+)\s+(?P<ccy>[A-Z]{3})\s*$"
)
_MIRAE_KRW_FEE = re.compile(
    r"(?P<date>\d{4}/\d{2}/\d{2})\s+예탁금이용료입금\s+(?P<amt>[\d,\.]+)"
)
_MIRAE_FX_FEE = re.compile(
    r"(?P<date>\d{4}/\d{2}/\d{2})\s+외화예탁금이용료입금\s+(?P<code>\S+)"
)
# 환율 컬럼: `1,313.70 수지WM 07:23:10` / `1,442Direct7 07:16:04`
_MIRAE_FX_LINE = re.compile(
    r"(?P<fx>\d{1,3},\d{3}(?:\.\d+)?|\d{3,4}(?:\.\d+)?)"
    r"\s*"
    r"(?P<branch>Direct\d*|[가-힣A-Za-z][가-힣A-Za-z0-9]*)"
    r"\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)",
    re.I,
)
_MIRAE_NAME_INDEX_NUMS = {100.0, 200.0, 500.0, 600.0}
_MIRAE_WRAP_SKIP = re.compile(
    r"거래내역서|계좌정보|페이지:|계좌번호|거래일자|CMARP|고객명"
)


def _mirae_is_num_tok(tok: str) -> bool:
    t = tok.replace(",", "")
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", t))


def _mirae_looks_like_fx(value: float) -> bool:
    return 500.0 <= value <= 4000.0


def _parse_mirae_div_detail(line: str) -> dict[str, Any] | None:
    """거래내역서 배당 상세.

    `1 0 1,434.9 INVESCO QQQ TRUST 14.16 80.34 USD`
      → fx=1434.9, name=…, tax=14.16, net=80.34
    `1 0 1,442.1 1.1 6.22 USD` (종목명은 위/아래 줄)
    `1 0 1,464.1 0.01 USD`
    단가 칸(1,434.9)이 환율이다. 처리점 줄에는 환율이 없다.
    """
    toks = line.split()
    if len(toks) < 4 or not toks[0].isdigit():
        return None
    ccy = toks[-1]
    if not re.fullmatch(r"[A-Z]{3}", ccy):
        return None
    if not _mirae_is_num_tok(toks[1]):
        return None
    idx = 2
    fx = 0.0
    if idx < len(toks) - 1 and _mirae_is_num_tok(toks[idx]):
        cand = _to_number(toks[idx])
        if _mirae_looks_like_fx(cand):
            fx = cand
            idx += 1
    rest = toks[idx:-1]
    money: list[float] = []
    while rest and _mirae_is_num_tok(rest[-1]) and len(money) < 2:
        token = rest[-1]
        val = _to_number(token)
        if "." not in token.replace(",", "") and val in _MIRAE_NAME_INDEX_NUMS:
            break
        money.insert(0, val)
        rest.pop()
    name = " ".join(rest).strip()
    if len(money) >= 2:
        tax, net = money[0], money[-1]
    elif len(money) == 1:
        tax, net = 0.0, money[0]
    else:
        tax, net = 0.0, 0.0
    if fx <= 0 and tax <= 0 and net <= 0 and not name:
        return None
    return {"fx": fx, "name": name, "tax": tax, "net": net, "ccy": ccy}


def _mirae_find_fx_in_lines(lines: list[str], start: int, end: int) -> float:
    """환율 컬럼 라인만 읽음. 단가·금액 숫자로 추정하지 않음."""
    from .models import coerce_fx_rate

    for j in range(start, end):
        nxt = lines[j]
        if re.match(r"^\d{4}/\d{2}/\d{2}\b", nxt):
            break
        if re.match(r"^(?:Direct\d*|[가-힣A-Za-z]+)\s+\d{1,2}:\d{2}", nxt, re.I):
            continue
        fm = _MIRAE_FX_LINE.search(nxt)
        if fm:
            v = coerce_fx_rate(fm.group("fx"))
            if 500.0 <= v <= 4000.0:
                return v
    return 0.0


def _is_mirae_trade_certificate(text: str, filename: str = "") -> bool:
    name = filename or ""
    if "거래실적" in name and ("미래에셋" in name or "mirae" in name.lower()):
        return True
    if "미래에셋" not in text and "mirae" not in name.lower():
        return False
    return sum(1 for m in _MIRAE_CERT_MARKERS if m in text or m in name) >= 2


# 미래에셋 이자·배당소득 원천징수영수증(별지 제23호서식)
# 예) QQQ 2026/01/01~2026/01/02 2026/01 W 56 B53 0.00 135,598 0.00 0 0 0 0 0
# 예) 2026/01/09 2026/01 C 13 NN A13 0.60 74,513 14.00 0 10,430 1,040 0 11,470
# 예) 2026/08/10~2026/08/11 2026/08 C 18 NN 3.00 16,318 14.00 0 2,280 220 0 2,500
_MIRAE_WH_LINE = re.compile(
    r"^(?:(?P<ticker>[A-Za-z][A-Za-z0-9]*|\d{6})\s+)?"
    r"(?:(?P<pstart>\d{4}/\d{2}/\d{2})~)?"
    r"(?P<pay>\d{4}/\d{2}/\d{2})\s+"
    r"(?P<ym>\d{4}/\d{2})\s+"
    r"(?P<tax>[CW])\s+"
    r"(?P<icode>\d+)\s+"
    r"(?:NN\s+)?"
    r"(?:(?P<code>[A-Z]\d{2})\s+)?"
    r"(?P<rate>\d+(?:\.\d+)?)\s+"
    r"(?P<amount>-?[\d,]+)\s+"
    r"(?P<taxrate>\d+(?:\.\d+)?)\s+"
    r"(?P<inc>[\d,]+)\s+"
    r"(?P<corp>[\d,]+)\s+"
    r"(?P<local>[\d,]+)\s+"
    r"(?P<agri>[\d,]+)\s+"
    r"(?P<total>[\d,]+)",
)
_MIRAE_WH_SKIP_NAME = re.compile(
    r"고객센터|홈페이지|합\s*계|징수|의무자|페이지|지급명세|원천징수"
)


def _is_mirae_withholding_pdf(text: str, filename: str = "") -> bool:
    name = filename or ""
    name_l = name.lower()
    if "원천징수" in name and ("미래에셋" in name or "mirae" in name_l):
        return True
    if "미래에셋" not in text and "mirae" not in name_l:
        return False
    if "이자·배당소득 원천징수" in text or "별지 제23호서식" in text:
        return True
    return len(_MIRAE_WH_LINE.findall(text)) >= 3


def _mirae_wh_income_type(tax: str, icode: int) -> str:
    """소득종류 11~49 이자, 51~99 배당. 과세구분 W는 배당, C는 이자(예외는 코드 우선)."""
    if 51 <= icode <= 99:
        return "배당"
    if 11 <= icode <= 49:
        return "이자"
    return "배당" if tax == "W" else "이자"


def _mirae_wh_product_name(ticker: str, name_line: str, tax: str, icode: int) -> str:
    name = (name_line or "").strip()
    name = re.sub(r"\s*\d{4}/\d{2}/\d{2}.*$", "", name).strip()
    name = re.sub(r"[~\-～]+$", "", name).strip()
    if _MIRAE_WH_SKIP_NAME.search(name) or re.fullmatch(r"\d{4}/\d{2}/\d{2}", name):
        name = ""
    ticker = (ticker or "").strip()
    if name:
        if ticker and ticker not in name:
            return f"{ticker} {name}".strip()
        return name
    if ticker:
        return ticker
    if icode == 13:
        return "예탁금이용료"
    if tax == "W" or 51 <= icode <= 99:
        return "배당"
    return "금융이자"


def parse_mirae_withholding_pdf(
    file_bytes: bytes, filename: str = ""
) -> IncomeParseResult:
    """미래에셋 이자·배당소득 원천징수영수증 PDF. 금액은 원화 그대로 사용."""
    notes: list[str] = []
    try:
        raw = _extract_pdf_text(file_bytes, include_tables=False)
    except Exception as exc:  # noqa: BLE001
        return IncomeParseResult(
            notes=[f"PDF 읽기 실패: {exc}"],
            source="pdf-mirae-wh",
        )

    if not raw.strip():
        return IncomeParseResult(
            notes=["PDF에서 텍스트를 추출하지 못했습니다. (스캔본이면 OCR 필요)"],
            source="pdf-mirae-wh",
        )

    text = re.sub(
        r"(\d{4}/\d{2}/\d{2})\s*[~\n～\-]+\s*(\d{4}/\d{2}/\d{2})",
        r"\1~\2",
        raw,
    )
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    pending: list[tuple[str, str, dict[str, Any]]] = []
    skipped_zero = 0
    for i, line in enumerate(lines):
        m = _MIRAE_WH_LINE.match(line)
        if not m:
            continue
        amount = _to_number(m.group("amount"))
        if amount == 0:
            skipped_zero += 1
            continue
        tax = m.group("tax")
        icode = int(m.group("icode"))
        itype = _mirae_wh_income_type(tax, icode)
        ticker = (m.group("ticker") or "").strip()
        pstart = m.group("pstart") or ""
        pay_raw = m.group("pay")
        period = f"{pstart}~{pay_raw}" if pstart else pay_raw
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        product = _mirae_wh_product_name(ticker, nxt, tax, icode)
        ym = m.group("ym").replace("/", "-")
        memo_bits = [
            "미래에셋증권 원천징수",
            f"과세구분={tax}",
            f"소득기간={period.replace('/', '-')}",
            f"귀속년월={ym}",
        ]
        if filename:
            memo_bits.append(filename)
        pending.append(
            (
                ticker or product,
                period,
                {
                    "지급일": pay_raw.replace("/", "-"),
                    "금융상품명": product,
                    "증권사": "미래에셋증권",
                    "소득구분": itype,
                    "종목코드": ticker,
                    "통화": "KRW",
                    "외화지급액": 0.0,
                    "외화원천세": 0.0,
                    "환율": 0.0,
                    "지급액": amount,
                    "법인세": _to_number(m.group("corp")),
                    "지방소득세": _to_number(m.group("local")),
                    "메모": " / ".join(memo_bits),
                },
            )
        )

    rows, skipped_offset = _drop_zero_net_kb_groups(pending)
    negative_left = [r for r in rows if float(r["지급액"]) < 0]
    if negative_left:
        rows = [r for r in rows if float(r["지급액"]) > 0]

    if not rows:
        return IncomeParseResult(
            notes=[
                "미래에셋 원천징수영수증에서 거래 라인을 찾지 못했습니다. "
                "일반 PDF 파서로 재시도합니다."
            ],
            source="pdf-mirae-wh",
        )

    gross_sum = sum(float(r["지급액"]) for r in rows)
    corp_sum = sum(float(r["법인세"]) for r in rows)
    local_sum = sum(float(r["지방소득세"]) for r in rows)
    div_n = sum(1 for r in rows if r["소득구분"] == "배당")
    int_n = sum(1 for r in rows if r["소득구분"] == "이자")
    notes.append(
        f"미래에셋 원천징수영수증에서 {len(rows)}건을 추출했습니다 "
        f"(배당 {div_n} · 이자 {int_n}, 지급액 합계 {gross_sum:,.0f}원 / "
        f"법인세 {corp_sum:,.0f} / 지방소득세 {local_sum:,.0f}). "
        "금액은 PDF 원화 그대로입니다."
    )
    if skipped_offset:
        notes.append(
            f"음수·양수가 상쇄되는 조정 묶음 {skipped_offset}건은 "
            "전표 금액이 왜곡되지 않도록 제외했습니다."
        )
    if negative_left:
        notes.append(f"상쇄되지 않은 음수 지급액 {len(negative_left)}건은 제외했습니다.")
    if skipped_zero:
        notes.append(f"지급액 0원 행 {skipped_zero}건은 제외했습니다.")
    return IncomeParseResult(rows=rows, notes=notes, source="pdf-mirae-wh")


def parse_mirae_trade_certificate_income(
    file_bytes: bytes, filename: str = ""
) -> IncomeParseResult:
    """미래에셋 '거래실적 증명서'에서 배당·예탁금이용료만 추출 → 이자·배당 내역."""
    notes: list[str] = []
    try:
        # 표 셀 혼합 시 라인 패턴이 깨지므로 본문 텍스트만 사용
        text = _extract_pdf_text(file_bytes, include_tables=False)
    except Exception as exc:  # noqa: BLE001
        return IncomeParseResult(
            notes=[f"PDF 읽기 실패: {exc}"],
            source="pdf-mirae-cert",
        )

    if not text.strip():
        return IncomeParseResult(
            notes=["PDF에서 텍스트를 추출하지 못했습니다. (스캔본이면 OCR 필요)"],
            source="pdf-mirae-cert",
        )

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        m_div = _MIRAE_DIV_HEADER.match(line)
        if m_div:
            date = m_div.group("date").replace("/", "-")
            ticker = m_div.group("ticker").strip()
            header_amt = _to_number(m_div.group("n1"))
            name = ticker
            tax_fx = 0.0
            net_fx = 0.0
            ccy = "USD"
            fx_rate = 0.0
            wrap_bits: list[str] = []
            for j in range(i + 1, min(i + 6, len(lines))):
                nxt = lines[j]
                if re.match(r"^\d{4}/\d{2}/\d{2}\b", nxt):
                    break
                dm = _parse_mirae_div_detail(nxt)
                if dm:
                    if dm["name"]:
                        name = str(dm["name"]).strip()
                    tax_fx = float(dm["tax"] or 0)
                    if float(dm["net"] or 0) > 0:
                        net_fx = float(dm["net"])
                    ccy = str(dm["ccy"] or "USD")
                    if _mirae_looks_like_fx(float(dm["fx"] or 0)):
                        fx_rate = float(dm["fx"])
                    continue
                # 구 거래실적증명서: tax mid net 3숫자
                old = _MIRAE_DIV_DETAIL.match(nxt)
                if old:
                    name = (old.group("name") or ticker).strip()
                    tax_fx = _to_number(old.group("tax"))
                    if _to_number(old.group("net")) > 0:
                        net_fx = _to_number(old.group("net"))
                    ccy = old.group("ccy") or "USD"
                    field_fx = _to_number(old.group("field"))
                    if fx_rate <= 0 and _mirae_looks_like_fx(field_fx):
                        fx_rate = field_fx
                    continue
                if re.search(r"\d{1,2}:\d{2}", nxt) or _MIRAE_WRAP_SKIP.search(nxt):
                    continue
                if re.match(r"^(?:Direct\d*|[가-힣A-Za-z]+)\s+\d{1,2}:\d{2}", nxt, re.I):
                    continue
                if re.search(r"[A-Za-z가-힣]", nxt):
                    wrap_bits.append(nxt)
            if name in {"", ticker} and wrap_bits:
                name = " ".join(wrap_bits).strip() or ticker
            # 처리점 줄에 붙은 환율(매매 서식)도 fallback
            if fx_rate <= 0:
                fx_rate = _mirae_find_fx_in_lines(lines, i + 1, min(i + 6, len(lines)))
            # 외화지급액 = 외화거래금액(세전). 없으면 외화입출금액+제세금합
            gross_fx = header_amt
            if gross_fx <= 0:
                gross_fx = float(net_fx or 0) + float(tax_fx or 0)
            if gross_fx <= 0:
                i += 1
                continue
            if fx_rate > 0:
                gross_krw = float(round(gross_fx * fx_rate))
                tax_krw = float(round(tax_fx * fx_rate)) if tax_fx > 0 else 0.0
            else:
                gross_krw = 0.0
                tax_krw = 0.0
                notes.append(
                    f"{date} {ticker} 배당: 환율 컬럼 없음 → 지급액 0원 "
                    f"(외화 {gross_fx:,.2f} {ccy}, 미리보기에서 환율 입력 가능)"
                )
            memo = build_fx_income_memo(
                ticker=ticker,
                product_name=name,
                amount_fx=gross_fx,
                tax_fx=tax_fx,
                fx_rate=fx_rate,
                currency=ccy,
                filename=filename or "",
            )
            rows.append(
                {
                    "지급일": date,
                    "금융상품명": name,
                    "증권사": "미래에셋증권",
                    "소득구분": "배당",
                    "종목코드": ticker,
                    "통화": ccy,
                    "외화지급액": float(gross_fx),
                    "외화원천세": float(tax_fx),
                    "환율": float(fx_rate),
                    "지급액": gross_krw,
                    "법인세": tax_krw,
                    "지방소득세": 0.0,
                    "메모": memo,
                }
            )
            i += 1
            continue

        m_fee = _MIRAE_KRW_FEE.match(line)
        if m_fee:
            date = m_fee.group("date").replace("/", "-")
            amt = _to_number(m_fee.group("amt"))
            if amt > 0:
                rows.append(
                    {
                        "지급일": date,
                        "금융상품명": "예탁금이용료",
                        "증권사": "미래에셋증권",
                        "소득구분": "이자",
                        "종목코드": "",
                        "통화": "KRW",
                        "외화지급액": 0.0,
                        "외화원천세": 0.0,
                        "환율": 0.0,
                        "지급액": amt,
                        "법인세": 0.0,
                        "지방소득세": 0.0,
                        "메모": (
                            "예탁금이용료입금"
                            + (f" / {filename}" if filename else "")
                        ),
                    }
                )
            i += 1
            continue

        m_fx = _MIRAE_FX_FEE.match(line)
        if m_fx:
            # 환율·금액이 PDF에 명시되지 않은 서식이 많아 추정하지 않고 스킵
            i += 1
            continue

        i += 1

    if not rows:
        return IncomeParseResult(
            notes=[
                "미래에셋 거래실적증명서에서 배당·예탁금이용료 행을 찾지 못했습니다. "
                "일반 PDF 파서로 재시도합니다."
            ],
            source="pdf-mirae-cert",
        )

    gross_sum = sum(float(r["지급액"]) for r in rows)
    div_n = sum(1 for r in rows if r["소득구분"] == "배당")
    int_n = sum(1 for r in rows if r["소득구분"] == "이자")
    notes.append(
        f"미래에셋 거래내역에서 {len(rows)}건을 추출했습니다 "
        f"(배당 {div_n} · 이자(이용료) {int_n}, 지급액 합계 {gross_sum:,.0f}원). "
        "외화배당 환율은 상세행 단가 칸(또는 처리점 옆 환율)을 사용합니다."
    )
    return IncomeParseResult(rows=rows, notes=notes, source="pdf-mirae-cert")


def parse_income_pdf(file_bytes: bytes, filename: str = "") -> IncomeParseResult:
    """원천징수영수증 PDF 파싱. 미래에셋 원천징수·거래실적·KB 과세내역 전용 파서 우선."""
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

    if _is_mirae_withholding_pdf(text, filename):
        mirae_wh = parse_mirae_withholding_pdf(file_bytes, filename)
        if mirae_wh.rows:
            return mirae_wh
        notes.extend(mirae_wh.notes)

    if _is_mirae_trade_certificate(text, filename):
        mirae = parse_mirae_trade_certificate_income(file_bytes, filename)
        if mirae.rows:
            return mirae
        notes.extend(mirae.notes)

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


def build_fx_income_memo(
    *,
    ticker: str,
    product_name: str,
    amount_fx: float,
    tax_fx: float,
    fx_rate: float,
    currency: str = "USD",
    filename: str = "",
    kind: str = "배당금외화입금",
) -> str:
    """외화 배당 메모. 환율 입력 반영."""
    label = (ticker or product_name or "").strip() or "외화배당"
    ccy = (currency or "USD").strip().upper() or "USD"
    bits: list[str] = []
    if fx_rate > 0:
        bits.append(f"{kind} {label} {amount_fx:,.2f}{ccy}×{fx_rate:,.2f}")
    else:
        bits.append(
            f"{kind} {label} {amount_fx:,.2f}{ccy} (환율 미기재→원화 0 / 직접 입력 가능)"
        )
    if tax_fx > 0:
        bits.append(f"외화원천세 {tax_fx:,.2f}{ccy}")
    if filename:
        bits.append(filename)
    return " / ".join(bits)


def apply_income_fx_rates(df: pd.DataFrame) -> pd.DataFrame:
    """환율 칸 기준으로 지급액·법인세·메모를 재계산.

    - 외화지급액 > 0 인 행만 대상
    - 환율 0 → 지급액·법인세 0 (임의 추정 없음)
    - 환율 > 0 → 지급액=외화지급액×환율, 법인세=외화원천세×환율
    """
    from .models import coerce_fx_rate

    if df is None or df.empty:
        return empty_income_frame()

    out = ensure_income_preview_columns(df)
    for idx in out.index:
        amt_fx = float(out.at[idx, "외화지급액"] or 0)
        if amt_fx <= 0:
            continue
        fx = coerce_fx_rate(out.at[idx, "환율"])
        tax_fx = float(out.at[idx, "외화원천세"] or 0)
        ccy = str(out.at[idx, "통화"] or "USD").strip() or "USD"
        ticker = str(out.at[idx, "종목코드"] or "").strip()
        product = str(out.at[idx, "금융상품명"] or "").strip()
        # 기존 메모에서 파일명 유지
        old_memo = str(out.at[idx, "메모"] or "")
        filename = ""
        if ".pdf" in old_memo.lower() or ".xlsx" in old_memo.lower():
            # 마지막 조각이 파일명인 경우 보존
            parts = [p.strip() for p in old_memo.split(" / ") if p.strip()]
            if parts and ("." in parts[-1]):
                filename = parts[-1]

        out.at[idx, "환율"] = fx
        if fx > 0:
            out.at[idx, "지급액"] = float(round(amt_fx * fx))
            out.at[idx, "법인세"] = float(round(tax_fx * fx)) if tax_fx > 0 else 0.0
        else:
            out.at[idx, "지급액"] = 0.0
            out.at[idx, "법인세"] = 0.0
        out.at[idx, "메모"] = build_fx_income_memo(
            ticker=ticker,
            product_name=product,
            amount_fx=amt_fx,
            tax_fx=tax_fx,
            fx_rate=fx,
            currency=ccy,
            filename=filename,
        )
    return out


def ensure_income_preview_columns(df: pd.DataFrame) -> pd.DataFrame:
    """미리보기 필수 컬럼 보장."""
    out = df.copy() if df is not None else empty_income_frame()
    for col in empty_income_frame().columns:
        if col not in out.columns:
            out[col] = 0.0 if col in _INCOME_NUM_COLS else ""
    # 숫자 컬럼 정규화
    for col in _INCOME_NUM_COLS:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    return out[list(empty_income_frame().columns)]


def rows_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return empty_income_frame()
    df = pd.DataFrame(rows)
    return ensure_income_preview_columns(df)
