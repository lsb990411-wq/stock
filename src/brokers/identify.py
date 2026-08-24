"""파일 구조(시그니처 컬럼) 기반 증권사 자동 식별 및 표준 컬럼 매핑."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class BrokerSignature:
    """증권사별 파일 시그니처 정의."""

    broker_name: str
    market: str  # domestic | overseas
    # 이 조합이 있으면 해당 증권사로 강하게 판별
    strong_columns: tuple[str, ...] = ()
    # 있으면 가산점 (단독으로는 부족)
    soft_columns: tuple[str, ...] = ()
    # 파일명에 포함되면 가산점
    filename_tokens: tuple[str, ...] = ()
    # 원본 컬럼 → 시스템 표준 컬럼
    column_map: dict[str, str] = field(default_factory=dict)
    # PDF 등 텍스트 마커
    text_markers: tuple[str, ...] = ()
    min_strong_hits: int = 2


# 시스템 표준 컬럼 (국내 변환기)
STANDARD_DOMESTIC_COLS = (
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
)

# 시스템 표준 컬럼 (해외 변환기 미리보기)
STANDARD_OVERSEAS_COLS = (
    "거래일자",
    "거래유형",
    "종목코드",
    "종목명",
    "수량",
    "외화단가",
    "외화수수료",
    "외화제세금",
    "통화코드",
    "적용환율",
    "원화단가",
    "증권사",
    "메모",
)


BROKER_SIGNATURES: list[BrokerSignature] = [
    # ---- 국내 ----
    BrokerSignature(
        broker_name="미래에셋증권",
        market="domestic",
        strong_columns=("결제일자", "위탁수수료", "농특세", "거래대금"),
        soft_columns=("체결일자", "체결수량", "체결단가", "종목코드", "매매구분"),
        filename_tokens=("mirae", "미래에셋", "미래", "주식주문체결"),
        column_map={
            "결제일자": "거래일자",
            "체결일자": "거래일자",
            "거래일": "거래일자",
            "주문일자": "거래일자",
            "매매일자": "거래일자",
            "종목번호": "종목코드",
            "단축코드": "종목코드",
            "매매구분": "거래유형",
            "주문구분": "거래유형",
            "체결수량": "수량",
            "거래수량": "수량",
            "체결단가": "단가",
            "체결가": "단가",
            "위탁수수료": "수수료",
            "농특세": "제세금",
            "거래세": "제세금",
            "거래대금": "정산금액",
            "결제금액": "정산금액",
            "체결금액": "정산금액",
        },
        text_markers=("미래에셋", "MIRAE ASSET"),
        min_strong_hits=1,
    ),
    BrokerSignature(
        broker_name="키움증권",
        market="domestic",
        strong_columns=("체결번호", "원주문번호", "주문번호"),
        soft_columns=("체결일", "체결수량", "체결가", "종목코드", "매매구분"),
        filename_tokens=("kiwoom", "키움", "영웅문"),
        column_map={
            "체결일": "거래일자",
            "거래일": "거래일자",
            "주문일": "거래일자",
            "종목번호": "종목코드",
            "매매구분": "거래유형",
            "주문구분": "거래유형",
            "체결수량": "수량",
            "체결가": "단가",
            "체결단가": "단가",
            "수수료합": "수수료",
            "거래세": "제세금",
            "정산금액": "정산금액",
            "체결금액": "정산금액",
        },
        text_markers=("키움", "영웅문"),
        min_strong_hits=1,
    ),
    BrokerSignature(
        broker_name="DB금융투자",
        market="domestic",
        strong_columns=("예수금잔", "유가금잔", "거래일시"),
        soft_columns=("거래일자", "거래구분", "거래단가", "거래수량", "종목명"),
        filename_tokens=("db금융", "db증권", "dbsec", "증권거래내역", "디비금융"),
        column_map={
            "거래일자": "거래일자",
            "거래구분": "거래유형",
            "매매구분": "거래유형",
            "거래수량": "수량",
            "거래단가": "단가",
            "위탁수수료": "수수료",
            "세금": "제세금",
            "정산금액": "정산금액",
            "결제금액": "정산금액",
        },
        text_markers=("DB금융", "DB증권"),
        min_strong_hits=2,
    ),
    # ---- 해외 ----
    BrokerSignature(
        broker_name="메리츠증권",
        market="overseas",
        strong_columns=("거래단가(외화)", "기준환율", "매매금액(외화)"),
        soft_columns=("거래일자", "거래구분", "종목코드", "수수료(외화)", "통화"),
        filename_tokens=("메리츠", "meritz"),
        column_map={
            "거래일자": "거래일자",
            "거래구분": "거래유형",
            "종목코드": "종목코드",
            "종목명": "종목명",
            "거래수량": "수량",
            "거래단가(외화)": "외화단가",
            "외화단가": "외화단가",
            "수수료(외화)": "외화수수료",
            "제비용(외화)": "외화제세금",
            "제세금(외화)": "외화제세금",
            "통화": "통화코드",
            "기준환율": "적용환율",
            "적용환율": "적용환율",
            "환율": "적용환율",
        },
        text_markers=("메리츠", "MERITZ"),
        min_strong_hits=2,
    ),
    BrokerSignature(
        broker_name="KB증권",
        market="overseas",
        # 신규 flat: 결제일자·거래구분·기준환율·수수료(국외)
        # 구 pair: 거래종류·국외수수료·환율
        strong_columns=(
            "결제일자",
            "수수료(국외)",
            "외화정산금액",
            "거래종류",
            "국외수수료",
        ),
        soft_columns=(
            "거래구분",
            "기준환율",
            "종목코드",
            "종목명",
            "수량",
            "단가",
            "거래금액",
            "환율",
            "거래일자",
        ),
        filename_tokens=("kb", "kb증권", "증권계좌거래", "해외주식거래내역"),
        column_map={
            "결제일자": "거래일자",
            "거래일자": "거래일자",
            "거래구분": "거래유형",
            "거래종류": "거래유형",
            "종목코드": "종목코드",
            "종목명": "종목명",
            "수량": "수량",
            "단가": "외화단가",
            "수수료(국외)": "외화수수료",
            "국외수수료": "외화수수료",
            "거래세": "외화제세금",
            "통화": "통화코드",
            "기준환율": "적용환율",
            "적용환율": "적용환율",
            "환율": "적용환율",
        },
        text_markers=("KB증권", "KB 증권", "수수료(국외)", "결제일자"),
        min_strong_hits=2,
    ),
    BrokerSignature(
        broker_name="미래에셋증권",
        market="overseas",
        strong_columns=(),
        soft_columns=(),
        filename_tokens=("mirae", "미래에셋", "해외주식"),
        column_map={},
        text_markers=("미래에셋", "해외주식매수", "해외주식매도", "배당금외화입금"),
        min_strong_hits=0,
    ),
]


@dataclass
class IdentifyResult:
    broker_name: str | None
    market: str | None  # domestic | overseas | None
    confidence: float
    signature: BrokerSignature | None = None
    matched_columns: list[str] = field(default_factory=list)
    message: str = ""
    candidates: list[tuple[str, float]] = field(default_factory=list)

    @property
    def identified(self) -> bool:
        return bool(self.broker_name) and self.confidence >= 0.35


def _norm_cols(columns: Any) -> set[str]:
    return {str(c).strip() for c in (columns or []) if str(c).strip()}


def _score_signature(
    sig: BrokerSignature,
    cols: set[str],
    filename: str = "",
    text: str = "",
) -> tuple[float, list[str]]:
    """시그니처 점수(0~1)와 매칭된 컬럼 목록."""
    score = 0.0
    matched: list[str] = []
    fname = (filename or "").lower()
    name_raw = filename or ""

    for tok in sig.filename_tokens:
        if tok.lower() in fname or tok in name_raw:
            score += 0.35
            break

    strong_hits = [c for c in sig.strong_columns if c in cols or any(c in x for x in cols)]
    soft_hits = [c for c in sig.soft_columns if c in cols or any(c in x for x in cols)]
    matched.extend(strong_hits)
    matched.extend(soft_hits)

    if strong_hits:
        # strong 1개당 가중, min_strong_hits 이상이면 보너스
        score += min(0.55, 0.22 * len(strong_hits))
        if len(strong_hits) >= sig.min_strong_hits:
            score += 0.15

    if soft_hits:
        score += min(0.35, 0.07 * len(soft_hits))

    if text and sig.text_markers:
        hits = sum(1 for m in sig.text_markers if m in text or m.lower() in text.lower())
        if hits:
            score += min(0.4, 0.15 * hits)

    # KB 해외 flat 양식: 결제일자+수수료(국외)면 강하게 가산
    if sig.broker_name == "KB증권" and sig.market == "overseas":
        if "결제일자" in cols and ("수수료(국외)" in cols or "외화정산금액" in cols):
            score = max(score, 0.92)
            matched = list(
                dict.fromkeys([*matched, "결제일자", "수수료(국외)", "기준환율"])
            )

    # KB 해외: 헤더가 2행에 걸쳐 있을 때 컬럼명이 Unnamed인 경우
    if sig.broker_name == "KB증권" and sig.market == "overseas" and text:
        if "거래종류" in text and "국외수수료" in text and "환율" in text:
            score = max(score, 0.75)
            matched = list(dict.fromkeys([*matched, "거래종류", "국외수수료", "환율"]))

    return min(score, 1.0), list(dict.fromkeys(matched))


def identify_broker(
    *,
    columns: Any = None,
    filename: str = "",
    text: str = "",
    market: str | None = None,
    min_confidence: float = 0.35,
) -> IdentifyResult:
    """컬럼명·파일명·본문 텍스트로 증권사를 식별.

    market이 지정되면 해당 시장 시그니처만 비교합니다.
    """
    cols = _norm_cols(columns)
    scored: list[tuple[BrokerSignature, float, list[str]]] = []

    for sig in BROKER_SIGNATURES:
        if market and sig.market != market:
            continue
        s, matched = _score_signature(sig, cols, filename=filename, text=text)
        if s > 0:
            scored.append((sig, s, matched))

    scored.sort(key=lambda x: x[1], reverse=True)
    candidates = [(s.broker_name, sc) for s, sc, _ in scored[:5]]

    if not scored or scored[0][1] < min_confidence:
        return IdentifyResult(
            broker_name=None,
            market=market,
            confidence=scored[0][1] if scored else 0.0,
            message="증권사 양식을 자동 인식하지 못했습니다. 증권사를 선택해 주세요.",
            candidates=candidates,
        )

    best_sig, best_score, matched = scored[0]
    # 동점/근접 후보가 다른 증권사면 신뢰도 낮춤
    if len(scored) >= 2 and scored[1][1] >= best_score - 0.05 and scored[1][0].broker_name != best_sig.broker_name:
        if best_score < 0.55:
            return IdentifyResult(
                broker_name=None,
                market=market,
                confidence=best_score,
                message=(
                    f"여러 증권사 후보가 유사합니다 "
                    f"({best_sig.broker_name} {best_score:.0%}, "
                    f"{scored[1][0].broker_name} {scored[1][1]:.0%}). "
                    "증권사를 선택해 주세요."
                ),
                candidates=candidates,
            )

    msg = f"[{best_sig.broker_name} 양식으로 인식되었습니다]"
    if matched:
        msg += f" (시그니처: {', '.join(matched[:5])})"
    return IdentifyResult(
        broker_name=best_sig.broker_name,
        market=best_sig.market,
        confidence=best_score,
        signature=best_sig,
        matched_columns=matched,
        message=msg,
        candidates=candidates,
    )


def identify_from_dataframe(
    df: pd.DataFrame,
    filename: str = "",
    *,
    market: str | None = None,
    text: str = "",
) -> IdentifyResult:
    """DataFrame 컬럼으로 증권사 식별. KB처럼 헤더가 본문에 있으면 text도 함께 전달."""
    cols = list(df.columns) if df is not None else []
    # 상위 행에 헤더 문자열이 섞인 경우(KB) 보조 텍스트
    extra = text
    if df is not None and not df.empty and df.shape[0] >= 1:
        head_bits = []
        for i in range(min(3, len(df))):
            head_bits.extend(str(v) for v in df.iloc[i].tolist())
        extra = (extra + " " + " ".join(head_bits)).strip()
    return identify_broker(
        columns=cols,
        filename=filename,
        text=extra,
        market=market,
    )


def apply_column_map(
    df: pd.DataFrame,
    column_map: dict[str, str] | None,
    *,
    overwrite_existing: bool = False,
) -> pd.DataFrame:
    """원본 컬럼을 표준 컬럼명으로 복사/개명.

    - 표준명이 이미 있으면 기본적으로 유지
    - 원본→표준 매핑만 적용 (값 변환은 파서가 담당)
    """
    if df is None or df.empty or not column_map:
        return df
    out = df.copy()
    rename: dict[str, str] = {}
    for src, dst in column_map.items():
        if src not in out.columns:
            # 부분 일치
            hit = next((c for c in out.columns if src in str(c)), None)
            if hit is None:
                continue
            src = str(hit)
        if src == dst:
            continue
        if dst in out.columns and not overwrite_existing:
            # 표준 컬럼이 없으면 값만 채움
            continue
        if dst in out.columns and overwrite_existing:
            out[dst] = out[src]
        else:
            rename[src] = dst
    if rename:
        # 충돌 방지: 동일 대상으로 여러 소스면 첫 번째만
        used: set[str] = set()
        safe: dict[str, str] = {}
        for s, d in rename.items():
            if d in used or d in out.columns:
                if d not in out.columns:
                    out[d] = out[s]
                continue
            used.add(d)
            safe[s] = d
        out = out.rename(columns=safe)
    return out


def get_column_map(broker_name: str, market: str | None = None) -> dict[str, str]:
    for sig in BROKER_SIGNATURES:
        if sig.broker_name != broker_name:
            continue
        if market and sig.market != market:
            continue
        return dict(sig.column_map)
    return {}


def list_identifiable_brokers(market: str | None = None) -> list[str]:
    names: list[str] = []
    for sig in BROKER_SIGNATURES:
        if market and sig.market != market:
            continue
        if sig.broker_name not in names:
            names.append(sig.broker_name)
    return names
