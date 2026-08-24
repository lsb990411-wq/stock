"""CSV/Excel Import 및 Export."""

from __future__ import annotations

import io
from typing import Any

import pandas as pd

from .models import Trade, coerce_fx_rate, normalize_currency, normalize_side, now_str
from .storage import Storage

# 표준 매매일지 컬럼 (한글 헤더)
STANDARD_COLUMNS = [
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

COLUMN_ALIASES = {
    "거래일자": ["거래일자", "일자", "날짜", "체결일", "매매일자", "trade_date", "date"],
    "사업자": ["사업자", "법인", "사업자명", "계좌명", "business", "entity"],
    "종목코드": ["종목코드", "코드", "단축코드", "종목번호", "stock_code", "code"],
    "종목명": ["종목명", "종목", "종목이름", "stock_name", "name"],
    "거래유형": ["거래유형", "매매구분", "구분", "유형", "매수매도", "side", "type"],
    "수량": ["수량", "체결수량", "거래수량", "quantity", "qty"],
    "단가": ["단가", "체결가", "매매단가", "가격", "price", "unit_price"],
    "수수료": ["수수료", "제비용", "fee", "commission"],
    "제세금": ["제세금", "세금", "거래세", "제세", "tax"],
    "정산금액": ["정산금액", "결제금액", "거래금액", "금액", "settlement", "amount"],
    "메모": ["메모", "비고", "적요", "memo", "note"],
    "환율": ["환율", "적용환율", "적용 환율", "fx_rate", "exchange_rate"],
    "통화": ["통화", "통화코드", "currency", "ccy"],
    "외화단가": ["외화단가", "외화가격", "price_fx"],
    "외화수수료": ["외화수수료", "fee_fx"],
    "외화제세금": ["외화제세금", "tax_fx"],
    "증권사": ["증권사", "증권회사", "금융기관", "broker", "broker_name", "거래처"],
}


def broker_name_from_source(source: str = "", fallback: str = "") -> str:
    """source/증권사 문자열에서 표시용 증권사명 추출."""
    text = (fallback or "").strip()
    if text and text.lower() not in {"nan", "none"}:
        return text
    src = (source or "").strip()
    if src.startswith("broker:"):
        name = src.split(":", 1)[1].strip()
        # mirae-overseas → 미래에셋증권 등 정규화
        lower = name.lower()
        mapping = {
            "mirae-overseas": "미래에셋증권",
            "mirae": "미래에셋증권",
            "miraeasset": "미래에셋증권",
            "kb-overseas": "KB증권",
            "kb": "KB증권",
            "meritz-overseas": "메리츠증권",
            "meritz": "메리츠증권",
            "kiwoom": "키움증권",
            "db": "DB금융투자",
            "db금융투자": "DB금융투자",
        }
        if lower in mapping:
            return mapping[lower]
        if "미래에셋" in name or "mirae" in lower:
            return "미래에셋증권"
        if "kb" in lower or "KB" in name:
            return "KB증권"
        if "메리츠" in name or "meritz" in lower:
            return "메리츠증권"
        if "키움" in name or "kiwoom" in lower:
            return "키움증권"
        if "DB" in name.upper() or "디비" in name:
            return "DB금융투자"
        return name
    if src in {"manual", "manual-overseas", "csv", "legacy_journal"}:
        return ""
    return src


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename: dict[str, str] = {}
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    used_src: set[str] = set()

    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = alias.lower()
            if key in lower_map and lower_map[key] not in used_src:
                rename[lower_map[key]] = canonical
                used_src.add(lower_map[key])
                break
    out = df.rename(columns=rename)
    return out


def _parse_date(value: Any) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"날짜 파싱 실패: {value}")
    return ts.strftime("%Y-%m-%d")


def _to_float(value: Any, default: float = 0.0) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("원", "")
        if text in {"", "-", "nan", "None"}:
            return default
        return float(text)
    return float(value)


def read_tabular(file_bytes: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").strip().lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    bio = io.BytesIO(file_bytes)
    if ext == "csv" or name.endswith(".csv"):
        for enc in ("utf-8-sig", "cp949", "euc-kr", "utf-8"):
            bio.seek(0)
            try:
                return pd.read_csv(bio, encoding=enc)
            except UnicodeDecodeError:
                continue
        bio.seek(0)
        return pd.read_csv(bio)
    if ext in {"xlsx", "xls"} or name.endswith((".xlsx", ".xls")):
        return pd.read_excel(bio)
    if ext == "pdf" or name.endswith(".pdf"):
        raise ValueError(
            "PDF는 read_tabular로 처리할 수 없습니다. PDF 전용 파서를 사용하세요."
        )
    raise ValueError(f"지원하지 않는 파일 형식입니다: {ext or name} (지원: csv, xlsx, xls)")


def dataframe_to_trades(
    df: pd.DataFrame,
    storage: Storage,
    *,
    default_business: str | None = None,
    source: str = "csv",
    market: str = "domestic",
) -> tuple[list[Trade], list[str]]:
    """표준/유사 컬럼 DataFrame을 Trade 리스트로 변환하고 종목/사업자를 자동 생성."""
    from .models import normalize_market

    mkt = normalize_market(market)
    work = _normalize_columns(df.copy())
    required = ["거래일자", "거래유형", "수량", "단가"]
    missing = [c for c in required if c not in work.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {', '.join(missing)}")
    if "종목코드" not in work.columns and "종목명" not in work.columns:
        raise ValueError("종목코드 또는 종목명 컬럼이 필요합니다.")

    errors: list[str] = []
    trades: list[Trade] = []

    for idx, row in work.iterrows():
        row_no = int(idx) + 2  # header + 1-based
        try:
            biz_name = str(row.get("사업자", "") or "").strip() or (default_business or "")
            if not biz_name:
                raise ValueError("사업자 정보가 없습니다.")
            business = storage.get_or_create_business(biz_name)

            code = str(row.get("종목코드", "") or "").strip()
            if code.lower() in {"nan", "none"}:
                code = ""
            # 종목코드가 숫자로 읽힌 경우 보정
            if code.endswith(".0"):
                code = code[:-2]
            code = code.zfill(6) if code.isdigit() and len(code) <= 6 else code
            name = str(row.get("종목명", "") or "").strip()
            if name.lower() in {"nan", "none"}:
                name = ""

            if code:
                stock = storage.get_or_create_stock(
                    code,
                    name or code,
                    business_id=int(business.id),
                    market=mkt,
                )
            elif name:
                stock = storage.get_or_create_stock_by_name(
                    name, business_id=int(business.id), market=mkt
                )
            else:
                raise ValueError("종목코드 또는 종목명이 필요합니다.")

            side = normalize_side(row["거래유형"])
            qty = _to_float(row["수량"])
            price = _to_float(row["단가"])
            fee = _to_float(row.get("수수료", 0))
            tax = _to_float(row.get("제세금", 0)) if "제세금" in work.columns else 0.0
            settlement = None
            if "정산금액" in work.columns and not pd.isna(row.get("정산금액")):
                settlement = _to_float(row.get("정산금액"))
            memo = str(row.get("메모", "") or "").strip()

            # 환율: 비어 있으면 추정하지 않고 0 등록
            fx_raw = row.get("환율") if "환율" in work.columns else None
            if fx_raw is None and "적용환율" in work.columns:
                fx_raw = row.get("적용환율")
            fx_rate = coerce_fx_rate(fx_raw)
            price_fx = (
                _to_float(row.get("외화단가", 0))
                if "외화단가" in work.columns
                else 0.0
            )
            fee_fx = (
                _to_float(row.get("외화수수료", 0))
                if "외화수수료" in work.columns
                else 0.0
            )
            tax_fx = (
                _to_float(row.get("외화제세금", 0))
                if "외화제세금" in work.columns
                else 0.0
            )
            currency = "KRW"
            if "통화" in work.columns:
                currency = normalize_currency(str(row.get("통화") or "KRW"))
            elif "통화코드" in work.columns:
                currency = normalize_currency(str(row.get("통화코드") or "KRW"))

            if qty <= 0:
                raise ValueError("수량은 0보다 커야 합니다.")

            broker_raw = ""
            if "증권사" in work.columns:
                broker_raw = str(row.get("증권사") or "").strip()
            broker_name = broker_name_from_source(source, broker_raw)
            account_id = None
            account_name = ""
            if broker_name:
                acct = storage.get_or_create_account(
                    broker_name,
                    int(business.id),  # type: ignore[arg-type]
                    market=mkt,
                )
                account_id = int(acct.id) if acct.id is not None else None
                account_name = acct.name

            trades.append(
                Trade(
                    id=None,
                    trade_date=_parse_date(row["거래일자"]),
                    business_id=business.id,  # type: ignore[arg-type]
                    stock_id=stock.id,  # type: ignore[arg-type]
                    side=side,
                    quantity=qty,
                    price=price,
                    fee=fee,
                    tax=tax if side == "SELL" else 0.0,
                    settlement_amount=settlement,
                    memo=memo,
                    source=source,
                    created_at=now_str(),
                    business_name=business.name,
                    stock_code=stock.code,
                    stock_name=stock.name,
                    currency=currency,
                    fx_rate=fx_rate,
                    price_fx=price_fx,
                    fee_fx=fee_fx,
                    tax_fx=tax_fx,
                    account_id=account_id,
                    account_name=account_name,
                )
            )
        except Exception as exc:  # noqa: BLE001 - row-level collect
            errors.append(f"{row_no}행: {exc}")

    return trades, errors


def import_standard_file(
    file_bytes: bytes,
    filename: str,
    storage: Storage,
    *,
    default_business: str | None = None,
    market: str = "domestic",
) -> tuple[int, list[str]]:
    df = read_tabular(file_bytes, filename)
    trades, errors = dataframe_to_trades(
        df,
        storage,
        default_business=default_business,
        source="csv",
        market=market,
    )
    if trades:
        storage.add_trades_bulk(trades)
    return len(trades), errors


def trades_to_dataframe(trades: list[Trade]) -> pd.DataFrame:
    rows = []
    for t in trades:
        side_label = (
            "매수" if t.side == "BUY" else ("매도" if t.side == "SELL" else "배당")
        )
        qty = float(t.quantity or 0)
        price = float(t.price or 0)
        rows.append(
            {
                "거래일자": t.trade_date,
                "사업자": t.business_name,
                "증권사": getattr(t, "account_name", "") or "",
                "종목코드": t.stock_code,
                "종목명": t.stock_name,
                "거래유형": side_label,
                "수량": qty,
                "거래금액(원가)": qty * price,
                "단가": price,
                "수수료": t.fee,
                "제세금": getattr(t, "tax", 0) or 0,
                "통화": getattr(t, "currency", "") or "",
                "환율": getattr(t, "fx_rate", 0) or 0,
                "외화단가": getattr(t, "price_fx", 0) or 0,
                "정산금액": t.settlement_amount
                if t.settlement_amount is not None
                else (
                    qty * price + t.fee
                    if t.side == "BUY"
                    else qty * price - t.fee
                ),
                "메모": t.memo,
                "출처": t.source,
                "ID": t.id,
            }
        )
    # STANDARD_COLUMNS + 표시용 원가 컬럼 + 메타
    display_cols = [
        "거래일자",
        "사업자",
        "증권사",
        "종목코드",
        "종목명",
        "거래유형",
        "수량",
        "거래금액(원가)",
        "단가",
        "수수료",
        "제세금",
        "정산금액",
        "메모",
        "출처",
        "ID",
    ]
    # 외화 메타 포함 (해외주식 상세용)
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(
            columns=display_cols + ["통화", "환율", "외화단가"]
        )
    extra = [c for c in ("통화", "환율", "외화단가") if c in df.columns]
    ordered = [c for c in display_cols if c in df.columns] + extra
    return df.loc[:, ordered]


def positions_to_dataframe(positions) -> pd.DataFrame:
    rows = []
    for p in positions:
        if p.quantity <= 1e-12 and abs(p.realized_pnl) < 1e-9:
            continue
        rows.append(
            {
                "사업자": p.business_name,
                "증권사": getattr(p, "account_name", "") or "미지정",
                "종목코드": p.stock_code,
                "종목명": p.stock_name,
                "잔여수량": p.quantity,
                "평단가": round(p.avg_price, 4),
                "원가잔액": round(p.total_cost, 2),
                "누적실현손익": round(p.realized_pnl, 2),
            }
        )
    return pd.DataFrame(rows)


def sell_results_to_dataframe(sell_results) -> pd.DataFrame:
    rows = []
    for s in sell_results:
        rows.append(
            {
                "거래일자": s.trade_date,
                "사업자": s.business_name,
                "증권사": getattr(s, "account_name", "") or "미지정",
                "종목코드": s.stock_code,
                "종목명": s.stock_name,
                "매도수량": s.quantity,
                "매도단가": s.price,
                "수수료": s.fee,
                "매수원가": round(getattr(s, "book_cost", 0.0) or 0.0, 2),
                "처분손익": round(
                    getattr(s, "disposal_pnl", s.realized_pnl) or 0.0, 2
                ),
                "FIFO실현손익": round(s.realized_pnl, 2),
                "부족수량": s.shortfall_qty,
                "매칭건수": len(s.matches),
            }
        )
    return pd.DataFrame(rows)


def export_to_excel_bytes(
    trades_df: pd.DataFrame,
    positions_df: pd.DataFrame,
    sells_df: pd.DataFrame,
    *,
    text_columns: list[str] | None = None,
) -> bytes:
    """거래/잔고/실현손익 시트를 담은 xlsx bytes.

    text_columns: 적요·메모처럼 공백 패딩을 유지해야 하는 텍스트 열.
    """
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        trades_df.to_excel(writer, sheet_name="거래내역", index=False)
        positions_df.to_excel(writer, sheet_name="보유잔고", index=False)
        sells_df.to_excel(writer, sheet_name="실현손익", index=False)
        if text_columns and "거래내역" in writer.sheets:
            ws = writer.sheets["거래내역"]
            headers = {
                cell.value: idx
                for idx, cell in enumerate(ws[1], start=1)
                if cell.value is not None
            }
            for col_name in text_columns:
                col_idx = headers.get(col_name)
                if not col_idx:
                    continue
                for row in range(2, ws.max_row + 1):
                    cell = ws.cell(row=row, column=col_idx)
                    if cell.value is None:
                        continue
                    cell.number_format = "@"
                    cell.value = str(cell.value)
    return bio.getvalue()


def export_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")
