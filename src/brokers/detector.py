"""증권사 파일 자동 인식 및 변환."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..import_export import read_tabular
from .base import BrokerParseResult, BrokerParser
from .db_securities import DBSecuritiesParser
from .kiwoom import KiwoomParser
from .miraeasset import MiraeAssetParser
from .pdf_parser import parse_broker_pdf

PARSERS: list[BrokerParser] = [
    KiwoomParser(),
    MiraeAssetParser(),
    DBSecuritiesParser(),
]
PDF_BROKERS = ["키움증권", "미래에셋증권", "한국투자증권", "DB금융투자"]


def list_brokers() -> list[str]:
    names = [p.name for p in PARSERS]
    for name in PDF_BROKERS:
        if name not in names:
            names.append(name)
    return names


def file_ext(filename: str) -> str:
    """파일 확장자(소문자, 점 제외)."""
    name = (filename or "").strip().lower()
    # 경로/공백 제거 후 마지막 확장자만 사용
    name = Path(name).name
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1]


def is_pdf_file(file_bytes: bytes, filename: str) -> bool:
    if file_ext(filename) == "pdf":
        return True
    # 확장자가 비정상인 경우 매직 넘버로 감지
    return bool(file_bytes) and file_bytes[:4] == b"%PDF"


def detect_and_parse(
    file_bytes: bytes,
    filename: str,
    *,
    default_business: str,
    broker_hint: str | None = None,
) -> BrokerParseResult:
    ext = file_ext(filename)

    # ---- PDF ----
    if ext == "pdf" or is_pdf_file(file_bytes, filename):
        return parse_broker_pdf(
            file_bytes,
            filename,
            default_business=default_business,
            broker_hint=broker_hint,
        )

    # ---- CSV / Excel ----
    last_exc: Exception | None = None
    candidates: list[pd.DataFrame] = []

    if ext in {"csv", "xlsx", "xls"}:
        try:
            candidates.append(read_tabular(file_bytes, filename))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

        if ext in {"xlsx", "xls"}:
            import io

            for header in range(0, 8):
                try:
                    df = pd.read_excel(io.BytesIO(file_bytes), header=header)
                    if len(df.columns) >= 4:
                        candidates.append(df)
                except Exception:  # noqa: BLE001
                    continue
    else:
        raise ValueError(
            f"지원하지 않는 파일 형식입니다: {ext or '(확장자 없음)'} "
            "(지원: csv, xlsx, xls, pdf)"
        )

    if not candidates:
        raise ValueError(f"파일을 읽을 수 없습니다: {last_exc}")

    best_score = -1.0
    best_parser: BrokerParser | None = None
    best_df: pd.DataFrame | None = None

    parsers = PARSERS
    if broker_hint and broker_hint != "자동 감지":
        parsers = [p for p in PARSERS if p.name == broker_hint] or PARSERS

    for df in candidates:
        for parser in parsers:
            score = parser.score(df, filename)
            if broker_hint and broker_hint == parser.name:
                score += 0.3
            if score > best_score:
                best_score = score
                best_parser = parser
                best_df = df

    if best_parser is None or best_df is None or best_score < 0.35:
        raise ValueError(
            "증권사 포맷을 인식하지 못했습니다. "
            "증권사를 직접 선택하거나 표준 양식으로 변환 후 다시 시도하세요."
        )

    result = best_parser.parse(best_df, default_business=default_business)
    result.confidence = best_score
    result.notes = [
        f"인식: {best_parser.name} (신뢰도 {best_score:.0%})",
        *result.notes,
    ]
    return result
