"""키움증권 거래/체결내역 파서."""

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


class KiwoomParser(BrokerParser):
    name = "키움증권"
    aliases = ["키움", "kiwoom", "영웅문"]

    DATE_CANDS = ["체결일", "거래일", "주문일", "일자", "매매일자"]
    CODE_CANDS = ["종목코드", "종목번호", "단축코드", "코드"]
    NAME_CANDS = ["종목명", "종목"]
    SIDE_CANDS = ["매매구분", "주문구분", "거래구분", "구분", "매수매도"]
    QTY_CANDS = ["체결수량", "주문수량", "수량", "거래수량"]
    PRICE_CANDS = ["체결가", "체결단가", "매매가", "단가", "가격"]
    FEE_CANDS = ["수수료", "수수료합", "제비용"]
    TAX_CANDS = ["거래세", "제세금", "세금"]
    AMT_CANDS = ["정산금액", "결제금액", "거래금액", "체결금액", "금액"]

    def score(self, df: pd.DataFrame, filename: str = "") -> float:
        score = 0.0
        fname = filename.lower()
        if any(a in fname for a in ("kiwoom", "키움", "영웅문")):
            score += 0.45
        headers = " ".join(str(c) for c in df.columns)
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
        # 키움 특이 컬럼
        if "체결번호" in headers or "원주문번호" in headers or "주문번호" in headers:
            score += 0.15
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

        required = {
            "일자": date_c,
            "종목코드": code_c,
            "거래유형": side_c,
            "수량": qty_c,
            "단가": price_c,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(f"키움증권 파서: 필수 컬럼 인식 실패 ({', '.join(missing)})")

        rows = []
        notes: list[str] = []
        for _, row in df.iterrows():
            side_raw = row[side_c]
            side_text = str(side_raw)
            # 취소/정정 등 제외
            if any(tok in side_text for tok in ("취소", "정정", "거부")):
                continue
            try:
                side = ensure_side(side_raw)
            except ValueError:
                continue

            qty = abs(clean_number(row[qty_c]))
            price = clean_number(row[price_c])
            if qty <= 0:
                continue

            fee = clean_number(row[fee_c]) if fee_c else 0.0
            tax = clean_number(row[tax_c]) if tax_c else 0.0
            total_cost = fee + tax

            if amt_c:
                settlement = abs(clean_number(row[amt_c]))
            else:
                settlement = qty * price + total_cost if side == "매수" else qty * price - total_cost

            code = clean_code(row[code_c])
            name = str(row[name_c]).strip() if name_c else code
            date_val = pd.to_datetime(row[date_c], errors="coerce")
            if pd.isna(date_val):
                notes.append(f"날짜 스킵: {row[date_c]}")
                continue

            rows.append(
                {
                    "거래일자": date_val.strftime("%Y-%m-%d"),
                    "사업자": default_business,
                    "종목코드": code,
                    "종목명": name,
                    "거래유형": side,
                    "수량": qty,
                    "단가": price,
                    "수수료": total_cost,
                    "정산금액": settlement,
                    "메모": "키움증권",
                }
            )

        out = to_standard_frame(rows)
        return BrokerParseResult(self.name, out, confidence=self.score(df), notes=notes)
