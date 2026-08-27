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
                # low_memory=False + engine=c 로 대용량 CSV 읽기 속도 향상
                return pd.read_csv(bio, encoding=enc, low_memory=False)
            except UnicodeDecodeError:
                continue
        bio.seek(0)
        return pd.read_csv(bio, low_memory=False)
    if ext in {"xlsx", "xls"} or name.endswith((".xlsx", ".xls")):
        engine = "xlrd" if ext == "xls" or name.endswith(".xls") else "openpyxl"
        return pd.read_excel(bio, engine=engine, dtype=str)
    if ext == "pdf" or name.endswith(".pdf"):
        raise ValueError(
            "PDF는 read_tabular로 처리할 수 없습니다. PDF 전용 파서를 사용하세요."
        )
    raise ValueError(f"지원하지 않는 파일 형식입니다: {ext or name} (지원: csv, xlsx, xls)")


def _series_clean_code(series: pd.Series) -> pd.Series:
    s = series.fillna("").astype(str).str.strip()
    s = s.replace({"nan": "", "None": "", "none": ""})
    s = s.str.replace(r"\.0$", "", regex=True)
    # 숫자 코드만 6자리 zero-pad
    mask = s.str.fullmatch(r"\d{1,6}")
    s = s.where(~mask, s.str.zfill(6))
    return s


def _series_to_float(series: pd.Series, default: float = 0.0) -> pd.Series:
    s = series
    if s.dtype == object or str(s.dtype) == "string":
        s = (
            s.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("원", "", regex=False)
            .str.strip()
            .replace({"": default, "-": default, "nan": default, "None": default, "none": default})
        )
    return pd.to_numeric(s, errors="coerce").fillna(default)


def dataframe_to_trades(
    df: pd.DataFrame,
    storage: Storage,
    *,
    default_business: str | None = None,
    source: str = "csv",
    market: str = "domestic",
) -> tuple[list[Trade], list[str]]:
    """표준/유사 컬럼 DataFrame을 Trade 리스트로 변환하고 종목/사업자를 자동 생성.

    대용량 최적화:
    - 사업자/증권사/종목을 행마다 DB 조회하지 않고 메모리 캐시 + stocks 일괄 insert
    - 수치/코드 컬럼은 pandas 벡터 연산으로 전처리
    """
    from .models import normalize_market

    mkt = normalize_market(market)
    work = _normalize_columns(df.copy())
    required = ["거래일자", "거래유형", "수량", "단가"]
    missing = [c for c in required if c not in work.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {', '.join(missing)}")
    if "종목코드" not in work.columns and "종목명" not in work.columns:
        raise ValueError("종목코드 또는 종목명 컬럼이 필요합니다.")

    # --- 벡터 전처리 ---
    n = len(work)
    if n == 0:
        return [], []

    if "사업자" in work.columns:
        biz_series = (
            work["사업자"].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
        )
        biz_series = biz_series.where(biz_series != "", default_business or "")
    else:
        biz_series = pd.Series([default_business or ""] * n, index=work.index)

    code_series = (
        _series_clean_code(work["종목코드"])
        if "종목코드" in work.columns
        else pd.Series([""] * n, index=work.index)
    )
    name_series = (
        work["종목명"].fillna("").astype(str).str.strip().replace({"nan": "", "None": "", "none": ""})
        if "종목명" in work.columns
        else pd.Series([""] * n, index=work.index)
    )
    qty_series = _series_to_float(work["수량"])
    price_series = _series_to_float(work["단가"])
    fee_series = (
        _series_to_float(work["수수료"]) if "수수료" in work.columns else pd.Series(0.0, index=work.index)
    )
    tax_series = (
        _series_to_float(work["제세금"]) if "제세금" in work.columns else pd.Series(0.0, index=work.index)
    )
    if "정산금액" in work.columns:
        settle_series = _series_to_float(work["정산금액"])
        settle_na = work["정산금액"].isna()
    else:
        settle_series = pd.Series(0.0, index=work.index)
        settle_na = pd.Series(True, index=work.index)

    memo_series = (
        work["메모"].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
        if "메모" in work.columns
        else pd.Series([""] * n, index=work.index)
    )

    fx_col = None
    if "환율" in work.columns:
        fx_col = "환율"
    elif "적용환율" in work.columns:
        fx_col = "적용환율"
    fx_series = (
        work[fx_col].map(coerce_fx_rate) if fx_col else pd.Series(0.0, index=work.index)
    )
    price_fx_series = (
        _series_to_float(work["외화단가"]) if "외화단가" in work.columns else pd.Series(0.0, index=work.index)
    )
    fee_fx_series = (
        _series_to_float(work["외화수수료"]) if "외화수수료" in work.columns else pd.Series(0.0, index=work.index)
    )
    tax_fx_series = (
        _series_to_float(work["외화제세금"]) if "외화제세금" in work.columns else pd.Series(0.0, index=work.index)
    )
    if "통화" in work.columns:
        currency_series = work["통화"].fillna("KRW").astype(str).map(normalize_currency)
    elif "통화코드" in work.columns:
        currency_series = work["통화코드"].fillna("KRW").astype(str).map(normalize_currency)
    else:
        currency_series = pd.Series(["KRW"] * n, index=work.index)

    broker_series = (
        work["증권사"].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
        if "증권사" in work.columns
        else pd.Series([""] * n, index=work.index)
    )

    # 날짜 일괄 파싱
    date_parsed = pd.to_datetime(work["거래일자"], errors="coerce")

    # --- 캐시: 사업자 / 증권사 / 종목 ---
    biz_cache: dict[str, Any] = {}
    acct_cache: dict[tuple[int, str], Any] = {}  # (business_id, broker_name) -> Account
    stock_by_biz_code: dict[int, dict[str, Any]] = {}  # business_id -> {code: Stock}
    stock_by_biz_name: dict[int, dict[str, Any]] = {}  # business_id -> {name: Stock}

    # 사업자 선확보 (고유값만)
    for biz_name in sorted({str(x).strip() for x in biz_series.tolist() if str(x).strip()}):
        biz_cache[biz_name] = storage.get_or_create_business(biz_name)

    # 코드 있는 종목 일괄 확보 (사업자별)
    from collections import defaultdict

    pending_stocks: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for biz_name, code, name in zip(biz_series.tolist(), code_series.tolist(), name_series.tolist()):
        biz_name = str(biz_name or "").strip()
        if not biz_name or biz_name not in biz_cache:
            continue
        code = str(code or "").strip()
        name = str(name or "").strip()
        if code:
            bid = int(biz_cache[biz_name].id)
            pending_stocks[bid].append((code, name or code))

    for bid, items in pending_stocks.items():
        stock_by_biz_code[bid] = storage.ensure_stocks_bulk(
            items, business_id=bid, market=mkt
        )

    # 증권사 선확보
    for biz_name, broker_raw in zip(biz_series.tolist(), broker_series.tolist()):
        biz_name = str(biz_name or "").strip()
        if not biz_name or biz_name not in biz_cache:
            continue
        broker_name = broker_name_from_source(source, str(broker_raw or ""))
        if not broker_name:
            continue
        bid = int(biz_cache[biz_name].id)
        key = (bid, broker_name)
        if key not in acct_cache:
            acct_cache[key] = storage.get_or_create_account(
                broker_name, bid, market=mkt
            )

    errors: list[str] = []
    trades: list[Trade] = []
    created_at = now_str()

    for i, idx in enumerate(work.index):
        row_no = int(idx) + 2 if isinstance(idx, (int, float)) else i + 2
        try:
            biz_name = str(biz_series.iloc[i] or "").strip()
            if not biz_name:
                raise ValueError("사업자 정보가 없습니다.")
            business = biz_cache.get(biz_name)
            if business is None:
                business = storage.get_or_create_business(biz_name)
                biz_cache[biz_name] = business
            bid = int(business.id)

            code = str(code_series.iloc[i] or "").strip()
            name = str(name_series.iloc[i] or "").strip()

            stock = None
            if code:
                code_map = stock_by_biz_code.get(bid)
                if code_map is None:
                    code_map = storage.ensure_stocks_bulk(
                        [(code, name or code)], business_id=bid, market=mkt
                    )
                    stock_by_biz_code[bid] = code_map
                stock = code_map.get(code)
                if stock is None:
                    stock = storage.get_or_create_stock(
                        code, name or code, business_id=bid, market=mkt
                    )
                    code_map[code] = stock
            elif name:
                name_map = stock_by_biz_name.setdefault(bid, {})
                stock = name_map.get(name)
                if stock is None:
                    stock = storage.get_or_create_stock_by_name(
                        name, business_id=bid, market=mkt
                    )
                    name_map[name] = stock
                    if stock.code:
                        stock_by_biz_code.setdefault(bid, {})[stock.code] = stock
            else:
                raise ValueError("종목코드 또는 종목명이 필요합니다.")

            side = normalize_side(work["거래유형"].iloc[i])
            qty = float(qty_series.iloc[i])
            price = float(price_series.iloc[i])
            if qty <= 0:
                raise ValueError("수량은 0보다 커야 합니다.")

            fee = float(fee_series.iloc[i])
            tax = float(tax_series.iloc[i]) if side == "SELL" else 0.0
            settlement = None if bool(settle_na.iloc[i]) else float(settle_series.iloc[i])

            ts = date_parsed.iloc[i]
            if pd.isna(ts):
                raise ValueError(f"날짜 파싱 실패: {work['거래일자'].iloc[i]}")
            trade_date = ts.strftime("%Y-%m-%d")

            broker_name = broker_name_from_source(source, str(broker_series.iloc[i] or ""))
            account_id = None
            account_name = ""
            if broker_name:
                acct = acct_cache.get((bid, broker_name))
                if acct is None:
                    acct = storage.get_or_create_account(broker_name, bid, market=mkt)
                    acct_cache[(bid, broker_name)] = acct
                account_id = int(acct.id) if acct.id is not None else None
                account_name = acct.name

            trades.append(
                Trade(
                    id=None,
                    trade_date=trade_date,
                    business_id=business.id,  # type: ignore[arg-type]
                    stock_id=stock.id,  # type: ignore[arg-type]
                    side=side,
                    quantity=qty,
                    price=price,
                    fee=fee,
                    tax=tax,
                    settlement_amount=settlement,
                    memo=str(memo_series.iloc[i] or ""),
                    source=source,
                    created_at=created_at,
                    business_name=business.name,
                    stock_code=stock.code,
                    stock_name=stock.name,
                    currency=str(currency_series.iloc[i] or "KRW"),
                    fx_rate=float(fx_series.iloc[i] or 0),
                    price_fx=float(price_fx_series.iloc[i] or 0),
                    fee_fx=float(fee_fx_series.iloc[i] or 0),
                    tax_fx=float(tax_fx_series.iloc[i] or 0),
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
