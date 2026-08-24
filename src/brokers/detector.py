"""증권사 파일 자동 인식 및 변환."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ..import_export import read_tabular
from .base import BrokerParseResult, BrokerParser
from .db_securities import DBSecuritiesParser
from .identify import (
    IdentifyResult,
    apply_column_map,
    get_column_map,
    identify_from_dataframe,
    list_identifiable_brokers,
)
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
    for name in list_identifiable_brokers(market="domestic"):
        if name not in names:
            names.append(name)
    return names


def file_ext(filename: str) -> str:
    """파일 확장자(소문자, 점 제외)."""
    name = (filename or "").strip().lower()
    name = Path(name).name
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1]


def is_pdf_file(file_bytes: bytes, filename: str) -> bool:
    if file_ext(filename) == "pdf":
        return True
    return bool(file_bytes) and file_bytes[:4] == b"%PDF"


def _load_tabular_candidates(
    file_bytes: bytes, filename: str, ext: str
) -> tuple[list[pd.DataFrame], Exception | None]:
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
    return candidates, last_exc


def detect_broker_from_file(
    file_bytes: bytes,
    filename: str,
    *,
    market: str | None = "domestic",
) -> IdentifyResult:
    """파일만으로 증권사 식별 (파싱 전 단계)."""
    ext = file_ext(filename)

    if ext == "pdf" or is_pdf_file(file_bytes, filename):
        # PDF는 본문 마커로 식별
        try:
            import pdfplumber
            from io import BytesIO

            parts: list[str] = []
            with pdfplumber.open(BytesIO(file_bytes)) as pdf:
                for page in pdf.pages[:3]:
                    parts.append(page.extract_text() or "")
            text = "\n".join(parts)
        except Exception:  # noqa: BLE001
            text = ""
        from .identify import identify_broker

        return identify_broker(
            filename=filename,
            text=text,
            market=market,
        )

    candidates, _ = _load_tabular_candidates(file_bytes, filename, ext)
    if not candidates:
        from .identify import identify_broker

        return identify_broker(filename=filename, market=market)

    best: IdentifyResult | None = None
    for df in candidates:
        result = identify_from_dataframe(df, filename, market=market)
        if best is None or result.confidence > best.confidence:
            best = result
    return best or IdentifyResult(
        broker_name=None,
        market=market,
        confidence=0.0,
        message="증권사 양식을 자동 인식하지 못했습니다. 증권사를 선택해 주세요.",
    )


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
            broker_hint=broker_hint if broker_hint and broker_hint != "자동 감지" else None,
        )

    # ---- CSV / Excel ----
    if ext not in {"csv", "xlsx", "xls"}:
        raise ValueError(
            f"지원하지 않는 파일 형식입니다: {ext or '(확장자 없음)'} "
            "(지원: csv, xlsx, xls, pdf)"
        )

    candidates, last_exc = _load_tabular_candidates(file_bytes, filename, ext)
    if not candidates:
        raise ValueError(f"파일을 읽을 수 없습니다: {last_exc}")

    # 1) 시그니처 컬럼으로 증권사 식별
    identity: IdentifyResult | None = None
    best_id_df: pd.DataFrame | None = None
    for df in candidates:
        cand = identify_from_dataframe(df, filename, market="domestic")
        if identity is None or cand.confidence > identity.confidence:
            identity = cand
            best_id_df = df

    hint = broker_hint if broker_hint and broker_hint != "자동 감지" else None
    identified_name = hint or (identity.broker_name if identity and identity.identified else None)

    best_score = -1.0
    best_parser: BrokerParser | None = None
    best_df: pd.DataFrame | None = None

    parsers = PARSERS
    if identified_name:
        matched = [p for p in PARSERS if p.name == identified_name]
        if matched:
            parsers = matched

    for df in candidates:
        # 식별된 증권사의 컬럼 매핑 선적용 (표준명 보강)
        mapped = df
        if identified_name:
            cmap = get_column_map(identified_name, market="domestic")
            if cmap:
                mapped = apply_column_map(df, cmap)

        for parser in parsers:
            score = parser.score(mapped, filename)
            if identified_name and parser.name == identified_name:
                score = min(1.0, score + 0.25)
            if score > best_score:
                best_score = score
                best_parser = parser
                best_df = mapped

    # 시그니처로 강하게 식별됐는데 파서 score가 낮으면 best_id_df + 매핑으로 재시도
    if (
        best_parser is not None
        and identity
        and identity.identified
        and identity.broker_name == best_parser.name
        and best_score < 0.35
        and best_id_df is not None
    ):
        cmap = get_column_map(best_parser.name, market="domestic")
        mapped = apply_column_map(best_id_df, cmap) if cmap else best_id_df
        best_df = mapped
        best_score = max(best_score, identity.confidence)

    if best_parser is None or best_df is None or best_score < 0.35:
        msg = "증권사 포맷을 인식하지 못했습니다."
        if identity and identity.message:
            msg = identity.message
        raise ValueError(
            f"{msg} 증권사를 직접 선택하거나 표준 양식으로 변환 후 다시 시도하세요."
        )

    result = best_parser.parse(best_df, default_business=default_business)
    result.confidence = best_score
    recog = (
        identity.message
        if identity and identity.identified and identity.broker_name == best_parser.name
        else f"[{best_parser.name} 양식으로 인식되었습니다]"
    )
    result.notes = [
        recog,
        f"신뢰도 {best_score:.0%}",
        *result.notes,
    ]
    return result


def detect_and_parse_overseas(
    file_bytes: bytes,
    filename: str,
) -> dict[str, Any]:
    """해외주식 파일 자동 식별 후 해당 파서로 변환.

    반환: {rows, notes, source, broker_name, identified}
    """
    from .kb_overseas import is_kb_overseas_excel, parse_kb_overseas_excel
    from .meritz_overseas import is_meritz_overseas_excel, parse_meritz_overseas_excel
    from .mirae_overseas import parse_mirae_overseas_pdf

    ext = file_ext(filename)
    notes: list[str] = []

    # PDF → 미래에셋 해외(현재 지원 포맷)
    if ext == "pdf" or is_pdf_file(file_bytes, filename):
        identity = detect_broker_from_file(file_bytes, filename, market="overseas")
        result = parse_mirae_overseas_pdf(file_bytes, filename)
        broker = "미래에셋증권"
        if identity.identified and identity.broker_name:
            broker = identity.broker_name
        notes = [
            f"[{broker} 양식으로 인식되었습니다]",
            *(result.get("notes") or []),
        ]
        return {
            **result,
            "notes": notes,
            "broker_name": broker,
            "identified": True,
        }

    # Excel: 내용 시그니처 우선 (파일명만으로 메리츠/KB 오판 방지)
    probe: pd.DataFrame | None = None
    try:
        from io import BytesIO

        probe = pd.read_excel(
            BytesIO(file_bytes),
            engine="xlrd" if ext == "xls" else None,
        )
    except Exception:  # noqa: BLE001
        probe = None

    # KB flat(결제일자)을 메리츠보다 먼저 — 공통 컬럼(거래구분·기준환율) 오판 방지
    if is_kb_overseas_excel(filename, probe) or is_kb_overseas_excel(filename):
        result = parse_kb_overseas_excel(file_bytes, filename)
        if result.get("rows") or is_kb_overseas_excel(filename, probe):
            notes = ["[KB증권 양식으로 인식되었습니다]", *(result.get("notes") or [])]
            return {**result, "notes": notes, "broker_name": "KB증권", "identified": True}

    if is_meritz_overseas_excel(filename) or is_meritz_overseas_excel(filename, probe):
        result = parse_meritz_overseas_excel(file_bytes, filename)
        notes = ["[메리츠증권 양식으로 인식되었습니다]", *(result.get("notes") or [])]
        return {**result, "notes": notes, "broker_name": "메리츠증권", "identified": True}

    identity = (
        identify_from_dataframe(probe, filename, market="overseas")
        if probe is not None
        else IdentifyResult(None, "overseas", 0.0, message="인식 실패")
    )

    if identity.identified and identity.broker_name == "KB증권":
        result = parse_kb_overseas_excel(file_bytes, filename)
        notes = [identity.message, *(result.get("notes") or [])]
        return {**result, "notes": notes, "broker_name": "KB증권", "identified": True}

    if identity.identified and identity.broker_name == "메리츠증권":
        result = parse_meritz_overseas_excel(file_bytes, filename)
        notes = [identity.message, *(result.get("notes") or [])]
        return {**result, "notes": notes, "broker_name": "메리츠증권", "identified": True}

    return {
        "rows": [],
        "notes": [
            identity.message
            if identity and not identity.identified
            else "지원 형식: 미래에셋 해외주식 PDF, KB증권·메리츠증권 해외주식 거래내역 엑셀.",
            "인식되지 않으면 아래에서 증권사를 선택한 뒤 다시 업로드하세요.",
        ],
        "source": "overseas-unknown",
        "broker_name": None,
        "identified": False,
    }


def parse_overseas_with_hint(
    file_bytes: bytes,
    filename: str,
    broker_hint: str,
) -> dict[str, Any]:
    """미식별 시 사용자가 고른 증권사로 해외 파싱."""
    from .kb_overseas import parse_kb_overseas_excel
    from .meritz_overseas import parse_meritz_overseas_excel
    from .mirae_overseas import parse_mirae_overseas_pdf

    hint = (broker_hint or "").strip()
    if hint == "미래에셋증권" or file_ext(filename) == "pdf":
        result = parse_mirae_overseas_pdf(file_bytes, filename)
        return {
            **result,
            "notes": [f"[{hint or '미래에셋증권'} 양식으로 처리했습니다]", *(result.get("notes") or [])],
            "broker_name": "미래에셋증권",
            "identified": True,
        }
    if hint == "메리츠증권":
        result = parse_meritz_overseas_excel(file_bytes, filename)
        return {
            **result,
            "notes": ["[메리츠증권 양식으로 처리했습니다]", *(result.get("notes") or [])],
            "broker_name": "메리츠증권",
            "identified": True,
        }
    if hint == "KB증권":
        result = parse_kb_overseas_excel(file_bytes, filename)
        return {
            **result,
            "notes": ["[KB증권 양식으로 처리했습니다]", *(result.get("notes") or [])],
            "broker_name": "KB증권",
            "identified": True,
        }
    return {
        "rows": [],
        "notes": [f"지원하지 않는 증권사 선택: {hint}"],
        "source": "overseas-unknown",
        "broker_name": None,
        "identified": False,
    }
