"""증권사 PDF 거래내역 파서 (pdfplumber)."""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

from .base import (
    BrokerParseResult,
    clean_code,
    clean_number,
    ensure_side,
    find_col,
    to_standard_frame,
)

# 표 헤더 후보
DATE_HEADERS = ["거래일자", "체결일", "거래일", "일자", "매매일자", "주문일", "체결일자"]
CODE_HEADERS = ["종목코드", "종목번호", "단축코드", "코드"]
NAME_HEADERS = ["종목명", "종목", "종목이름"]
SIDE_HEADERS = ["매매구분", "거래유형", "거래구분", "주문구분", "구분", "매수매도"]
QTY_HEADERS = ["체결수량", "수량", "거래수량"]
PRICE_HEADERS = ["체결가", "체결단가", "단가", "매매가", "가격"]
FEE_HEADERS = ["수수료", "위탁수수료", "수수료합", "제비용"]
TAX_HEADERS = ["거래세", "제세금", "세금", "농특세"]
AMT_HEADERS = ["정산금액", "결제금액", "거래금액", "체결금액", "거래대금", "금액"]

# 텍스트 한 줄/블록 패턴
DATE_RE = re.compile(
    r"(?P<date>(?:20\d{2})[./-]?(?:0[1-9]|1[0-2])[./-]?(?:0[1-9]|[12]\d|3[01])|"
    r"(?:20\d{2})년\s*(?:0?[1-9]|1[0-2])월\s*(?:0?[1-9]|[12]\d|3[01])일)"
)
SIDE_RE = re.compile(r"(매수|매도|매입)")
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
# 종목명: 한글/영문/숫자/&·- 조합 (코드와 인접)
NAME_RE = re.compile(
    r"([가-힣A-Za-z0-9&·\-\.]{2,20})"
)
# 수량 단가 수수료 순으로 숫자가 나열되는 흔한 패턴
NUM_TRIPLE_RE = re.compile(
    r"(?P<qty>\d{1,3}(?:,\d{3})*|\d+)\s+"
    r"(?P<price>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s+"
    r"(?P<fee>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)?"
)


def _detect_broker_from_text(text: str, filename: str = "") -> str:
    blob = f"{filename}\n{text}".lower()
    if any(k in blob for k in ("키움", "kiwoom", "영웅문")):
        return "키움증권"
    if any(k in blob for k in ("미래에셋", "mirae", "증권 미래에셋")):
        return "미래에셋증권"
    if any(k in blob for k in ("한국투자", "한투", "true friend", "korea investment")):
        return "한국투자증권"
    return "PDF(자동)"


def _normalize_date(raw: str) -> str | None:
    text = str(raw).strip()
    text = text.replace("년", "-").replace("월", "-").replace("일", "")
    text = re.sub(r"[./]", "-", text)
    text = re.sub(r"\s+", "", text)
    digits = re.sub(r"\D", "", text)
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    try:
        ts = pd.to_datetime(text, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def _header_map(columns: list[Any]) -> dict[str, Any]:
    df_stub = pd.DataFrame(columns=columns)
    mapping: dict[str, Any] = {}
    for key, cands in {
        "date": DATE_HEADERS,
        "code": CODE_HEADERS,
        "name": NAME_HEADERS,
        "side": SIDE_HEADERS,
        "qty": QTY_HEADERS,
        "price": PRICE_HEADERS,
        "fee": FEE_HEADERS,
        "tax": TAX_HEADERS,
        "amt": AMT_HEADERS,
    }.items():
        col = find_col(df_stub, cands)
        if col is not None:
            mapping[key] = col
    return mapping


def _table_to_rows(
    table: list[list[Any]], *, default_business: str, source_note: str
) -> list[dict[str, Any]]:
    if not table or len(table) < 2:
        return []

    # 첫 non-empty 행을 헤더로 가정
    header_idx = 0
    for i, row in enumerate(table[:5]):
        joined = " ".join(str(c or "") for c in row)
        if any(h in joined for h in (*DATE_HEADERS, *NAME_HEADERS, *SIDE_HEADERS)):
            header_idx = i
            break

    headers = [str(c).strip() if c is not None else f"col{i}" for i, c in enumerate(table[header_idx])]
    # 중복 헤더 보정
    seen: dict[str, int] = {}
    uniq_headers: list[str] = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            uniq_headers.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            uniq_headers.append(h or "col")

    body = table[header_idx + 1 :]
    df = pd.DataFrame(body, columns=uniq_headers)
    df = df.dropna(how="all")
    mapping = _header_map(list(df.columns))
    if "date" not in mapping or "side" not in mapping or "qty" not in mapping or "price" not in mapping:
        return []

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            side_raw = row[mapping["side"]]
            if side_raw is None or str(side_raw).strip() in {"", "None", "nan"}:
                continue
            side_text = str(side_raw)
            if any(tok in side_text for tok in ("취소", "정정", "거부", "합계", "소계")):
                continue
            side = ensure_side(side_raw)
            qty = abs(clean_number(row[mapping["qty"]]))
            price = clean_number(row[mapping["price"]])
            if qty <= 0:
                continue
            date_val = _normalize_date(row[mapping["date"]])
            if not date_val:
                continue

            fee = clean_number(row[mapping["fee"]]) if "fee" in mapping else 0.0
            tax = clean_number(row[mapping["tax"]]) if "tax" in mapping else 0.0
            total_fee = fee + tax
            if "amt" in mapping:
                settlement = abs(clean_number(row[mapping["amt"]]))
            else:
                settlement = (
                    qty * price + total_fee if side == "매수" else qty * price - total_fee
                )

            code = ""
            if "code" in mapping:
                code = clean_code(row[mapping["code"]])
            name = ""
            if "name" in mapping and row[mapping["name"]] is not None:
                name = str(row[mapping["name"]]).strip()
            if not name:
                name = code or "미상"

            rows.append(
                {
                    "거래일자": date_val,
                    "사업자": default_business,
                    "종목코드": code,
                    "종목명": name,
                    "거래유형": side,
                    "수량": qty,
                    "단가": price,
                    "수수료": total_fee,
                    "정산금액": settlement,
                    "메모": source_note,
                }
            )
        except Exception:  # noqa: BLE001
            continue
    return rows


def _extract_tables_with_pdfplumber(file_bytes: bytes) -> tuple[list[list[list[Any]]], str]:
    import pdfplumber

    tables: list[list[list[Any]]] = []
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
            page_tables = page.extract_tables() or []
            for t in page_tables:
                if t and len(t) >= 2:
                    tables.append(t)
    return tables, "\n".join(text_parts)


def _parse_text_lines(
    text: str, *, default_business: str, source_note: str
) -> list[dict[str, Any]]:
    """표 추출 실패 시 텍스트 줄 단위 휴리스틱 파싱."""
    rows: list[dict[str, Any]] = []
    lines = [ln.strip() for ln in text.splitlines() if ln and ln.strip()]

    for i, line in enumerate(lines):
        if not SIDE_RE.search(line):
            continue
        if any(tok in line for tok in ("취소", "정정", "합계", "소계", "잔고", "평가")):
            continue

        # 날짜: 같은 줄 또는 바로 위 줄
        date_match = DATE_RE.search(line)
        if not date_match and i > 0:
            date_match = DATE_RE.search(lines[i - 1])
        if not date_match:
            continue
        date_val = _normalize_date(date_match.group("date"))
        if not date_val:
            continue

        try:
            side = ensure_side(SIDE_RE.search(line).group(1))  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            continue

        code_match = CODE_RE.search(line)
        code = clean_code(code_match.group(1)) if code_match else ""

        # 종목명 추정: 날짜/유형/숫자 제거 후 남은 한글 토큰
        cleaned = DATE_RE.sub(" ", line)
        cleaned = SIDE_RE.sub(" ", cleaned)
        cleaned = CODE_RE.sub(" ", cleaned)
        cleaned = re.sub(r"[\d,.\-]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        name_tokens = [
            t
            for t in cleaned.split()
            if re.search(r"[가-힣A-Za-z]", t) and t not in {"주", "원", "보통", "시장가", "지정가"}
        ]
        name = name_tokens[0] if name_tokens else (code or "미상")

        nums = re.findall(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?", line)
        # 날짜 숫자 제거(YYYYMMDD가 잡힌 경우)
        nums = [n for n in nums if not re.fullmatch(r"20\d{6}", n.replace(",", ""))]
        if code:
            nums = [n for n in nums if n.replace(",", "") != code.lstrip("0") and n.replace(",", "") != code]

        if len(nums) < 2:
            continue
        qty = abs(clean_number(nums[0]))
        price = clean_number(nums[1])
        fee = clean_number(nums[2]) if len(nums) >= 3 else 0.0
        if qty <= 0 or price < 0:
            continue

        settlement = qty * price + fee if side == "매수" else qty * price - fee
        rows.append(
            {
                "거래일자": date_val,
                "사업자": default_business,
                "종목코드": code,
                "종목명": name,
                "거래유형": side,
                "수량": qty,
                "단가": price,
                "수수료": fee,
                "정산금액": settlement,
                "메모": source_note,
            }
        )
    return rows


OCR_TIP = (
    "이미지형 PDF입니다. HTS/MTS에서 CSV/엑셀 파일로 다운로드받아 업로드하시면 "
    "100% 정확하게 자동 변환됩니다."
)

STANDARD_EMPTY_COLUMNS = [
    "거래일자",
    "사업자",
    "종목코드",
    "종목명",
    "거래유형",
    "수량",
    "단가",
    "수수료",
    "정산금액",
    "메모",
]


def empty_trade_rows(default_business: str, n: int = 5) -> pd.DataFrame:
    """수동 입력/붙여넣기용 빈 행 템플릿."""
    rows = []
    for _ in range(n):
        rows.append(
            {
                "거래일자": "",
                "사업자": default_business,
                "종목코드": "",
                "종목명": "",
                "거래유형": "매수",
                "수량": None,
                "단가": None,
                "수수료": 0,
                "정산금액": None,
                "메모": "",
            }
        )
    return pd.DataFrame(rows, columns=STANDARD_EMPTY_COLUMNS)


def _ocr_pdf_text(file_bytes: bytes, *, max_pages: int = 8) -> tuple[str, list[str]]:
    """
    스캔(이미지) PDF용 OCR fallback.
    pytesseract + pdfplumber 페이지 렌더링을 사용한다.
    """
    notes: list[str] = []
    try:
        import pytesseract  # type: ignore
    except ImportError:
        notes.append(OCR_TIP)
        notes.append("OCR 사용 시: pip install pytesseract 및 Tesseract-OCR 본체를 설치하세요.")
        return "", notes

    try:
        import pdfplumber
    except ImportError:
        notes.append(OCR_TIP)
        return "", notes

    parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = pdf.pages[:max_pages]
            if not pages:
                notes.append("PDF 페이지가 비어 있습니다.")
                return "", notes
            notes.append(f"OCR 시도 중… (최대 {len(pages)}페이지)")
            for idx, page in enumerate(pages, start=1):
                try:
                    rendered = page.to_image(resolution=200)
                    img = rendered.original
                    text = pytesseract.image_to_string(img, lang="kor+eng")
                    if not text.strip():
                        # 한국어 데이터 없을 때 eng만 재시도
                        text = pytesseract.image_to_string(img, lang="eng")
                    parts.append(text or "")
                except pytesseract.TesseractNotFoundError:
                    notes.append(OCR_TIP)
                    notes.append(
                        "Tesseract 실행 파일을 찾을 수 없습니다. "
                        "https://github.com/tesseract-ocr/tesseract 설치 후 PATH에 추가하세요."
                    )
                    return "", notes
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"OCR {idx}페이지 실패: {exc}")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"OCR 렌더링 실패: {exc}")
        notes.append(OCR_TIP)
        return "", notes

    joined = "\n".join(parts).strip()
    if joined:
        notes.append(f"OCR로 텍스트 {len(joined)}자 추출")
    else:
        notes.append("OCR로도 텍스트를 추출하지 못했습니다.")
        notes.append(OCR_TIP)
    return joined, notes


def parse_broker_pdf(
    file_bytes: bytes,
    filename: str = "",
    *,
    default_business: str,
    broker_hint: str | None = None,
) -> BrokerParseResult:
    """PDF에서 표/텍스트를 추출해 표준 거래 DataFrame으로 변환."""
    try:
        tables, text = _extract_tables_with_pdfplumber(file_bytes)
    except ImportError as exc:
        raise ImportError(
            "pdfplumber가 설치되어 있지 않습니다. "
            "`pip install pdfplumber` 후 다시 시도하세요."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"PDF를 읽을 수 없습니다: {exc}") from exc

    notes: list[str] = [f"PDF 페이지 텍스트 {len(text)}자, 표 {len(tables)}개 감지"]

    # 텍스트가 거의 없으면 스캔 PDF로 보고 OCR fallback
    if len(text.strip()) < 50:
        notes.append("텍스트 추출량이 적어(50자 미만) OCR fallback을 시도합니다.")
        ocr_text, ocr_notes = _ocr_pdf_text(file_bytes)
        notes.extend(ocr_notes)
        if ocr_text:
            text = f"{text}\n{ocr_text}".strip()

    broker = _detect_broker_from_text(text, filename)
    if broker_hint and broker_hint not in {"자동 감지", None}:
        broker = broker_hint

    source_note = f"PDF:{broker}"
    rows: list[dict[str, Any]] = []

    for table in tables:
        extracted = _table_to_rows(
            table, default_business=default_business, source_note=source_note
        )
        rows.extend(extracted)

    if not rows:
        text_rows = _parse_text_lines(
            text, default_business=default_business, source_note=source_note
        )
        rows.extend(text_rows)
        if text_rows:
            notes.append("표 헤더 인식 실패 → 텍스트/OCR 패턴으로 추출했습니다. 검수해 주세요.")
        else:
            notes.append(
                "거래 행 0건 감지. 아래 테이블에 직접 입력하거나 "
                "엑셀/PDF에서 복사해 붙여넣기(Ctrl+V) 하세요."
            )
            if len(text.strip()) < 50:
                notes.append(OCR_TIP)

    # 중복 제거
    df = to_standard_frame(rows)
    if not df.empty:
        df = df.drop_duplicates(
            subset=["거래일자", "종목코드", "종목명", "거래유형", "수량", "단가"],
            keep="first",
        ).reset_index(drop=True)
    else:
        # 수동 편집용 빈 행 5개
        df = empty_trade_rows(default_business, n=5)
        notes.append("수동 입력을 위해 빈 행 5개를 준비했습니다.")

    confidence = (
        0.75
        if (not df.empty and len(tables) > 0 and rows)
        else (0.45 if rows else 0.15)
    )
    notes.insert(0, f"인식: {broker} PDF (신뢰도 {confidence:.0%} · 추출 {len(rows)}건)")
    return BrokerParseResult(broker, df, confidence=confidence, notes=notes)
