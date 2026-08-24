"""DB금융투자(DB Securities) 증권거래내역상세 Excel 파서."""

from __future__ import annotations

import pandas as pd

from .base import (
    BrokerParseResult,
    BrokerParser,
    clean_code,
    clean_number,
    ensure_side,
    find_col,
    to_standard_frame,
)


def _get_priority(gubun: object) -> int:
    """매수/입고=0(먼저), 매도/출고=1(나중에)."""
    text = str(gubun or "")
    if "매수" in text or "입고" in text or "매입" in text:
        return 0
    return 1


def _signed_qty_simple(qty: object, gubun: object) -> float:
    """매수/입고 +, 그 외(매도/출고 등) - ."""
    q = abs(clean_number(qty))
    text = str(gubun or "")
    if "매수" in text or "입고" in text or "매입" in text:
        return q
    if "매도" in text or "출고" in text:
        return -q
    return 0.0


class DBSecuritiesParser(BrokerParser):
    """DB금융투자 '증권거래내역상세' 엑셀.

    파일 읽은 직후:
      1) 원본 유가금잔 삭제
      2) 정렬우선순위 + 일자·일시 정렬
      3) 계산된수량 → 유가금잔(cumsum) 덮어쓰기
    """

    name = "DB금융투자"
    aliases = [
        "DB금융",
        "DB증권",
        "디비금융",
        "db증권",
        "db금융",
        "dbsec",
        "db securities",
        "증권거래내역상세",
    ]

    DATE_CANDS = ["거래일자", "거래일", "체결일", "일자"]
    TIME_CANDS = ["거래일시", "체결시각", "체결시간", "거래시간", "시간"]
    CODE_CANDS = ["종목코드", "종목번호", "단축코드"]
    NAME_CANDS = ["종목명", "종목"]
    SIDE_CANDS = ["거래유형", "거래구분", "매매구분", "주문구분", "매수매도"]
    QTY_CANDS = ["수량", "거래수량", "체결수량"]
    PRICE_CANDS = ["단가", "거래단가", "체결단가", "체결가"]
    FEE_CANDS = ["수수료", "위탁수수료"]
    TAX_CANDS = ["제세금", "세금", "거래세"]
    AMT_CANDS = ["정산금액", "결제금액", "거래금액"]

    def score(self, df: pd.DataFrame, filename: str = "") -> float:
        score = 0.0
        fname = (filename or "").lower()
        if any(
            a.lower() in fname
            for a in self.aliases
            + ["증권거래내역", "db금융투자", "dbfi"]
        ):
            score += 0.5

        hits = 0
        for group in (
            self.DATE_CANDS,
            self.SIDE_CANDS,
            self.QTY_CANDS,
            self.PRICE_CANDS,
            self.NAME_CANDS,
        ):
            if find_col(df, group):
                hits += 1
        score += hits * 0.08

        headers = " ".join(str(c) for c in df.columns)
        if "예수금잔" in headers:
            score += 0.12
        if "유가금잔" in headers:
            score += 0.1
        if "외화예수금잔고" in headers or "외화정산금액" in headers:
            score += 0.08
        if "거래단가" in headers and "거래수량" in headers and "거래구분" in headers:
            score += 0.1
        if "거래일시" in headers:
            score += 0.05
        return min(score, 1.0)

    def _normalize_right_after_read(self, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        """파일을 읽은 직후: 원본 유가금잔 삭제 → 우선순위·정렬 → 부호수량 → cumsum."""
        notes: list[str] = []
        work = df.copy()

        # --- 원본 유가금잔 삭제 ---
        dropped = [c for c in list(work.columns) if str(c).strip() in {"유가금잔", "잔고수량", "보유수량"} or str(c).startswith("유가금잔")]
        if dropped:
            work = work.drop(columns=dropped)
            notes.append(f"원본 잔고 컬럼 삭제 후 재계산: {', '.join(str(c) for c in dropped)}")

        side_c = find_col(work, self.SIDE_CANDS)
        qty_c = find_col(work, self.QTY_CANDS)
        name_c = find_col(work, self.NAME_CANDS)
        date_c = find_col(work, self.DATE_CANDS)
        time_c = find_col(work, self.TIME_CANDS)

        if side_c is None or qty_c is None or name_c is None or date_c is None:
            return work, notes

        # --- 거래일시 결측 보정 ---
        if time_c is None:
            work["거래일시"] = "00:00:00"
            time_c = "거래일시"
        else:
            raw = work[time_c]
            if pd.api.types.is_datetime64_any_dtype(raw):
                work[time_c] = pd.to_datetime(raw).dt.strftime("%H:%M:%S")
            else:
                extracted = (
                    raw.astype(str)
                    .str.strip()
                    .str.extract(r"(\d{1,2}:\d{2}(?::\d{2})?)", expand=False)
                )
                work[time_c] = extracted
            work[time_c] = work[time_c].fillna("00:00:00")

        # --- 1) 정렬우선순위 ---
        work["정렬우선순위"] = work[side_c].map(_get_priority)

        # --- 2) 날짜, 시간, 우선순위 정렬 ---
        work["_sort_date"] = pd.to_datetime(work[date_c], errors="coerce")
        work["_orig_idx"] = range(len(work))
        work = work.sort_values(
            by=["_sort_date", time_c, "정렬우선순위", "_orig_idx"],
            ascending=True,
            kind="mergesort",
        ).reset_index(drop=True)
        notes.append(
            "거래일자·거래일시·매수우선(매수→매도) 정렬 후 유가금잔을 재계산했습니다."
        )

        # --- 3) 매수 +, 매도 - ---
        work["계산된수량"] = [
            _signed_qty_simple(q, g)
            for q, g in zip(work[qty_c], work[side_c])
        ]

        # --- 4) 종목별 cumsum → 유가금잔 덮어쓰기 ---
        work["유가금잔"] = work.groupby(name_c, sort=False)["계산된수량"].cumsum()

        # 정리용 임시 컬럼 제거는 parse에서 사용 후 처리
        return work, notes

    def parse(self, df: pd.DataFrame, *, default_business: str) -> BrokerParseResult:
        # ★ 파일 읽은 직후 정렬·잔고 재계산
        work, notes = self._normalize_right_after_read(df)

        date_c = find_col(work, self.DATE_CANDS)
        code_c = find_col(work, self.CODE_CANDS)
        name_c = find_col(work, self.NAME_CANDS)
        side_c = find_col(work, self.SIDE_CANDS)
        qty_c = find_col(work, self.QTY_CANDS)
        price_c = find_col(work, self.PRICE_CANDS)
        fee_c = find_col(work, self.FEE_CANDS)
        tax_c = find_col(work, self.TAX_CANDS)
        amt_c = find_col(work, self.AMT_CANDS)

        required = {
            "일자": date_c,
            "거래유형": side_c,
            "수량": qty_c,
            "단가": price_c,
            "종목명": name_c,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(
                f"DB금융투자 파서: 필수 컬럼 인식 실패 ({', '.join(missing)})"
            )

        rows: list[dict] = []
        skipped_non_trade = 0

        for _, row in work.iterrows():
            side_text = str(row[side_c] or "").strip()
            if not side_text or side_text.lower() in {"nan", "none"}:
                continue
            if any(tok in side_text for tok in ("취소", "정정", "거부")):
                continue

            # 이미 계산된 부호 수량 기준 (0이면 비매매)
            signed = float(row.get("계산된수량") or 0)
            if signed == 0:
                skipped_non_trade += 1
                continue

            qty = abs(clean_number(row[qty_c]))
            price = clean_number(row[price_c])
            if qty <= 0 or price <= 0:
                continue

            try:
                side = ensure_side(row[side_c])
            except ValueError:
                side = "매수" if signed > 0 else "매도"

            name = str(row[name_c]).strip()
            if not name or name.lower() in {"nan", "none"}:
                notes.append(f"종목명 없음 스킵: {side_text}")
                continue

            code = clean_code(row[code_c]) if code_c else ""
            if code and not (code.isdigit() or len(code) <= 12):
                code = ""
            if code.upper() in {"KRW", "USD", "JPY", "EUR", "HKD", "CNY"}:
                code = ""

            fee = clean_number(row[fee_c]) if fee_c else 0.0
            tax = clean_number(row[tax_c]) if tax_c else 0.0
            if amt_c:
                settlement = abs(clean_number(row[amt_c]))
            else:
                settlement = (
                    qty * price + fee + tax
                    if side == "매수"
                    else max(qty * price - fee - tax, 0)
                )

            date_val = pd.to_datetime(row[date_c], errors="coerce")
            if pd.isna(date_val):
                notes.append(f"날짜 스킵: {row[date_c]}")
                continue

            bal = float(row.get("유가금잔") or 0)

            rows.append(
                {
                    "거래일자": date_val.strftime("%Y-%m-%d"),
                    "사업자": default_business,
                    "종목코드": code,
                    "종목명": name,
                    "거래유형": side,
                    "수량": qty,
                    "유가금잔": bal,
                    "단가": price,
                    "수수료": fee,
                    "제세금": tax,
                    "정산금액": settlement,
                    "메모": f"DB금융투자/{side_text}",
                }
            )

        if skipped_non_trade:
            notes.append(f"비매매 거래 {skipped_non_trade}건 제외 (이체·예탁금·배당 등)")
        if not rows:
            notes.append("매수/매도 행을 찾지 못했습니다. 파일을 확인해 주세요.")
        else:
            notes.append(
                "유가금잔 = 정렬 후 매수(+)/매도(-) 종목별 cumsum 결과입니다."
            )

        out = to_standard_frame(rows)
        return BrokerParseResult(
            self.name, out, confidence=self.score(df), notes=notes
        )
