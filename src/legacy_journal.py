"""기존 시트별 '주식 매매일지' 엑셀 일괄 파싱."""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

from .brokers.base import to_standard_frame
from .models import normalize_side

SKIP_SHEET_KEYWORDS = (
    "summary",
    "요약",
    "합계",
    "총괄",
    "dashboard",
    "대시보드",
    "목차",
    "cover",
    "표지",
)

# 헤더 셀에 이 키워드가 있으면 헤더 행으로 간주
HEADER_MUST_HINTS = ("매매 구분", "매매구분", "체결 단가", "체결단가", "체결 수량", "체결수량")

COL_ALIASES: dict[str, list[str]] = {
    "종목명": ["종목명", "종목", "종목이름"],
    "매수일자": ["매수 일자", "매수일자", "매입일자", "매입 일자"],
    "매도일자": ["매도 일자", "매도일자", "처분일자"],
    "매매구분": ["매매 구분", "매매구분", "거래유형", "유형"],
    "체결단가": ["체결 단가", "체결단가", "단가", "매수단가", "매도단가"],
    "체결수량": ["체결 수량", "체결수량", "매수수량", "수량"],
    "매도수량": ["매도수량", "매도 수량"],
    "매매비용": [
        "매매 비용(수수료+제세금)",
        "매매 비용",
        "매매비용",
        "수수료",
        "수수료+제세금",
        "제비용",
    ],
    "보유수량메모": ["주식 보유 수량", "보유 수량", "보유수량", "비고", "메모"],
    "종목코드": ["종목코드", "코드", "단축코드"],
}


def _safe_str(val: Any) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if pd.isna(val):
        return ""
    text = str(val).strip()
    if text.lower() in {"nan", "none", "nat", "<na>"}:
        return ""
    return text


def _row_join(row: Any) -> str:
    """행을 안전한 문자열로 join (float/NaN TypeError 방지)."""
    parts = [_safe_str(v) for v in list(row) if _safe_str(v)]
    return " ".join(parts)


def _norm_header(value: Any) -> str:
    return re.sub(r"\s+", " ", _safe_str(value))


def _safe_float(val: Any, default: float = 0.0) -> float:
    """천단위 쉼표/소수점/NaN을 안전하게 float으로 변환."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return float(val)
        except (TypeError, ValueError):
            return default
    text = _safe_str(val).replace(",", "").replace("원", "").replace(" ", "")
    if text in {"", "-", "--"}:
        return default
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return default


def _parse_date(value: Any) -> str | None:
    text = _safe_str(value)
    if not text or text in {"-"}:
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _is_skip_sheet(name: str) -> bool:
    lower = _safe_str(name).lower()
    if not lower:
        return True
    return any(k in lower for k in SKIP_SHEET_KEYWORDS)


def _find_header_row(raw: pd.DataFrame) -> int | None:
    """
    '매매 구분', '체결 단가', '체결 수량' 등이 포함된 행을 헤더로 탐색.
    """
    scan = min(25, len(raw))
    best_i, best_score = None, -1
    for i in range(scan):
        joined = _row_join(raw.iloc[i].tolist())
        if not joined:
            continue
        score = 0
        for hint in HEADER_MUST_HINTS:
            if hint in joined:
                score += 2
        for soft in ("매수 일자", "매도 일자", "매매 비용", "종목명", "일자", "구분"):
            if soft in joined:
                score += 1
        if score > best_score:
            best_score = score
            best_i = i
    # 최소: 매매구분/체결 관련 힌트 2점 이상
    if best_i is None or best_score < 2:
        return None
    return best_i


def _build_rename_map(columns: list[Any]) -> dict[str, str]:
    rename: dict[str, str] = {}
    used: set[str] = set()
    col_map = {_norm_header(c).lower(): _norm_header(c) for c in columns}

    for canonical, aliases in COL_ALIASES.items():
        for alias in aliases:
            key = alias.lower()
            if key in col_map and canonical not in used:
                rename[col_map[key]] = canonical
                used.add(canonical)
                break
            for raw_l, raw_name in col_map.items():
                if key in raw_l and canonical not in used and raw_name not in rename.values():
                    # 같은 원본 컬럼이 중복 매핑되지 않게
                    if raw_name not in rename:
                        rename[raw_name] = canonical
                        used.add(canonical)
                        break
            if canonical in used:
                break
    return rename


def _sheet_to_work(raw: pd.DataFrame, sheet_name: str) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None

    header_idx = _find_header_row(raw)
    if header_idx is None:
        return None

    headers = [_norm_header(x) for x in raw.iloc[header_idx].tolist()]
    # 빈/중복 헤더 보정
    seen: dict[str, int] = {}
    uniq: list[str] = []
    for i, h in enumerate(headers):
        name = h if h else f"col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        uniq.append(name)

    work = raw.iloc[header_idx + 1 :].copy()
    work.columns = uniq
    work = work.dropna(how="all")
    if work.empty:
        return None

    work = work.rename(columns=_build_rename_map(list(work.columns)))

    # 종목명: 행 값 우선, 없으면 시트명 / 병합셀 ffill
    if "종목명" in work.columns:
        cleaned = work["종목명"].map(_safe_str)
        cleaned = cleaned.replace({"": pd.NA})
        work["종목명"] = cleaned.ffill().fillna(sheet_name)
    else:
        work["종목명"] = sheet_name
    work["종목명"] = work["종목명"].map(lambda x: _safe_str(x) or sheet_name)
    return work


def _row_to_trade_dict(
    row: pd.Series, *, default_business: str, sheet_name: str
) -> dict[str, Any] | None:
    buy_date = _parse_date(row.get("매수일자")) if "매수일자" in row.index else None
    sell_date = _parse_date(row.get("매도일자")) if "매도일자" in row.index else None

    side: str | None = None
    if "매매구분" in row.index:
        side_raw = _safe_str(row.get("매매구분"))
        if side_raw:
            try:
                side = "매수" if normalize_side(side_raw) == "BUY" else "매도"
            except ValueError:
                # '구분' 컬럼에 다른 값이 있는 경우
                if "매수" in side_raw:
                    side = "매수"
                elif "매도" in side_raw:
                    side = "매도"

    trade_date: str | None = None
    if side == "매수" and buy_date:
        trade_date = buy_date
    elif side == "매도" and sell_date:
        trade_date = sell_date
    elif buy_date and not sell_date:
        trade_date, side = buy_date, side or "매수"
    elif sell_date and not buy_date:
        trade_date, side = sell_date, side or "매도"
    elif buy_date and sell_date and side in {"매수", "매도"}:
        trade_date = buy_date if side == "매수" else sell_date
    else:
        return None

    if not side or not trade_date:
        return None

    price = abs(_safe_float(row.get("체결단가"), default=-1.0)) if "체결단가" in row.index else -1.0
    if price <= 0:
        return None

    qty = 0.0
    if "체결수량" in row.index:
        qty = abs(_safe_float(row.get("체결수량")))
    if qty <= 0 and "매도수량" in row.index:
        qty = abs(_safe_float(row.get("매도수량")))
    if qty <= 0:
        return None

    fee = abs(_safe_float(row.get("매매비용"), default=0.0)) if "매매비용" in row.index else 0.0

    code = _safe_str(row.get("종목코드")) if "종목코드" in row.index else ""
    if code.endswith(".0"):
        code = code[:-2]
    if code.isdigit() and len(code) <= 6:
        code = code.zfill(6)

    name = _safe_str(row.get("종목명")) or sheet_name

    memo = ""
    if "보유수량메모" in row.index:
        memo_val = _safe_str(row.get("보유수량메모"))
        if memo_val and not re.fullmatch(r"-?\d+(\.\d+)?", memo_val.replace(",", "")):
            memo = memo_val

    settlement = qty * price + fee if side == "매수" else qty * price - fee
    return {
        "거래일자": trade_date,
        "사업자": default_business,
        "종목코드": code,
        "종목명": name,
        "거래유형": side,
        "수량": qty,
        "단가": price,
        "수수료": fee,
        "정산금액": settlement,
        "메모": memo or f"시트:{sheet_name}",
    }


def parse_legacy_journal_excel(
    file_bytes: bytes,
    *,
    default_business: str,
) -> tuple[pd.DataFrame, list[str], dict[str, int]]:
    """
    시트별 매매일지 엑셀 → 표준 거래 DataFrame.
    Returns: (dataframe, notes, stats)
      stats = {"trade_count": int, "sheet_count": int}
    """
    notes: list[str] = []
    try:
        book = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=None,
            header=None,
            dtype=object,
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"엑셀을 읽을 수 없습니다: {exc}") from exc

    if not book:
        raise ValueError("시트가 없는 엑셀입니다.")

    all_rows: list[dict[str, Any]] = []
    used_sheets = 0
    skipped_sheets: list[str] = []

    for sheet_name, raw in book.items():
        name = _safe_str(sheet_name) or "시트"
        if _is_skip_sheet(name):
            skipped_sheets.append(name)
            continue

        try:
            work = _sheet_to_work(raw, name)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"시트 '{name}' 오류: {exc}")
            skipped_sheets.append(name)
            continue

        if work is None or work.empty:
            skipped_sheets.append(name)
            continue

        sheet_count = 0
        for _, row in work.iterrows():
            try:
                item = _row_to_trade_dict(
                    row, default_business=default_business, sheet_name=name
                )
            except Exception:  # noqa: BLE001
                continue
            if item:
                all_rows.append(item)
                sheet_count += 1

        if sheet_count > 0:
            used_sheets += 1
            notes.append(f"시트 '{name}': {sheet_count}건")
        else:
            skipped_sheets.append(name)

    df = to_standard_frame(all_rows)
    if not df.empty:
        df = df.sort_values(["거래일자", "종목명", "거래유형"]).reset_index(drop=True)

    summary = (
        f"총 {len(df)}건의 거래 내역({used_sheets}개 종목 시트)이 정상 추출되었습니다."
    )
    notes.insert(0, summary)
    if skipped_sheets:
        # 문자열만 join
        skip_label = ", ".join(_safe_str(s) for s in skipped_sheets if _safe_str(s))
        if skip_label:
            notes.append(f"제외/무거래 시트: {skip_label}")

    stats = {"trade_count": int(len(df)), "sheet_count": int(used_sheets)}
    return df, notes, stats
