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


def to_standard_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = [
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
    return pd.DataFrame(rows, columns=columns)
