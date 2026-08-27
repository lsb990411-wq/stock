"""증권사 파서 공통 인터페이스."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from ..models import normalize_side


class BrokerParseResult:
    def __init__(
        self,
        broker_name: str,
        dataframe: pd.DataFrame,
        confidence: float,
        notes: list[str] | None = None,
    ) -> None:
        self.broker_name = broker_name
        self.dataframe = dataframe
        self.confidence = confidence
        self.notes = notes or []


class BrokerParser(ABC):
    name: str = "unknown"
    aliases: list[str] = []

    @abstractmethod
    def score(self, df: pd.DataFrame, filename: str = "") -> float:
        """0~1 인식 신뢰도."""

    @abstractmethod
    def parse(
        self,
        df: pd.DataFrame,
        *,
        default_business: str,
    ) -> BrokerParseResult:
        """표준 컬럼 DataFrame으로 변환."""


def clean_code(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    text = text.replace("A", "").replace("'", "")
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text


def clean_number(value: Any, default: float = 0.0) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("원", "").replace(" ", "")
    if text in {"", "-", "--", "nan", "None"}:
        return default
    # 괄호 음수 처리
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    return float(text)


def series_clean_code(series: pd.Series) -> pd.Series:
    """종목코드 시리즈를 벡터로 정규화."""
    s = series.fillna("").astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True)
    s = s.str.replace("A", "", regex=False).str.replace("'", "", regex=False)
    s = s.replace({"nan": "", "None": "", "none": ""})
    mask = s.str.fullmatch(r"\d{1,6}")
    return s.where(~mask, s.str.zfill(6))


def series_clean_number(series: pd.Series, default: float = 0.0) -> pd.Series:
    """숫자 시리즈를 벡터로 파싱 (쉼표/원/괄호음수 처리)."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(default)
    s = series.fillna("").astype(str).str.strip()
    s = s.str.replace(",", "", regex=False).str.replace("원", "", regex=False)
    # 괄호 음수 → -숫자
    paren = s.str.match(r"^\(.*\)$")
    s = s.where(~paren, "-" + s.str.slice(1, -1))
    s = s.replace({"": default, "-": default, "--": default, "nan": default, "None": default})
    return pd.to_numeric(s, errors="coerce").fillna(default)


def series_ensure_side(series: pd.Series) -> pd.Series:
    """매수/매도 시리즈 정규화. 실패 행은 빈 문자열."""
    out = []
    for v in series.tolist():
        try:
            out.append(ensure_side(v))
        except Exception:  # noqa: BLE001
            out.append("")
    return pd.Series(out, index=series.index)


def find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols = {str(c).strip(): c for c in df.columns}
    lower = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand in cols:
            return cols[cand]
        if cand.lower() in lower:
            return lower[cand.lower()]
    # 부분 포함
    for cand in candidates:
        for c in df.columns:
            if cand in str(c):
                return c
    return None


def ensure_side(value: Any) -> str:
    side = normalize_side(value)
    return "매수" if side == "BUY" else "매도"


def to_standard_frame(rows: list[dict[str, Any]] | pd.DataFrame) -> pd.DataFrame:
    base_columns = [
        "거래일자",
        "사업자",
        "종목코드",
        "종목명",
        "거래유형",
        "수량",
        "단가",
        "수수료",
        "제세금",
        "정산금액",
        "메모",
    ]
    if isinstance(rows, pd.DataFrame):
        df = rows.copy()
        has_bal = "유가금잔" in df.columns
    else:
        has_bal = any(isinstance(r, dict) and "유가금잔" in r for r in rows)
        if not rows:
            columns = (
                [
                    "거래일자",
                    "사업자",
                    "종목코드",
                    "종목명",
                    "거래유형",
                    "수량",
                    "유가금잔",
                    "단가",
                    "수수료",
                    "제세금",
                    "정산금액",
                    "메모",
                ]
                if has_bal
                else base_columns
            )
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows)

    columns = (
        [
            "거래일자",
            "사업자",
            "종목코드",
            "종목명",
            "거래유형",
            "수량",
            "유가금잔",
            "단가",
            "수수료",
            "제세금",
            "정산금액",
            "메모",
        ]
        if has_bal
        else base_columns
    )
    for c in columns:
        if c not in df.columns:
            df[c] = 0.0 if c in {"제세금", "수수료", "유가금잔"} else ""
    out = df.loc[:, columns].copy()
    if "제세금" in out.columns:
        out["제세금"] = pd.to_numeric(out["제세금"], errors="coerce").fillna(0.0)
    if "유가금잔" in out.columns:
        out["유가금잔"] = pd.to_numeric(out["유가금잔"], errors="coerce")
    return out.reset_index(drop=True)
