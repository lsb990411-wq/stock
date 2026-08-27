"""미래에셋증권 거래/체결내역 파서."""

from __future__ import annotations

import pandas as pd

from .base import (
    BrokerParseResult,
    BrokerParser,
    find_col,
    series_clean_code,
    series_clean_number,
    series_ensure_side,
    to_standard_frame,
)


class MiraeAssetParser(BrokerParser):
    name = "미래에셋증권"
    aliases = ["미래에셋", "mirae", "miraeasset", "주식주문체결"]

    DATE_CANDS = ["결제일자", "거래일자", "거래일", "체결일자", "주문일자", "매매일자", "일자", "기준일"]
    CODE_CANDS = ["종목코드", "종목번호", "단축코드", "종목CD", "코드"]
    NAME_CANDS = ["종목명", "종목", "종목이름"]
    SIDE_CANDS = ["거래유형", "매매구분", "거래구분", "주문구분", "매수매도구분", "구분"]
    QTY_CANDS = ["수량", "체결수량", "거래수량", "주문수량"]
    PRICE_CANDS = ["단가", "체결단가", "체결가", "매매단가", "평균단가"]
    FEE_CANDS = ["수수료", "위탁수수료"]
    TAX_CANDS = ["제세금", "거래세", "농특세", "세금"]
    AMT_CANDS = ["정산금액", "결제금액", "거래대금", "체결금액", "거래금액"]
    ACCT_CANDS = ["계좌번호", "계좌명", "계좌"]

    def score(self, df: pd.DataFrame, filename: str = "") -> float:
        score = 0.0
        fname = filename.lower()
        if any(a in fname for a in ("mirae", "미래에셋", "미래")):
            score += 0.45
        hits = 0
        for group in (
            self.DATE_CANDS,
            self.CODE_CANDS,
            self.SIDE_CANDS,
            self.QTY_CANDS,
            self.PRICE_CANDS,
        ):
            if find_col(df, group):
                hits += 1
        score += hits * 0.1
        headers = " ".join(str(c) for c in df.columns)
        if "위탁수수료" in headers or "농특세" in headers or "거래대금" in headers:
            score += 0.15
        if "결제일자" in headers:
            score += 0.2
        return min(score, 1.0)

    def parse(self, df: pd.DataFrame, *, default_business: str) -> BrokerParseResult:
        date_c = find_col(df, self.DATE_CANDS)
        code_c = find_col(df, self.CODE_CANDS)
        name_c = find_col(df, self.NAME_CANDS)
        side_c = find_col(df, self.SIDE_CANDS)
        qty_c = find_col(df, self.QTY_CANDS)
        price_c = find_col(df, self.PRICE_CANDS)
        fee_c = find_col(df, self.FEE_CANDS)
        tax_c = find_col(df, self.TAX_CANDS)
        amt_c = find_col(df, self.AMT_CANDS)
        acct_c = find_col(df, self.ACCT_CANDS)

        required = {
            "일자": date_c,
            "종목코드": code_c,
            "거래유형": side_c,
            "수량": qty_c,
            "단가": price_c,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(f"미래에셋증권 파서: 필수 컬럼 인식 실패 ({', '.join(missing)})")

        work = df.copy()
        side_text = work[side_c].astype(str)
        skip = side_text.str.contains("취소|정정|거부|조회", na=False)
        work = work.loc[~skip].copy()
        notes: list[str] = []
        if work.empty:
            return BrokerParseResult(
                self.name, to_standard_frame([]), confidence=self.score(df), notes=notes
            )

        sides = series_ensure_side(work[side_c])
        qty = series_clean_number(work[qty_c]).abs()
        price = series_clean_number(work[price_c])
        fee = series_clean_number(work[fee_c]) if fee_c else 0.0
        tax = series_clean_number(work[tax_c]) if tax_c else 0.0
        total_cost = fee + tax
        if amt_c:
            settlement = series_clean_number(work[amt_c]).abs()
        else:
            settlement = qty * price + total_cost
            sell_mask = sides == "매도"
            settlement = settlement.where(~sell_mask, qty * price - total_cost)

        codes = series_clean_code(work[code_c])
        names = (
            work[name_c].fillna("").astype(str).str.strip()
            if name_c
            else codes.copy()
        )
        names = names.where(names != "", codes)
        dates = pd.to_datetime(work[date_c], errors="coerce")

        memos = pd.Series(["미래에셋증권"] * len(work), index=work.index)
        if acct_c:
            acct = work[acct_c].fillna("").astype(str).str.strip()
            acct = acct.replace({"nan": "", "None": ""})
            has_acct = acct != ""
            memos = memos.where(~has_acct, memos + " / 계좌:" + acct)

        valid = (sides != "") & (qty > 0) & dates.notna()
        bad_dates = (~dates.notna()) & (sides != "") & (qty > 0)
        if bool(bad_dates.any()):
            for raw in work.loc[bad_dates, date_c].head(5).tolist():
                notes.append(f"날짜 스킵: {raw}")

        out = pd.DataFrame(
            {
                "거래일자": dates.dt.strftime("%Y-%m-%d"),
                "사업자": default_business,
                "종목코드": codes,
                "종목명": names,
                "거래유형": sides,
                "수량": qty,
                "단가": price,
                "수수료": total_cost,
                "정산금액": settlement,
                "메모": memos,
            }
        )
        out = out.loc[valid].reset_index(drop=True)
        return BrokerParseResult(
            self.name,
            to_standard_frame(out),
            confidence=self.score(df),
            notes=notes,
        )
