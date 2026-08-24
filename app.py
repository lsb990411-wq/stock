"""법인 주식 매매일지 및 잔고 관리 웹앱 (Streamlit)."""

from __future__ import annotations

import html
import locale
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

try:
    locale.setlocale(locale.LC_ALL, "ko_KR.UTF-8")
except Exception:
    try:
        locale.setlocale(locale.LC_ALL, "Korean_Korea.949")
    except Exception:
        pass

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.brokers.pdf_parser import OCR_TIP, empty_trade_rows
from src.legacy_journal import parse_legacy_journal_excel

# Streamlit 핫리로드 시 stale sys.modules 방지
import importlib

import src.brokers as _brokers_mod
import src.brokers.detector as _brokers_detector_mod
import src.brokers.identify as _brokers_identify_mod
import src.brokers.kb_overseas as _kb_overseas_mod
import src.brokers.meritz_overseas as _meritz_overseas_mod
import src.brokers.mirae_overseas as _mirae_overseas_mod
import src.models as _models_mod
import src.storage as _storage_mod
import src.fifo as _fifo_mod
import src.voucher_export as _voucher_mod
import src.income_parser as _income_parser_mod
import src.income_voucher as _income_voucher_mod
import src.import_export as _import_export_mod

# 하위 모듈부터 리로드 (detector가 kb/meritz를 참조)
_brokers_identify_mod = importlib.reload(_brokers_identify_mod)
_kb_overseas_mod = importlib.reload(_kb_overseas_mod)
_meritz_overseas_mod = importlib.reload(_meritz_overseas_mod)
_mirae_overseas_mod = importlib.reload(_mirae_overseas_mod)
_brokers_detector_mod = importlib.reload(_brokers_detector_mod)
_brokers_mod = importlib.reload(_brokers_mod)
_models_mod = importlib.reload(_models_mod)
_storage_mod = importlib.reload(_storage_mod)
_fifo_mod = importlib.reload(_fifo_mod)
_voucher_mod = importlib.reload(_voucher_mod)
_income_parser_mod = importlib.reload(_income_parser_mod)
_income_voucher_mod = importlib.reload(_income_voucher_mod)
_import_export_mod = importlib.reload(_import_export_mod)

detect_and_parse = _brokers_mod.detect_and_parse
detect_and_parse_overseas = _brokers_mod.detect_and_parse_overseas
list_brokers = _brokers_mod.list_brokers
parse_overseas_with_hint = _brokers_mod.parse_overseas_with_hint
AccountConfig = _models_mod.AccountConfig
IncomeAccountConfig = _models_mod.IncomeAccountConfig
Trade = _models_mod.Trade
MARKET_DOMESTIC = _models_mod.MARKET_DOMESTIC
MARKET_OVERSEAS = _models_mod.MARKET_OVERSEAS
FX_CURRENCIES = _models_mod.FX_CURRENCIES
normalize_market = _models_mod.normalize_market
normalize_currency = _models_mod.normalize_currency
normalize_side = _models_mod.normalize_side
coerce_fx_rate = _models_mod.coerce_fx_rate
now_str = _models_mod.now_str
Storage = _storage_mod.Storage
compute_positions = _fifo_mod.compute_positions
sells_by_trade_id = _fifo_mod.sells_by_trade_id
aggregate_positions_by_stock = _fifo_mod.aggregate_positions_by_stock
buy_lot_remainders = _fifo_mod.buy_lot_remainders
export_voucher_excel_bytes = _voucher_mod.export_voucher_excel_bytes
trades_to_voucher_lines = _voucher_mod.trades_to_voucher_lines
parse_income_file = _income_parser_mod.parse_income_file
rows_to_dataframe = _income_parser_mod.rows_to_dataframe
apply_income_fx_rates = _income_parser_mod.apply_income_fx_rates
ensure_income_preview_columns = _income_parser_mod.ensure_income_preview_columns
export_income_voucher_excel_bytes = _income_voucher_mod.export_income_voucher_excel_bytes
income_to_voucher_lines = _income_voucher_mod.income_to_voucher_lines
dataframe_to_trades = _import_export_mod.dataframe_to_trades
export_to_csv_bytes = _import_export_mod.export_to_csv_bytes
export_to_excel_bytes = _import_export_mod.export_to_excel_bytes
positions_to_dataframe = _import_export_mod.positions_to_dataframe
sell_results_to_dataframe = _import_export_mod.sell_results_to_dataframe
trades_to_dataframe = _import_export_mod.trades_to_dataframe
broker_name_from_source = _import_export_mod.broker_name_from_source

st.set_page_config(
    page_title="법인 주식 매매일지",
    page_icon="📈",
    layout="wide",
)

# 사이드바 트리 메뉴 — 모던 SaaS 스타일
SIDEBAR_CUSTOM_CSS = """
<style>
    /* 사이드바 Expander 테두리 및 배경 깔끔하게 정리 */
    [data-testid="stSidebar"] [data-testid="stExpander"] {
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        margin-bottom: 0.5rem !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] details {
        border: none !important;
        background: transparent !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] summary {
        font-weight: 700 !important;
        font-size: 0.95rem !important;
        color: #1f2937 !important;
        padding: 0.6rem 0.4rem !important;
        border-radius: 8px !important;
        list-style: none !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
        background-color: #f3f4f6 !important;
    }

    /* 트리 하위 메뉴 버튼만 대상 (사업자 추가/삭제 버튼 제외) */
    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button {
        width: 100% !important;
        border: none !important;
        border-width: 0 !important;
        box-shadow: none !important;
        background-color: transparent !important;
        color: #4b5563 !important;
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 0.5rem 0.8rem !important;
        border-radius: 6px !important;
        font-size: 0.88rem !important;
        font-weight: 500 !important;
        transition: all 0.2s ease !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button:hover {
        background-color: #f3f4f6 !important;
        color: #111827 !important;
        border: none !important;
        box-shadow: none !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button:focus,
    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button:active {
        box-shadow: none !important;
        outline: none !important;
    }

    /* 활성화된 메뉴 (primary) */
    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button[kind="primary"],
    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button[data-testid="baseButton-primary"] {
        background-color: #eff6ff !important;
        color: #2563eb !important;
        font-weight: 700 !important;
        border: none !important;
        border-left: 3px solid #2563eb !important;
        border-radius: 0 6px 6px 0 !important;
        box-shadow: none !important;
    }

    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button[kind="primary"]:hover,
    [data-testid="stSidebar"] [data-testid="stExpander"] .stButton > button[data-testid="baseButton-primary"]:hover {
        background-color: #dbeafe !important;
        color: #1d4ed8 !important;
    }
</style>
"""


def inject_sidebar_styles() -> None:
    st.markdown(SIDEBAR_CUSTOM_CSS, unsafe_allow_html=True)


STORAGE_CACHE_VERSION = 28  # Storage API 변경 시 증가 → 캐시 무효화


@st.cache_resource
def _storage_singleton(_version: int) -> object:
    """버전 키가 바뀌면 models→storage 순으로 재로드 후 인스턴스를 만든다."""
    import importlib

    import src.models as models_mod
    import src.storage as storage_mod
    import src.supabase_client as supabase_mod

    importlib.reload(models_mod)
    importlib.reload(supabase_mod)
    try:
        supabase_mod.reset_supabase_client()
    except Exception:  # noqa: BLE001
        pass
    storage_mod = importlib.reload(storage_mod)
    return storage_mod.Storage()


def get_storage() -> Storage:
    """Supabase Storage. 핫리로드로 모델/메서드가 어긋나면 강제 갱신한다."""
    import importlib
    import inspect
    import types

    import src.models as models_mod
    import src.storage as storage_mod

    storage = _storage_singleton(STORAGE_CACHE_VERSION)
    required = (
        "update_trade_date",
        "update_trade_disposal_pnl",
        "get_all_stocks",
        "get_all_accounts",
        "delete_account",
        "get_or_create_account",
        "get_account_by_name",
        "list_accounts",
        "add_account",
        "get_trades_by_period",
        "get_account_config",
        "save_account_config",
        "update_stock_partner_codes",
        "get_income_account_config",
        "save_income_account_config",
        "list_income_records",
        "get_income_records_by_period",
        "list_broker_partners",
        "clear_trades_for_business",
        "clear_income_records_for_business",
    )
    biz_ok = True
    stocks_ok = False
    try:
        # Business에 code 필드가 있는지 런타임 확인
        from dataclasses import fields as dc_fields

        biz_fields = {f.name for f in dc_fields(models_mod.Business)}
        biz_ok = "code" in biz_fields and "account_no" in biz_fields
    except Exception:  # noqa: BLE001
        biz_ok = False

    try:
        # get_all_stocks(business_id=..., market=...) 시그니처 확인 (구버전 캐시 감지)
        sig = inspect.signature(type(storage).get_all_stocks)
        stocks_ok = "business_id" in sig.parameters and "market" in sig.parameters
        lt = inspect.signature(type(storage).list_trades)
        stocks_ok = stocks_ok and "market" in lt.parameters
        ac = inspect.signature(type(storage).get_account_config)
        stocks_ok = stocks_ok and "market" in ac.parameters
    except Exception:  # noqa: BLE001
        stocks_ok = False

    if (
        biz_ok
        and stocks_ok
        and all(callable(getattr(storage, name, None)) for name in required)
    ):
        return storage  # type: ignore[return-value]

    importlib.reload(models_mod)
    storage_mod = importlib.reload(storage_mod)
    Latest = storage_mod.Storage
    _storage_singleton.clear()
    storage = _storage_singleton(STORAGE_CACHE_VERSION)

    for name in required:
        if not callable(getattr(storage, name, None)) and callable(
            getattr(Latest, name, None)
        ):
            setattr(
                storage,
                name,
                types.MethodType(getattr(Latest, name), storage),
            )
    return storage  # type: ignore[return-value]


def money(v: float) -> str:
    return f"{v:,.0f}"


def trade_table_column_config() -> dict:
    """거래 내역 테이블 숫자 컬럼 천단위 쉼표 포맷."""
    return {
        "수량": st.column_config.NumberColumn("수량", format="%,d"),
        "거래금액(원가)": st.column_config.NumberColumn(
            "거래금액(원가)", format="%,d 원", help="수량 × 단가"
        ),
        "단가": st.column_config.NumberColumn("단가", format="%,d 원"),
        "수수료": st.column_config.NumberColumn("수수료", format="%,d 원"),
        "제세금": st.column_config.NumberColumn("제세금", format="%,d 원"),
        "정산금액": st.column_config.NumberColumn("정산금액", format="%,d 원"),
        "처분손익": st.column_config.NumberColumn("처분손익", format="%,d 원"),
        "ID": st.column_config.NumberColumn("ID", format="%d"),
    }


def _attach_disposal_pnl(df: pd.DataFrame, sells) -> pd.DataFrame:
    """매도 행에 FIFO 처분손익(전표와 동일 산식)을 붙인다."""
    if df is None or df.empty or "ID" not in df.columns:
        return df
    by_id = sells_by_trade_id(sells)
    values: list[float | None] = []
    for tid in df["ID"]:
        try:
            if tid is None or (isinstance(tid, float) and pd.isna(tid)):
                values.append(None)
                continue
            sell = by_id.get(int(tid))
            if sell is None:
                values.append(None)
            else:
                values.append(
                    round(float(getattr(sell, "disposal_pnl", sell.realized_pnl) or 0), 0)
                )
        except (TypeError, ValueError):
            values.append(None)
    out = df.copy()
    out["처분손익"] = values
    return out


def _filter_stock_trades(
    trades: list,
    *,
    stock_name: str,
    business_name: str = "",
    stock_code: str = "",
) -> pd.DataFrame:
    trades_df = trades_to_dataframe(trades)
    if trades_df.empty:
        return trades_df
    detail = trades_df.copy()
    name = (stock_name or "").strip()
    code = (stock_code or "").strip().upper()
    if "종목명" in detail.columns and name:
        names = detail["종목명"].astype(str).str.strip()
        mask = (names == name) | (names.str.upper() == name.upper())
        if code and "종목코드" in detail.columns:
            codes = detail["종목코드"].astype(str).str.strip().str.upper()
            mask = mask | (codes == code)
        detail = detail[mask]
    elif code and "종목코드" in detail.columns:
        codes = detail["종목코드"].astype(str).str.strip().str.upper()
        detail = detail[codes == code]
    if business_name and "사업자" in detail.columns:
        biz = business_name.strip()
        biz_col = detail["사업자"].astype(str).str.strip()
        detail = detail[biz_col == biz]
    detail = detail.drop(columns=["출처"], errors="ignore")
    # 핫리로드 stale 모듈 대비: 원가 컬럼 없으면 수량×단가로 즉시 생성
    if "거래금액(원가)" not in detail.columns and {"수량", "단가"}.issubset(detail.columns):
        detail["거래금액(원가)"] = (
            pd.to_numeric(detail["수량"], errors="coerce").fillna(0)
            * pd.to_numeric(detail["단가"], errors="coerce").fillna(0)
        )
    for col in ("수량", "거래금액(원가)", "단가", "수수료", "제세금", "정산금액", "처분손익", "ID"):
        if col in detail.columns:
            detail[col] = pd.to_numeric(detail[col], errors="coerce")

    # 팝업: 시간순 정렬 (거래일자 → 거래일시 → ID)
    detail = _sort_trades_chronologically(detail)
    return detail.reset_index(drop=True)


def _sort_trades_chronologically(df: pd.DataFrame) -> pd.DataFrame:
    """거래일자·거래일시(있으면)·매수우선·ID 기준 오름차순.

    DB에 거래일시가 없어도, 시간순으로 넣은 거래는 ID 순서가 시간순에 대응한다.
    동일 일자에는 매수가 매도보다 먼저 오도록 한다.
    """
    if df is None or df.empty:
        return df
    work = df.copy()
    sort_by: list[str] = []
    drop_tmp: list[str] = []

    if "거래일자" in work.columns:
        work["_sort_date"] = pd.to_datetime(work["거래일자"], errors="coerce")
        sort_by.append("_sort_date")
        drop_tmp.append("_sort_date")

    if "거래일시" in work.columns:
        work["_sort_time"] = (
            work["거래일시"].fillna("00:00:00").astype(str).str.strip()
        )
        work.loc[
            work["_sort_time"].isin({"", "nan", "None", "NaT"}),
            "_sort_time",
        ] = "00:00:00"
        sort_by.append("_sort_time")
        drop_tmp.append("_sort_time")

    # 매수(0) → 매도(1)
    side_col = "거래유형" if "거래유형" in work.columns else None
    if side_col:
        work["_sort_side"] = work[side_col].map(
            lambda v: 0
            if ("매수" in str(v) or "매입" in str(v) or "입고" in str(v))
            else 1
        )
        sort_by.append("_sort_side")
        drop_tmp.append("_sort_side")

    if "ID" in work.columns:
        sort_by.append("ID")

    if not sort_by:
        return work

    work = work.sort_values(by=sort_by, ascending=True, kind="mergesort")
    return work.drop(columns=drop_tmp, errors="ignore")


DETAIL_TRADE_COLUMNS = [
    "거래일자",
    "종목명",
    "거래유형",
    "수량",
    "잔여수량",
    "매수단가(외화)",
    "매수단가(원화)",
    "거래금액(외화)",
    "거래금액(원화)",
    "단가",
    "수수료",
    "제세금",
    "정산금액",
    "처분손익",
    "메모",
]


def _attach_fifo_remainders(df: pd.DataFrame, trades: list) -> pd.DataFrame:
    """매수 행에 FIFO 잔여수량·매수단가(외화/원화) 컬럼을 붙인다.

    매도·배당 행은 매수단가 컬럼을 None으로 둔다.
    """
    if df is None or df.empty or "ID" not in df.columns:
        return df
    rem_map = buy_lot_remainders(trades)
    out = df.copy()
    rem_qty: list[float | None] = []
    buy_fx: list[float | None] = []
    buy_krw: list[float | None] = []
    for _, row in out.iterrows():
        side = str(row.get("거래유형") or "")
        try:
            tid = int(row["ID"]) if pd.notna(row.get("ID")) else 0
        except (TypeError, ValueError):
            tid = 0
        info = rem_map.get(tid)
        if "매수" in side and info is not None:
            rem_qty.append(round(float(info["remaining_qty"]), 4))
            krw = float(info["buy_price"])
            fx_px = float(info.get("buy_price_fx") or 0)
            # FIFO 맵에 외화단가가 없으면 행의 외화단가/환율로 보완
            if fx_px <= 0:
                fx_px = float(pd.to_numeric(row.get("외화단가"), errors="coerce") or 0)
            if fx_px <= 0:
                rate = float(pd.to_numeric(row.get("환율"), errors="coerce") or 0)
                if rate > 0 and krw > 0:
                    fx_px = krw / rate
            buy_fx.append(round(fx_px, 6) if fx_px > 0 else None)
            buy_krw.append(round(krw, 4) if krw > 0 else None)
        else:
            rem_qty.append(None)
            buy_fx.append(None)
            buy_krw.append(None)
    out["잔여수량"] = rem_qty
    out["매수단가(외화)"] = buy_fx
    out["매수단가(원화)"] = buy_krw
    out.drop(columns=["매수단가"], errors="ignore", inplace=True)
    return out


def _attach_trade_amounts(df: pd.DataFrame) -> pd.DataFrame:
    """거래금액(외화/원화) 컬럼 부착.

    - 거래금액(외화) = 수량 × 외화단가 (표시: '1,234.5600 USD')
    - 거래금액(원화) = 수량 × 외화단가 × 환율 (수수료 제외)
      외화단가·환율이 없으면 수량 × 원화단가
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    disp_fx: list[str | None] = []
    amt_krw: list[float | None] = []

    for _, row in out.iterrows():
        qty = float(pd.to_numeric(row.get("수량"), errors="coerce") or 0)
        price_fx = float(pd.to_numeric(row.get("외화단가"), errors="coerce") or 0)
        fx_rate = float(pd.to_numeric(row.get("환율"), errors="coerce") or 0)
        price_krw = float(pd.to_numeric(row.get("단가"), errors="coerce") or 0)
        ccy = str(row.get("통화") or "").strip().upper() or "USD"
        side = str(row.get("거래유형") or "")

        if qty <= 0 and "배당" not in side:
            disp_fx.append(None)
            amt_krw.append(None)
            continue

        if price_fx > 0:
            fx_amt = qty * price_fx
            disp_fx.append(f"{fx_amt:,.4f} {ccy}")
            if fx_rate > 0:
                amt_krw.append(round(qty * price_fx * fx_rate, 0))
            elif price_krw > 0:
                amt_krw.append(round(qty * price_krw, 0))
            else:
                amt_krw.append(None)
        else:
            # 국내주식 등 외화 없음
            disp_fx.append(None)
            if price_krw > 0 and qty > 0:
                amt_krw.append(round(qty * price_krw, 0))
            elif "거래금액(원가)" in out.columns:
                legacy = float(pd.to_numeric(row.get("거래금액(원가)"), errors="coerce") or 0)
                amt_krw.append(round(legacy, 0) if legacy else None)
            else:
                amt_krw.append(None)

    out["거래금액(외화)"] = disp_fx
    out["거래금액(원화)"] = amt_krw
    return out



def _normalize_trade_date(value) -> str | None:
    """data_editor 날짜 값을 YYYY-MM-DD 문자열로 변환."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat()[:10]
        except Exception:  # noqa: BLE001
            pass
    text = str(value).strip()
    if not text or text.lower() == "nat":
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _dismiss_stock_detail_modal() -> None:
    """종목 상세 다이얼로그 닫힘 시 pending 제거 (query clear 후 rerun에서도 유지하다가 해제)."""
    st.session_state.pop("_pending_stock_detail", None)
    st.session_state.pop("_stock_detail_pnl_toast", None)
    st.session_state.pop("_pnl_editor_dirty", None)
    # 처분손익 에디터/기준선 세션 잔여 정리 (위젯 키는 버전으로 무효화)
    for key in list(st.session_state.keys()):
        sk = str(key)
        if (
            sk.startswith("stock_detail_pnl_editor_")
            or sk.startswith("_pnl_baseline_")
            or sk.startswith("_pnl_input_snap_")
        ):
            try:
                del st.session_state[key]
            except Exception:  # noqa: BLE001
                st.session_state.pop(key, None)


def _is_sell_side_label(side: object) -> bool:
    text = str(side or "").strip()
    return ("매도" in text) and ("배당" not in text)


def _parse_pnl_cell(raw) -> float | None:
    """data_editor 처분손익 셀 → float | None."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, str):
        text = raw.strip().replace(",", "").replace("원", "").replace(" ", "")
        if not text or text.lower() in {"none", "null", "nan", "-"}:
            return None
        try:
            return round(float(text), 0)
        except ValueError:
            return None
    try:
        if pd.isna(raw):
            return None
        return round(float(raw), 0)
    except (TypeError, ValueError):
        return None


def _pnl_values_differ(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return abs(float(a) - float(b)) >= 0.5


def _collect_disposal_pnl_updates(
    *,
    edited_df: pd.DataFrame,
    input_df: pd.DataFrame,
    widget_state: object,
    stored_override: dict[int, float | None],
) -> dict[int, float | None]:
    """에디터 반환 DF · edited_rows · 입력 스냅샷을 ID 기준으로 비교해 저장 대상 추출."""
    updates: dict[int, float | None] = {}

    # 입력 DF 기준: 행 위치 → trade id, 매도 여부, 표시 기준값
    id_by_pos: dict[int, int] = {}
    sell_ids: set[int] = set()
    input_pnl: dict[int, float | None] = {}
    for pos, (_, row) in enumerate(input_df.iterrows()):
        if pd.isna(row.get("ID")):
            continue
        tid = int(row["ID"])
        id_by_pos[pos] = tid
        if _is_sell_side_label(row.get("거래유형")):
            sell_ids.add(tid)
            input_pnl[tid] = _parse_pnl_cell(row.get("처분손익"))

    def _baseline(tid: int) -> float | None:
        # DB 오버라이드가 있으면 그것과 비교, 없으면 화면에 보여준 입력값
        if tid in stored_override and stored_override[tid] is not None:
            return stored_override[tid]
        return input_pnl.get(tid)

    def _queue(tid: int, new_val: float | None) -> None:
        if tid not in sell_ids:
            return
        stored = stored_override.get(tid)
        base = _baseline(tid)
        if new_val is None:
            # 비움 → DB 오버라이드가 있을 때만 NULL로 복귀
            if stored is not None:
                updates[tid] = None
            return
        if _pnl_values_differ(new_val, base):
            updates[tid] = new_val

    # 1) session_state EditingState.edited_rows (행 위치 → 컬럼)
    edited_rows: dict = {}
    if isinstance(widget_state, dict):
        raw_rows = widget_state.get("edited_rows") or {}
        if isinstance(raw_rows, dict):
            edited_rows = raw_rows
    for pos_key, changes in edited_rows.items():
        try:
            pos = int(pos_key)
        except (TypeError, ValueError):
            continue
        tid = id_by_pos.get(pos)
        if tid is None or not isinstance(changes, dict):
            continue
        if "처분손익" not in changes:
            continue
        _queue(tid, _parse_pnl_cell(changes.get("처분손익")))

    # 2) 반환 DataFrame — ID 컬럼으로 직접 매핑 (정렬/필터와 무관)
    if edited_df is not None and not edited_df.empty and "ID" in edited_df.columns:
        for _, row in edited_df.iterrows():
            if pd.isna(row.get("ID")):
                continue
            tid = int(row["ID"])
            if tid not in sell_ids:
                continue
            _queue(tid, _parse_pnl_cell(row.get("처분손익")))

    return updates


def _save_disposal_pnl_updates(updates: dict[int, float | None]) -> int:
    """Supabase trades.disposal_pnl 업데이트. 성공 건수 반환."""
    if not updates:
        return 0
    db = get_storage()
    saved = 0
    for tid, new_val in updates.items():
        db.update_trade_disposal_pnl(int(tid), new_val)
        saved += 1
    return saved


def _highlight_trade_type(val) -> str:
    """종목 상세 표의 거래유형 글자색."""
    text = str(val).strip() if val is not None and not (isinstance(val, float) and pd.isna(val)) else ""
    if text == "매수":
        return "color: #dc2626; font-weight: 700;"
    if text == "매도":
        return "color: #2563eb; font-weight: 700;"
    if text == "배당":
        return "color: #16a34a; font-weight: 700;"
    return ""


def _stock_detail_holding_metrics(
    df: pd.DataFrame,
    *,
    positions: list | None = None,
    stock_name: str = "",
    business_name: str = "",
    stock_code: str = "",
) -> dict[str, float | str]:
    """종목 상세용 보유·평가 요약.

    - 보유수량/평단/원가: FIFO 포지션 우선, 없으면 매수−매도·원가합으로 추정
    - 평가단가: 최근 거래 원화 단가 (없으면 외화단가×환율)
    - 평가금액 = 보유수량 × 평가단가
    - 평가손익 = 평가금액 − 원가잔액
    """
    qty = 0.0
    avg_cost = 0.0
    book_cost = 0.0
    realized = 0.0
    mark_price = 0.0
    mark_note = "최근 거래단가"

    name = (stock_name or "").strip()
    code = (stock_code or "").strip().upper()
    biz = (business_name or "").strip()

    matched_pos = []
    if positions:
        for p in positions:
            pname = str(getattr(p, "stock_name", "") or "").strip()
            pcode = str(getattr(p, "stock_code", "") or "").strip().upper()
            pbiz = str(getattr(p, "business_name", "") or "").strip()
            if biz and pbiz != biz:
                continue
            name_ok = bool(name) and (
                pname == name or pname.upper() == name.upper()
            )
            code_ok = bool(code) and pcode == code
            if name_ok or code_ok:
                matched_pos.append(p)
            elif not name and not code:
                continue

    if matched_pos:
        qty = float(sum(float(p.quantity or 0) for p in matched_pos))
        book_cost = float(sum(float(p.total_cost or 0) for p in matched_pos))
        realized = float(sum(float(p.realized_pnl or 0) for p in matched_pos))
        avg_cost = (book_cost / qty) if qty > 1e-12 else 0.0
    elif df is not None and not df.empty:
        work = df.copy()
        side = work["거래유형"].astype(str) if "거래유형" in work.columns else None
        qty_s = pd.to_numeric(work.get("수량"), errors="coerce").fillna(0.0)
        if side is not None:
            buy_m = side.str.contains("매수|매입|입고", regex=True, na=False)
            sell_m = side.str.contains("매도", na=False) & ~side.str.contains("배당", na=False)
            qty = float(qty_s[buy_m].sum() - qty_s[sell_m].sum())
            # 단순 원가: 매수 거래금액 합 − 매도 수량에 대응하는 추정은 생략, 매수 원가합만
            if "거래금액(원가)" in work.columns:
                cost_s = pd.to_numeric(work["거래금액(원가)"], errors="coerce").fillna(0.0)
            else:
                price_s = pd.to_numeric(work.get("단가"), errors="coerce").fillna(0.0)
                cost_s = qty_s * price_s
            buy_cost = float(cost_s[buy_m].sum())
            buy_qty = float(qty_s[buy_m].sum())
            # 잔여 원가는 매수원가 × (잔여/매수수량) 근사
            if buy_qty > 1e-12 and qty > 1e-12:
                book_cost = buy_cost * (qty / buy_qty)
                avg_cost = book_cost / qty
            elif qty > 1e-12 and buy_qty > 1e-12:
                avg_cost = buy_cost / buy_qty
                book_cost = avg_cost * qty

    # 평가단가: 시간순 마지막 유효 단가
    if df is not None and not df.empty:
        ordered = _sort_trades_chronologically(df)
        for i in range(len(ordered) - 1, -1, -1):
            row = ordered.iloc[i]
            side_t = str(row.get("거래유형") or "")
            if "배당" in side_t:
                continue
            px = float(pd.to_numeric(row.get("단가"), errors="coerce") or 0)
            if px > 0:
                mark_price = px
                mark_note = "최근 거래 원화단가"
                break
            fx_px = float(pd.to_numeric(row.get("외화단가"), errors="coerce") or 0)
            fx = float(pd.to_numeric(row.get("환율"), errors="coerce") or 0)
            if fx_px > 0 and fx > 0:
                mark_price = fx_px * fx
                mark_note = "최근 외화단가×환율"
                break

    if mark_price <= 0 and avg_cost > 0:
        mark_price = avg_cost
        mark_note = "평균단가(최근가 없음)"

    qty = max(qty, 0.0) if qty > -1e-9 else qty
    market_value = qty * mark_price if qty > 1e-12 else 0.0
    unrealized = market_value - book_cost if qty > 1e-12 else 0.0

    return {
        "qty": float(qty),
        "avg_cost": float(avg_cost),
        "book_cost": float(book_cost),
        "mark_price": float(mark_price),
        "market_value": float(market_value),
        "unrealized": float(unrealized),
        "realized": float(realized),
        "mark_note": mark_note,
    }


def _safe_download_filename_part(text: str, *, fallback: str = "미지정") -> str:
    """파일명에 쓸 수 없는 문자 제거."""
    raw = str(text or "").strip() or fallback
    for ch in '\\/:*?"<>|\n\r\t':
        raw = raw.replace(ch, "_")
    raw = "_".join(p for p in raw.split() if p)
    return raw[:80] or fallback


def _dataframe_to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "거래내역") -> bytes:
    """DataFrame → .xlsx bytes."""
    import io

    bio = io.BytesIO()
    export = df.copy()
    if "거래일자" in export.columns:
        export["거래일자"] = export["거래일자"].astype(str)
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        export.to_excel(writer, sheet_name=sheet_name[:31] or "Sheet1", index=False)
    return bio.getvalue()


def _row_get(row: object, key: str, default=None):
    try:
        if hasattr(row, "get"):
            return row.get(key, default)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    try:
        return row[key]  # type: ignore[index]
    except Exception:  # noqa: BLE001
        return default


def _fmt_qty_num(value: float) -> str:
    """수량·단가 표시 (불필요 소수·과학적 표기 방지)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "0"
    x = float(value)
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _trade_fx_fields(row: object) -> tuple[float, float, float, float, str]:
    """수량, 외화단가, 환율, 원화단가, 통화."""
    qty = float(pd.to_numeric(_row_get(row, "수량"), errors="coerce") or 0)
    price_fx = float(pd.to_numeric(_row_get(row, "외화단가"), errors="coerce") or 0)
    fx_rate = float(pd.to_numeric(_row_get(row, "환율"), errors="coerce") or 0)
    price_krw = float(pd.to_numeric(_row_get(row, "단가"), errors="coerce") or 0)
    ccy = str(_row_get(row, "통화") or "").strip().upper() or "KRW"
    # 외화단가 누락 시 원화단가÷환율로 추정
    if price_fx <= 0 and fx_rate > 0 and price_krw > 0 and ccy != "KRW":
        price_fx = price_krw / fx_rate
    return qty, price_fx, fx_rate, price_krw, ccy


def _format_memo_stock_qty_fx(row: object) -> str:
    """옵션1: 종목명 거래유형 / @수량주 * $단가 * 환율원

    예: INVESCO NASDAQ 100 매수 / @40주 * $247.67 * 1442.00원
    """
    name = str(_row_get(row, "종목명") or "").strip() or str(
        _row_get(row, "종목코드") or ""
    ).strip()
    side = str(_row_get(row, "거래유형") or "").strip() or "거래"
    qty, price_fx, fx_rate, price_krw, ccy = _trade_fx_fields(row)

    if qty <= 0:
        return str(_row_get(row, "메모") or "").strip()

    head = f"{name} {side}".strip()
    if price_fx > 0 and fx_rate > 0 and ccy != "KRW":
        if ccy == "USD":
            px = f"${price_fx:.2f}"
        else:
            px = f"{ccy} {price_fx:.2f}"
        return f"{head} / @{_fmt_qty_num(qty)}주 * {px} * {fx_rate:.2f}원"
    if price_krw > 0:
        return f"{head} / @{_fmt_qty_num(qty)}주 * {price_krw:,.0f}원"
    return str(_row_get(row, "메모") or head).strip()


def _format_memo_ccy_amount_qty(row: object) -> str:
    """옵션2: 통화 외화총액 / 수량주*단가 * 환율

    예: USD 1862.29 / 5주*371.529 * 1528.6
    """
    qty, price_fx, fx_rate, price_krw, ccy = _trade_fx_fields(row)

    if price_fx > 0 and qty > 0 and ccy != "KRW":
        fx_amt = qty * price_fx
        body = (
            f"{ccy} {_fmt_qty_num(fx_amt)} / "
            f"{_fmt_qty_num(qty)}주*{_fmt_qty_num(price_fx)}"
        )
        if fx_rate > 0:
            body += f" * {_fmt_qty_num(fx_rate)}"
        return body
    if price_krw > 0 and qty > 0:
        return (
            f"KRW {_fmt_qty_num(qty * price_krw)} / "
            f"{_fmt_qty_num(qty)}주*{_fmt_qty_num(price_krw)}"
        )
    return str(_row_get(row, "메모") or "").strip()


def _format_overseas_detail_memo(row: object) -> str:
    """하위 호환: 옵션2와 동일."""
    return _format_memo_ccy_amount_qty(row)


def _pad_memo_strings(memos: list[str]) -> list[str]:
    """옵션2 적요: '/' 기준 앞부분만 최장 길이에 맞춰 오른쪽 공백 패딩.

    예)
      USD 9906.8 / 40주*247.67 * 1442
      USD 7021   / 100주*70.21 * 1442
    → 앞부분(통화·금액) 정렬 후 재조합: 앞부분.ljust(max) + " / " + 뒷부분
    """
    cleaned = ["" if m is None else str(m) for m in memos]
    if not cleaned:
        return cleaned

    fronts: list[str] = []
    backs: list[str] = []
    has_sep: list[bool] = []
    for m in cleaned:
        if "/" in m:
            left, right = m.split("/", 1)
            fronts.append(left.rstrip())
            backs.append(right.lstrip())
            has_sep.append(True)
        else:
            fronts.append(m)
            backs.append("")
            has_sep.append(False)

    # '/' 가 있는 행들만 앞부분 길이 기준으로 패딩
    sep_front_lens = [len(f) for f, sep in zip(fronts, has_sep) if sep]
    if not sep_front_lens:
        return cleaned
    max_len = max(sep_front_lens)

    out: list[str] = []
    for front, back, sep in zip(fronts, backs, has_sep):
        if not sep:
            out.append(front)
            continue
        out.append(front.ljust(max_len) + " / " + back)
    return out


def _prepare_stock_detail_export(
    df: pd.DataFrame,
    *,
    memo_mode: str,
    display_cols: list[str],
) -> pd.DataFrame:
    """엑셀 다운로드용 DF (종목 상세). memo_mode: 'as_is' | 'stock' | 'amount'."""
    if df is None or df.empty:
        return df
    export = df.copy()
    keep = list(dict.fromkeys([*display_cols, "통화", "환율", "외화단가", "메모", "ID"]))
    cols = [c for c in keep if c in export.columns]
    export = export.loc[:, cols].copy()

    if memo_mode == "stock":
        raw = [_format_memo_stock_qty_fx(row) for _, row in export.iterrows()]
        export["적요"] = raw
    elif memo_mode == "amount":
        raw = [_format_memo_ccy_amount_qty(row) for _, row in export.iterrows()]
        export["적요"] = _pad_memo_strings(raw)
    else:
        export["적요"] = export["메모"] if "메모" in export.columns else ""

    out_cols = []
    for c in display_cols:
        if c == "메모":
            out_cols.append("적요")
        elif c in export.columns and c != "적요":
            out_cols.append(c)
    if "적요" not in out_cols:
        out_cols.append("적요")
    seen: set[str] = set()
    ordered: list[str] = []
    for c in out_cols:
        if c not in seen and c in export.columns:
            seen.add(c)
            ordered.append(c)
    return export.loc[:, ordered]



@st.dialog(
    "📊 종목 상세 거래 내역",
    width="large",
    on_dismiss=_dismiss_stock_detail_modal,
)
def show_stock_detail_modal(
    storage: Storage,
    stock_name: str,
    df_stock_trades: pd.DataFrame,
    business_name: str = "",
    positions: list | None = None,
    stock_code: str = "",
    all_trades: list | None = None,
) -> None:
    st.markdown(f"**종목명:** {stock_name}")
    if business_name:
        st.caption(f"사업자: {business_name}")
    period = st.session_state.get("_dash_period") or {}
    if period.get("start") and period.get("end"):
        st.caption(
            f"대시보드 조회 기간: **{period['start']} ~ {period['end']}** "
            "(표는 기간 내 거래, 잔여수량·보유요약은 종료일 기준 FIFO)"
        )

    if df_stock_trades is None or df_stock_trades.empty:
        st.info("해당 종목의 거래 내역이 없습니다.")
        return

    if "ID" not in df_stock_trades.columns:
        st.error("거래 ID가 없어 처분손익·일자를 수정할 수 없습니다.")
        return

    pending_toast = st.session_state.pop("_stock_detail_pnl_toast", None)
    if pending_toast:
        st.toast(str(pending_toast))

    # FIFO 매수 잔여·매수단가 + 거래금액(외화/원화)
    fifo_src = all_trades if all_trades is not None else []
    enriched = _attach_trade_amounts(_attach_fifo_remainders(df_stock_trades, fifo_src))

    display_cols = [c for c in DETAIL_TRADE_COLUMNS if c in enriched.columns]
    # 팝업 표시 직전 시간순 재정렬
    sorted_src = _sort_trades_chronologically(enriched)
    work = sorted_src.loc[
        :, [c for c in [*display_cols, "ID"] if c in sorted_src.columns]
    ].copy()
    work["거래일자"] = pd.to_datetime(work["거래일자"], errors="coerce").dt.date
    work["ID"] = pd.to_numeric(work["ID"], errors="coerce").astype("Int64")
    original_dates = {
        int(tid): _normalize_trade_date(dt)
        for tid, dt in zip(work["ID"], work["거래일자"])
        if pd.notna(tid)
    }

    # 처분손익 기준값 (매도만 숫자, 그 외 None)
    original_pnl: dict[int, float | None] = {}
    for _, row in work.iterrows():
        if pd.isna(row.get("ID")):
            continue
        tid = int(row["ID"])
        side = str(row.get("거래유형") or "")
        raw = row.get("처분손익") if "처분손익" in work.columns else None
        if side != "매도":
            original_pnl[tid] = None
            if "처분손익" in work.columns:
                work.at[row.name, "처분손익"] = None
        elif raw is None or (isinstance(raw, float) and pd.isna(raw)):
            original_pnl[tid] = None
        else:
            try:
                original_pnl[tid] = float(raw)
            except (TypeError, ValueError):
                original_pnl[tid] = None

    # 상단 요약: 처분손익 합(누적실현)이 표와 동기화되도록 work 기준
    metrics = _stock_detail_holding_metrics(
        work,
        positions=positions,
        stock_name=stock_name,
        business_name=business_name,
        stock_code=stock_code,
    )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("보유수량", f"{metrics['qty']:,.4f} 주")
    m2.metric("평균단가", f"{metrics['avg_cost']:,.0f} 원")
    m3.metric(
        "평가금액",
        f"{metrics['market_value']:,.0f} 원",
        help=f"보유수량 × 평가단가({metrics['mark_note']}: {metrics['mark_price']:,.0f} 원)",
    )
    unreal = float(metrics["unrealized"])
    m4.metric(
        "평가손익",
        f"{unreal:,.0f} 원",
        delta=f"{unreal:,.0f}",
        delta_color="normal",
        help="평가금액 − 원가잔액 (시세 연동 없음 · 최근 거래단가 기준)",
    )
    st.caption(
        f"원가잔액 {metrics['book_cost']:,.0f} 원 · "
        f"평가단가 {metrics['mark_price']:,.0f} 원 ({metrics['mark_note']})"
        + (
            f" · 누적실현손익 {metrics['realized']:,.0f} 원"
            if abs(float(metrics["realized"])) > 1e-9
            else ""
        )
    )

    view = work.loc[:, [c for c in display_cols if c in work.columns]].copy()
    detail_col_config = {
        "거래일자": st.column_config.DateColumn("거래일자", format="YYYY-MM-DD"),
        "수량": st.column_config.NumberColumn("수량", format="%,.4f"),
        "잔여수량": st.column_config.NumberColumn(
            "잔여수량",
            format="%,.4f",
            help="해당 매수 건이 FIFO 매도 소진 후 남은 수량 (매도·배당 행은 비움, 전량 소진=0)",
        ),
        "매수단가(외화)": st.column_config.NumberColumn(
            "매수단가(외화)",
            format="%.4f",
            help="매수 건의 외화 단가 (USD 등). 국내주식·매도 행은 비움",
        ),
        "매수단가(원화)": st.column_config.NumberColumn(
            "매수단가(원화)",
            format="%,d 원",
            help="매수 건의 원화 환산 단가 (외화단가×환율). 매도·배당 행은 비움",
        ),
        "거래금액(외화)": st.column_config.TextColumn(
            "거래금액(외화)",
            help="수량 × 외화단가 (통화코드 포함, 수수료 제외)",
        ),
        "거래금액(원화)": st.column_config.NumberColumn(
            "거래금액(원화)",
            format="%,d 원",
            help="수량 × 외화단가 × 적용환율 (수수료 제외). 외화 없으면 수량×원화단가",
        ),
        "단가": st.column_config.NumberColumn("단가", format="%,d 원"),
        "수수료": st.column_config.NumberColumn("수수료", format="%,d 원"),
        "제세금": st.column_config.NumberColumn("제세금", format="%,d 원"),
        "정산금액": st.column_config.NumberColumn("정산금액", format="%,d 원"),
        "처분손익": st.column_config.NumberColumn(
            "처분손익",
            format="%,d 원",
            help="매도 행만 수정 · 변경 시 즉시 저장 (비우면 FIFO 계산값으로 복귀)",
            step=1,
        ),
    }

    stock_part = _safe_download_filename_part(stock_name, fallback="종목")
    biz_part = _safe_download_filename_part(business_name or "전체", fallback="전체")
    xlsx_name = f"{stock_part}_{biz_part}_거래내역.xlsx"
    try:
        xlsx_bytes = _dataframe_to_xlsx_bytes(view, sheet_name="거래내역")
    except Exception as exc:  # noqa: BLE001
        xlsx_bytes = b""
        st.caption(f"엑셀 변환 실패: {exc}")

    dl_col, _ = st.columns([1, 3])
    with dl_col:
        st.download_button(
            "📥 엑셀 다운로드",
            data=xlsx_bytes,
            file_name=xlsx_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="secondary",
            use_container_width=True,
            disabled=not xlsx_bytes,
            key=f"stock_detail_xlsx_{biz_part}_{stock_part}",
        )

    st.caption(
        "매수 행의 **잔여수량**·**매수단가**는 FIFO 기준입니다. "
        "**처분손익**은 매도 행에서 수정 후 Enter/포커스 아웃 시 자동 저장됩니다. "
        "자동 저장이 안 되면 아래 **처분손익 저장** 버튼을 눌러 주세요."
    )

    editor_df = view.copy()
    if "ID" in work.columns and "ID" not in editor_df.columns:
        editor_df["ID"] = work["ID"].values

    # 행 위치 ↔ ID 매핑이 어긋나지 않도록 연속 RangeIndex 로 고정
    editor_df = editor_df.reset_index(drop=True)
    if "ID" not in editor_df.columns:
        st.error("거래 ID 컬럼이 없어 처분손익을 저장할 수 없습니다.")
        return
    editor_df["ID"] = pd.to_numeric(editor_df["ID"], errors="coerce").astype("Int64")

    # DB 오버라이드 (None = FIFO). 키는 반드시 int(trade.id)
    stored_override: dict[int, float | None] = {}
    for t in fifo_src:
        try:
            if t.id is None or str(t.side).upper() != "SELL":
                continue
            ov = getattr(t, "disposal_pnl", None)
            stored_override[int(t.id)] = None if ov is None else float(ov)
        except (TypeError, ValueError):
            continue

    editor_ver = int(st.session_state.get("_pnl_editor_ver", 0) or 0)
    editor_key = f"stock_detail_pnl_editor_{biz_part}_{stock_part}_v{editor_ver}"
    dirty_flag = "_pnl_editor_dirty"

    def _mark_pnl_editor_dirty() -> None:
        st.session_state[dirty_flag] = editor_key

    # NumberColumn format에 '원'을 넣으면 일부 환경에서 커밋이 불안정할 수 있어 숫자만 표시
    pnl_col_config = {
        **{k: v for k, v in detail_col_config.items() if k in editor_df.columns},
        "처분손익": st.column_config.NumberColumn(
            "처분손익",
            format="%,d",
            help="매도 행만 수정 · Enter/포커스 아웃 시 저장 (비우면 FIFO 복귀)",
            step=1,
        ),
        "ID": st.column_config.NumberColumn(
            "ID",
            format="%d",
            disabled=True,
            help="DB trades.id (저장 키)",
            width="small",
        ),
    }
    # ID를 앞에 두어 매핑 확인이 쉽도록
    col_order = ["ID"] + [c for c in editor_df.columns if c != "ID"]
    editor_df = editor_df.loc[:, col_order]

    disabled_cols = [c for c in editor_df.columns if c != "처분손익"]
    edited = st.data_editor(
        editor_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=disabled_cols,
        column_config=pnl_col_config,
        column_order=col_order,
        key=editor_key,
        on_change=_mark_pnl_editor_dirty,
    )

    widget_state = st.session_state.get(editor_key)
    pending_updates = _collect_disposal_pnl_updates(
        edited_df=edited if isinstance(edited, pd.DataFrame) else editor_df,
        input_df=editor_df,
        widget_state=widget_state,
        stored_override=stored_override,
    )

    save_clicked = st.button(
        "💾 처분손익 저장",
        type="primary",
        use_container_width=True,
        key=f"stock_detail_pnl_save_{biz_part}_{stock_part}_v{editor_ver}",
        help="표에서 처분손익을 수정한 뒤 이 버튼으로도 저장할 수 있습니다.",
    )

    # Enter/포커스 아웃(on_change·값 diff) 또는 저장 버튼 → 즉시 DB 반영
    if pending_updates:
        try:
            saved = _save_disposal_pnl_updates(pending_updates)
            if saved > 0:
                st.session_state.pop(dirty_flag, None)
                st.session_state["_pnl_editor_ver"] = editor_ver + 1
                msg = f"처분손익 {saved}건 저장됨"
                st.session_state["_stock_detail_pnl_toast"] = msg
                st.toast(msg)
                st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"처분손익 저장 실패: {exc}")
    elif save_clicked:
        st.info("변경된 처분손익이 없습니다. 매도 행의 처분손익을 수정한 뒤 다시 시도해 주세요.")

    # data_editor는 Styler 색상을 지원하지 않아, 일자 수정만 별도 편집기로 유지
    with st.expander("✏️ 거래일자 수정", expanded=False):
        edit_cols = [c for c in ("거래일자", "거래유형", "ID") if c in work.columns]
        edited_dates = st.data_editor(
            work.loc[:, edit_cols].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            disabled=[c for c in edit_cols if c != "거래일자"],
            column_config={
                "거래일자": st.column_config.DateColumn(
                    "거래일자",
                    format="YYYY-MM-DD",
                    required=True,
                ),
                "ID": st.column_config.NumberColumn("ID", format="%d"),
            },
            key=f"stock_detail_editor_{business_name}_{stock_name}",
        )

        if st.button("💾 수정사항 저장", type="primary", use_container_width=True):
            try:
                db = get_storage()
                updated = 0
                for _, row in edited_dates.iterrows():
                    if pd.isna(row.get("ID")):
                        continue
                    trade_id = int(row["ID"])
                    new_date = _normalize_trade_date(row.get("거래일자"))
                    if not new_date:
                        st.error(f"거래 ID {trade_id}: 거래일자가 비어 있습니다.")
                        return
                    if original_dates.get(trade_id) == new_date:
                        continue
                    db.update_trade_date(trade_id, new_date)
                    updated += 1

                if updated == 0:
                    st.info("변경된 거래일자가 없습니다.")
                    return

                st.session_state["_pending_toast"] = "거래일자가 수정되었습니다."
                st.toast("거래일자가 수정되었습니다.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))


def _open_stock_detail_from_query(
    storage: Storage,
    trades: list,
    *,
    period_trades: list | None = None,
) -> None:
    """세션에 쌓인 종목 상세 요청(_pending_stock_detail)으로 모달을 연다.

    query_params.clear()로 추가 rerun이 나더라도 pending을 pop하지 않고
    다이얼로그 dismiss 때까지 유지한다.

    trades: FIFO·잔여수량 계산용 (보통 종료일까지의 전체 이력)
    period_trades: 테이블에 보여줄 기간 필터 거래 (없으면 trades 전체)
    """
    pending = st.session_state.get("_pending_stock_detail")
    if not pending and (
        "stock" in st.query_params or "code" in st.query_params
    ):
        consume_stock_click_query()
        pending = st.session_state.get("_pending_stock_detail")
    if not pending:
        return

    stock_name = str(pending.get("stock") or "").strip()
    stock_code = str(pending.get("code") or "").strip()
    biz_name = str(pending.get("biz") or "").strip()
    if not stock_name and not stock_code:
        st.session_state.pop("_pending_stock_detail", None)
        return

    # 모달을 여는 매 렌더마다 메뉴/시장 컨텍스트 재고정
    menu = pending.get("menu")
    if menu in ALL_MENU_KEYS:
        st.session_state.active_menu = menu
        st.session_state.active_category = (
            "domestic"
            if menu in DOMESTIC_MENU_KEYS
            else ("overseas" if menu in OVERSEAS_MENU_KEYS else "interest")
        )
        mkt = menu_market(menu)
        if mkt:
            st.session_state.active_market = mkt
    if biz_name and st.session_state.get("sidebar_business") != biz_name:
        # 사이드바 렌더 이후이므로 다음 rerun용 pending만 남김
        st.session_state._pending_business_label = biz_name

    table_src = period_trades if period_trades is not None else trades
    detail = _filter_stock_trades(
        table_src,
        stock_name=stock_name or stock_code,
        business_name=biz_name,
        stock_code=stock_code,
    )
    positions, sells, _ = compute_positions(trades)
    detail = _attach_disposal_pnl(detail, sells)
    show_stock_detail_modal(
        storage,
        stock_name or stock_code,
        detail,
        business_name=biz_name,
        positions=positions,
        stock_code=stock_code,
        all_trades=trades,
    )


def _render_holdings_html_table(
    pos_df: pd.DataFrame,
    *,
    active_menu: str | None = None,
    market: str | None = None,
    show_broker: bool = True,
) -> None:
    """보유 잔고 HTML 표 (종목명 클릭 → query_params).

    컬럼: [증권사], 종목명, 보유수량, 매수가격(평단), 총 평가금액(원가잔액), 누적실현손익
    """
    rows: list[str] = []
    sum_qty = 0.0
    sum_cost = 0.0
    sum_pnl = 0.0
    has_broker = show_broker and "증권사" in pos_df.columns
    has_avg = "평단가" in pos_df.columns

    menu = active_menu or st.session_state.get("active_menu", "domestic_dashboard")
    mkt = market or menu_market(menu) or MARKET_DOMESTIC

    for _, row in pos_df.iterrows():
        biz = str(row.get("사업자", "")).strip()
        broker = str(row.get("증권사", "")).strip() if has_broker else ""
        stock = str(row.get("종목명", "")).strip()
        qty = float(row.get("잔여수량", 0) or 0)
        avg = float(row.get("평단가", 0) or 0) if has_avg else 0.0
        cost = float(row.get("원가잔액", 0) or 0)
        pnl = float(row.get("누적실현손익", 0) or 0)
        sum_qty += qty
        sum_cost += cost
        sum_pnl += pnl

        href = (
            f"?stock={quote(stock)}"
            f"&menu={quote(str(menu))}"
            f"&market={quote(normalize_market(mkt))}"
            f"&market_type={quote(_market_type_param(mkt) or 'DOMESTIC')}"
        )
        code = str(row.get("종목코드", "") or "").strip()
        if code:
            href += f"&code={quote(code)}"
        if biz:
            href += f"&biz={quote(biz)}"
        active_bid = st.session_state.get("active_business_id")
        if active_bid is not None:
            href += f"&business_id={int(active_bid)}"
        elif biz:
            id_by_name = st.session_state.get("_biz_id_by_name") or {}
            row_bid = id_by_name.get(biz)
            if row_bid is not None:
                href += f"&business_id={int(row_bid)}"
        stock_link = (
            f'<a href="{html.escape(href)}" target="_self">'
            f"{html.escape(stock or code or '-')}</a>"
        )
        cells = []
        if has_broker:
            cells.append(f"<td>{html.escape(broker or '미지정')}</td>")
        cells.append(f"<td class='stock'>{stock_link}</td>")
        cells.append(f"<td class='num'>{qty:,.4f} 주</td>")
        if has_avg:
            cells.append(f"<td class='num'>{avg:,.0f} 원</td>")
        cells.append(f"<td class='num'>{cost:,.0f} 원</td>")
        cells.append(f"<td class='num'>{pnl:,.0f} 원</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    footer_cells = []
    if has_broker:
        footer_cells.append("<td></td>")
    footer_cells.append("<td>합계</td>")
    footer_cells.append(f"<td class='num'>{sum_qty:,.4f} 주</td>")
    if has_avg:
        footer_cells.append("<td class='num'>—</td>")
    footer_cells.append(f"<td class='num'>{sum_cost:,.0f} 원</td>")
    footer_cells.append(f"<td class='num'>{sum_pnl:,.0f} 원</td>")
    footer = f"<tr class='total'>{''.join(footer_cells)}</tr>"

    headers = []
    if has_broker:
        headers.append("<th>증권사</th>")
    headers.append("<th>종목명</th>")
    headers.append("<th>보유수량</th>")
    if has_avg:
        headers.append("<th>매수가격</th>")
    headers.append("<th>총 평가금액</th>")
    headers.append("<th>누적실현손익</th>")

    table_html = f"""
    <style>
    .holdings-html-wrap {{
        width: 100%;
        overflow-x: auto;
        margin: 0.25rem 0 0.75rem 0;
    }}
    table.holdings-html {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.875rem;
        line-height: 1.35;
        color: inherit;
    }}
    table.holdings-html th,
    table.holdings-html td {{
        padding: 0.4rem 0.65rem;
        border-bottom: 1px solid rgba(49, 51, 63, 0.18);
        text-align: left;
        vertical-align: middle;
        white-space: nowrap;
    }}
    table.holdings-html th {{
        font-weight: 600;
        opacity: 0.85;
    }}
    table.holdings-html td.num {{
        text-align: right;
        font-variant-numeric: tabular-nums;
    }}
    table.holdings-html td.stock a {{
        color: #1c83e1;
        text-decoration: none;
        font-weight: 600;
    }}
    table.holdings-html td.stock a:hover {{
        text-decoration: underline;
    }}
    table.holdings-html tbody tr:hover {{
        background: rgba(28, 131, 225, 0.06);
    }}
    table.holdings-html tfoot tr.total td {{
        font-weight: 700;
        border-top: 2px solid rgba(49, 51, 63, 0.35);
        border-bottom: none;
        padding-top: 0.55rem;
        background: rgba(49, 51, 63, 0.04);
    }}
    </style>
    <div class="holdings-html-wrap">
      <table class="holdings-html">
        <thead>
          <tr>
            {''.join(headers)}
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
        <tfoot>
          {footer}
        </tfoot>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)



def refresh_fifo(
    storage: Storage,
    business_id: int | None = None,
    market: str | None = None,
):
    trades = storage.list_trades(business_id=business_id, market=market)
    return compute_positions(trades), trades


def _trade_ymd(trade) -> str:
    return str(getattr(trade, "trade_date", "") or "")[:10]


def _filter_trades_by_date_range(
    trades: list,
    start: date | None,
    end: date | None,
) -> list:
    """시작일~종료일(포함) 거래만 남긴다. None이면 해당 쪽 제한 없음."""
    if not trades:
        return []
    s = start.isoformat() if isinstance(start, date) else (str(start)[:10] if start else "")
    e = end.isoformat() if isinstance(end, date) else (str(end)[:10] if end else "")
    out = []
    for t in trades:
        d = _trade_ymd(t)
        if not d:
            continue
        if s and d < s:
            continue
        if e and d > e:
            continue
        out.append(t)
    return out


def _default_dashboard_date_range(trades: list) -> tuple[date, date]:
    """거래 데이터의 최소~최대(없으면 올해 1/1~오늘)."""
    today = date.today()
    dates: list[date] = []
    for t in trades:
        parsed = pd.to_datetime(_trade_ymd(t), errors="coerce")
        if pd.notna(parsed):
            dates.append(parsed.date())
    if not dates:
        return date(today.year, 1, 1), today
    return min(dates), max(dates)

DOMESTIC_MENU_ITEMS = [
    ("domestic_dashboard", "📊 대시보드"),
    ("domestic_trade_input", "📝 매매 입력"),
    ("domestic_import_export", "📤 엑셀 다운로드"),
    ("domestic_converter", "🔄 증권사 변환기"),
    ("domestic_base_data", "📋 기초 데이터 등록"),
    ("domestic_stock_masters", "🏢 거래처 및 종목 관리"),
    ("domestic_stock_settings", "⚙️ 환경설정 (국내주식 계정코드)"),
]
OVERSEAS_MENU_ITEMS = [
    ("overseas_dashboard", "📊 대시보드"),
    ("overseas_trade_input", "📝 매매 입력"),
    ("overseas_import_export", "📤 엑셀 다운로드"),
    ("overseas_converter", "🔄 증권사 변환기"),
    ("overseas_base_data", "📋 기초 데이터 등록"),
    ("overseas_stock_masters", "🏢 거래처 및 종목 관리"),
    ("overseas_stock_settings", "⚙️ 환경설정 (해외주식 계정코드)"),
]
INCOME_MENU_ITEMS = [
    ("interest_list", "📄 이자·배당 내역 및 전표"),
    ("interest_settings", "⚙️ 거래처 및 계정과목 설정"),
]

DOMESTIC_MENU_KEYS = {k for k, _ in DOMESTIC_MENU_ITEMS}
OVERSEAS_MENU_KEYS = {k for k, _ in OVERSEAS_MENU_ITEMS}
INCOME_MENU_KEYS = {k for k, _ in INCOME_MENU_ITEMS}
ALL_MENU_KEYS = DOMESTIC_MENU_KEYS | OVERSEAS_MENU_KEYS | INCOME_MENU_KEYS

_MARKET_TITLE = {
    MARKET_DOMESTIC: ("국내주식 매매일지 · 잔고 관리", "국내"),
    MARKET_OVERSEAS: ("해외주식 매매일지 · 잔고 관리", "해외"),
}

MENU_PAGE_META: dict[str, tuple[str, str]] = {}
for _mkt, (_title, _short) in _MARKET_TITLE.items():
    prefix = "domestic" if _mkt == MARKET_DOMESTIC else "overseas"
    MENU_PAGE_META.update(
        {
            f"{prefix}_dashboard": (_title, ""),
            f"{prefix}_trade_input": (_title, f"{_short} · 매수·매도 거래 입력"),
            f"{prefix}_import_export": (_title, ""),
            f"{prefix}_converter": (_title, f"{_short} · 증권사 거래내역 변환"),
            f"{prefix}_base_data": (_title, f"{_short} · 기초 데이터(레거시) 등록"),
            f"{prefix}_stock_masters": (_title, f"{_short} · 거래처·종목 마스터"),
            f"{prefix}_stock_settings": (_title, f"{_short} · 주식 계정과목 코드 설정"),
        }
    )
MENU_PAGE_META.update(
    {
        "interest_list": ("이자·배당소득 관리", "원천징수 업로드 · 전표 다운로드"),
        "interest_settings": ("이자·배당소득 관리", "거래처·계정과목 설정"),
    }
)

# 구 라벨/세션 값 → active_menu 키 (기본: 국내)
_LEGACY_TO_MENU: dict[str, str] = {
    "dashboard": "domestic_dashboard",
    "trade_input": "domestic_trade_input",
    "import_export": "domestic_import_export",
    "converter": "domestic_converter",
    "base_data": "domestic_base_data",
    "stock_masters": "domestic_stock_masters",
    "stock_settings": "domestic_stock_settings",
    "domestic_dashboard": "domestic_dashboard",
    "overseas_dashboard": "overseas_dashboard",
    "interest_list": "interest_list",
    "interest_settings": "interest_settings",
    "대시보드": "domestic_dashboard",
    "📊 대시보드": "domestic_dashboard",
    "매매 입력": "domestic_trade_input",
    "📝 매매 입력": "domestic_trade_input",
    "Import / Export": "domestic_import_export",
    "📤 Import / Export": "domestic_import_export",
    "엑셀 다운로드": "domestic_import_export",
    "📤 엑셀 다운로드": "domestic_import_export",
    "증권사 변환기": "domestic_converter",
    "🔄 증권사 변환기": "domestic_converter",
    "기초 데이터 등록": "domestic_base_data",
    "📋 기초 데이터 등록": "domestic_base_data",
    "거래처 및 종목 관리": "domestic_stock_masters",
    "🏢 거래처 및 종목 관리": "domestic_stock_masters",
    "환경설정 (주식)": "domestic_stock_settings",
    "⚙️ 환경설정 (주식)": "domestic_stock_settings",
    "⚙️ 환경설정 (주식 계정코드)": "domestic_stock_settings",
    "⚙️ 환경설정 (국내주식 계정코드)": "domestic_stock_settings",
    "⚙️ 환경설정 (해외주식 계정코드)": "overseas_stock_settings",
    "환경설정 / 계정과목 관리": "domestic_stock_settings",
    "⚙️ 환경설정 (계정과목 코드)": "domestic_stock_settings",
    "내역 업로드·전표": "interest_list",
    "📄 이자·배당 내역 및 전표": "interest_list",
    "환경설정 (이자·배당)": "interest_settings",
    "⚙️ 거래처 및 계정과목 설정": "interest_settings",
}

NEW_STOCK_OPTION = "➕ 신규 종목 직접 입력"


def menu_market(menu: str) -> str | None:
    """active_menu → domestic|overseas|None(이자)."""
    if menu.startswith("domestic_"):
        return MARKET_DOMESTIC
    if menu.startswith("overseas_"):
        return MARKET_OVERSEAS
    return None


def menu_action(menu: str) -> str:
    if menu.startswith("domestic_"):
        return menu[len("domestic_") :]
    if menu.startswith("overseas_"):
        return menu[len("overseas_") :]
    return menu


def market_label(market: str) -> str:
    return "해외주식" if normalize_market(market) == MARKET_OVERSEAS else "국내주식"


def _market_type_param(market: str | None) -> str | None:
    """세션 시장값 → URL market_type (DOMESTIC|OVERSEAS)."""
    if not market:
        return None
    return (
        "OVERSEAS"
        if normalize_market(market) == MARKET_OVERSEAS
        else "DOMESTIC"
    )


def sync_url_params() -> None:
    """세션의 사업자·메뉴·시장을 URL 쿼리에 반영 (값이 다를 때만 갱신)."""
    # business_id
    bid = st.session_state.get("active_business_id")
    desired_biz = "all" if bid is None else str(int(bid))
    if str(st.query_params.get("business_id", "") or "") != desired_biz:
        st.query_params["business_id"] = desired_biz

    # menu
    menu = str(st.session_state.get("active_menu") or "domestic_dashboard")
    if menu in ALL_MENU_KEYS and str(st.query_params.get("menu", "") or "") != menu:
        st.query_params["menu"] = menu

    # market_type (메뉴에서 유도, 이자 메뉴는 마지막 active_market 유지)
    mkt = menu_market(menu) or st.session_state.get("active_market")
    desired_mt = _market_type_param(mkt)
    if desired_mt and str(st.query_params.get("market_type", "") or "") != desired_mt:
        st.query_params["market_type"] = desired_mt


def sync_business_to_query(business_id: int | None) -> None:
    """선택 사업자를 URL ?business_id= 에 반영 (F5 복원용). 전체는 all."""
    desired = "all" if business_id is None else str(int(business_id))
    current = str(st.query_params.get("business_id", "") or "")
    if current != desired:
        st.query_params["business_id"] = desired
    # 메뉴·시장도 함께 유지
    sync_url_params()


def restore_business_from_query(
    businesses: list,
) -> tuple[str, int | None] | None:
    """URL business_id → (라벨, id). 유효하지 않으면 None."""
    if "business_id" not in st.query_params:
        return None
    raw = str(st.query_params.get("business_id", "") or "").strip().lower()
    if raw in {"", "all", "none", "null"}:
        return ("전체", None)
    if not raw.isdigit():
        return None
    bid = int(raw)
    match = next((b for b in businesses if int(b.id) == bid), None)
    if match is None:
        return None
    return (match.name, int(match.id))


def _restore_menu_from_query() -> str | None:
    """URL menu → 유효한 active_menu 키. 없거나 무효면 None."""
    raw = str(st.query_params.get("menu", "") or "").strip()
    if not raw:
        return None
    mapped = _LEGACY_TO_MENU.get(raw, raw)
    if mapped in ALL_MENU_KEYS:
        return mapped
    return None


def _restore_market_from_query() -> str | None:
    """URL market_type|market → domestic|overseas."""
    raw = str(
        st.query_params.get("market_type")
        or st.query_params.get("market")
        or ""
    ).strip()
    if not raw:
        return None
    return normalize_market(raw)


def consume_stock_click_query() -> None:
    """보유잔고 종목 클릭(?stock=…) 시 메뉴·사업자·시장 세션을 먼저 복원한다.

    query_params 네비게이션으로 세션이 비어도 URL에 실린 컨텍스트로
    해외/국내 메뉴와 사업자가 기본값으로 덮이지 않도록 한다.
    """
    if "stock" not in st.query_params and "code" not in st.query_params:
        return

    stock_name = str(st.query_params.get("stock", "") or "").strip()
    stock_code = str(st.query_params.get("code", "") or "").strip()
    biz_name = str(st.query_params.get("biz", "") or "").strip()
    menu = str(st.query_params.get("menu", "") or "").strip()
    market_raw = str(
        st.query_params.get("market")
        or st.query_params.get("market_type")
        or ""
    ).strip()
    # F5 유지용 파라미터는 clear 대상에서 제외
    kept_business_id = str(st.query_params.get("business_id", "") or "").strip()
    kept_menu = menu
    kept_market_type = str(st.query_params.get("market_type", "") or "").strip()

    if menu in ALL_MENU_KEYS:
        st.session_state.active_menu = menu
    elif market_raw:
        mkt = normalize_market(market_raw)
        st.session_state.active_menu = (
            "overseas_dashboard"
            if mkt == MARKET_OVERSEAS
            else "domestic_dashboard"
        )
        st.session_state.active_market = mkt
    elif st.session_state.get("active_menu") not in ALL_MENU_KEYS:
        pass

    if market_raw:
        st.session_state.active_market = normalize_market(market_raw)

    if biz_name:
        st.session_state.sidebar_business = biz_name
        st.session_state._pending_business_label = biz_name
    elif kept_business_id and kept_business_id.isdigit():
        st.session_state.active_business_id = int(kept_business_id)

    if stock_name or stock_code:
        st.session_state["_pending_stock_detail"] = {
            "stock": stock_name or stock_code,
            "code": stock_code,
            "biz": biz_name,
            "menu": st.session_state.get("active_menu"),
            "market": market_raw or (
                menu_market(st.session_state.get("active_menu", "")) or ""
            ),
        }

    # 종목 클릭 일회성 파라미터만 제거 (menu / market_type / business_id 유지)
    for key in ("stock", "code", "biz", "market"):
        if key in st.query_params:
            del st.query_params[key]
    if kept_business_id and str(st.query_params.get("business_id", "") or "") != kept_business_id:
        st.query_params["business_id"] = kept_business_id
    if kept_menu in ALL_MENU_KEYS and str(st.query_params.get("menu", "") or "") != kept_menu:
        st.query_params["menu"] = kept_menu
    if kept_market_type and str(st.query_params.get("market_type", "") or "") != kept_market_type:
        st.query_params["market_type"] = kept_market_type


def init_session_state() -> None:
    # 종목 클릭 URL 컨텍스트를 기본값 적용보다 먼저 복원
    consume_stock_click_query()

    if "active_business_id" not in st.session_state:
        st.session_state.active_business_id = None

    if "active_menu" not in st.session_state:
        # 1순위: URL ?menu= (F5 복원)
        from_query = _restore_menu_from_query()
        if from_query:
            st.session_state.active_menu = from_query
        else:
            candidates = [
                st.session_state.get("active_menu"),
                st.session_state.get("stock_sub_menu"),
                st.session_state.get("interest_sub_menu"),
                st.session_state.get("stock_sub_page"),
                st.session_state.get("income_sub_page"),
                st.session_state.get("active_page"),
                st.session_state.get("main_category_radio"),
                st.session_state.get("main_menu"),
            ]
            resolved = "domestic_dashboard"
            for raw in candidates:
                if raw is None:
                    continue
                mapped = _LEGACY_TO_MENU.get(str(raw))
                if mapped in ALL_MENU_KEYS:
                    resolved = mapped
                    break
                if str(raw) in ALL_MENU_KEYS:
                    resolved = str(raw)
                    break
                if "이자" in str(raw) or "배당" in str(raw):
                    resolved = "interest_list"
                    break
                if "해외" in str(raw):
                    resolved = "overseas_dashboard"
                    break
            st.session_state.active_menu = resolved

    cur = st.session_state.get("active_menu")
    if cur in _LEGACY_TO_MENU:
        st.session_state.active_menu = _LEGACY_TO_MENU[cur]
    if st.session_state.get("active_menu") not in ALL_MENU_KEYS:
        st.session_state.active_menu = "domestic_dashboard"

    st.session_state.active_category = (
        "domestic"
        if st.session_state.active_menu in DOMESTIC_MENU_KEYS
        else (
            "overseas"
            if st.session_state.active_menu in OVERSEAS_MENU_KEYS
            else "interest"
        )
    )
    # 시장 키: 메뉴 우선, 없으면 URL market_type 복원
    mkt = menu_market(st.session_state.active_menu)
    if mkt:
        st.session_state.active_market = mkt
    elif "active_market" not in st.session_state:
        from_mkt = _restore_market_from_query()
        if from_mkt:
            st.session_state.active_market = from_mkt

    # 호환 키 (프롬프트의 market_type)
    if st.session_state.get("active_market"):
        st.session_state.market_type = _market_type_param(
            st.session_state.active_market
        )

    st.session_state.pop("_force_page", None)

def _clear_force_page() -> None:
    st.session_state.pop("_force_page", None)


def set_active_business(business_id: int | None, business_name: str | None = None) -> None:
    """활성 사업자 ID를 갱신하고, 다음 렌더에서 사이드바 선택을 반영."""
    st.session_state.active_business_id = business_id
    if business_name is not None:
        st.session_state._pending_business_label = business_name
    elif business_id is None:
        st.session_state._pending_business_label = "전체"
    sync_business_to_query(business_id)


def _on_sidebar_business_change() -> None:
    """사이드바 사업자 selectbox 변경 → 세션 + URL 동기화."""
    label = str(st.session_state.get("sidebar_business", "전체") or "전체")
    businesses = st.session_state.get("_biz_id_by_name") or {}
    if label == "전체":
        st.session_state.active_business_id = None
        sync_business_to_query(None)
        return
    bid = businesses.get(label)
    if bid is not None:
        st.session_state.active_business_id = int(bid)
        sync_business_to_query(int(bid))


@st.dialog("⚙️ 사업자 관리")
def manage_business_modal(
    storage: Storage,
    current_biz_id: int | None = None,
) -> None:
    """사업자 명칭 수정 및 삭제."""
    # 다이얼로그는 옛 Storage를 들고 있을 수 있어 항상 최신 인스턴스 사용
    db = get_storage()
    businesses = db.list_businesses()
    if not businesses:
        st.info("등록된 사업자가 없습니다. ＋ 버튼으로 추가해 주세요.")
        return

    st.caption("등록된 사업자 명칭을 수정하거나 삭제할 수 있습니다.")

    biz_by_id = {int(b.id): b for b in businesses if b.id is not None}  # type: ignore[arg-type]
    options = list(biz_by_id.keys())
    default_idx = 0
    if current_biz_id is not None and int(current_biz_id) in biz_by_id:
        default_idx = options.index(int(current_biz_id))

    selected_id = st.selectbox(
        "대상 사업자 선택",
        options=options,
        index=default_idx,
        format_func=lambda x: biz_by_id[int(x)].name,
        key="manage_biz_select",
    )
    selected = biz_by_id[int(selected_id)]
    current_name = selected.name

    st.divider()
    st.subheader("✏️ 사업자명 수정")
    new_name = st.text_input(
        "새 사업자 명칭",
        value=current_name,
        key=f"edit_biz_name_{selected_id}",
    )
    if st.button("💾 명칭 수정 저장", use_container_width=True, type="primary"):
        try:
            name = (new_name or "").strip()
            if not name:
                st.error("사업자명을 입력해 주세요.")
            elif name == current_name:
                st.warning("기존 명칭과 동일합니다.")
            elif any(
                b.name == name and int(b.id) != int(selected_id)
                for b in businesses
                if b.id is not None
            ):
                st.error("이미 사용 중인 사업자명입니다.")
            else:
                db.update_business(
                    int(selected_id),
                    name,
                    note=getattr(selected, "note", "") or "",
                    code=getattr(selected, "code", "") or "",
                    account_no=getattr(selected, "account_no", "") or "",
                )
                if st.session_state.get("active_business_id") == int(selected_id):
                    set_active_business(int(selected_id), name)
                st.session_state["_pending_toast"] = (
                    f"사업자명이 '{name}'(으)로 변경되었습니다."
                )
                st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    st.divider()
    st.subheader("🗑️ 사업자 삭제")
    st.caption(
        "⚠️ 사업자를 삭제하면 해당 사업자의 모든 매매 내역·잔고·이자/배당 데이터가 "
        "함께 삭제됩니다. 이 작업은 되돌릴 수 없습니다."
    )
    confirm_delete = st.checkbox(
        f"[{current_name}] 삭제를 확인했습니다.",
        key=f"confirm_delete_chk_{selected_id}",
    )
    if st.button(
        "🚨 사업자 삭제",
        use_container_width=True,
        disabled=not confirm_delete,
        key=f"manage_biz_delete_{selected_id}",
    ):
        try:
            db.delete_entity(int(selected_id))
            # 삭제한 사업자가 현재 선택이면 전체로 전환 (menu/market URL은 유지)
            if st.session_state.get("active_business_id") == int(selected_id):
                set_active_business(None, "전체")
            st.session_state["_pending_toast"] = (
                f"'{current_name}' 사업자가 삭제되었습니다."
            )
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))


def sidebar_business_selector(storage: Storage) -> int | None:
    try:
        businesses = storage.list_businesses()
    except Exception as exc:  # noqa: BLE001
        from src.supabase_client import is_transient_network_error

        if is_transient_network_error(exc) or "데이터베이스 연결에 실패" in str(exc):
            st.sidebar.error("사업자 목록을 불러오지 못했습니다. 연결을 확인해 주세요.")
            st.sidebar.caption(str(exc))
            return st.session_state.get("active_business_id")
        raise
    labels = ["전체", *[b.name for b in businesses]]
    id_by_name = {b.name: b.id for b in businesses}
    # on_change 콜백에서 이름→ID 해석용
    st.session_state._biz_id_by_name = id_by_name

    # 세션이 비어 있을 때만 URL → 세션 복원 (F5). 매 렌더 적용 시 selectbox 변경을 덮어씀.
    if "sidebar_business" not in st.session_state:
        restored = restore_business_from_query(businesses)
        if restored is not None:
            label_from_q, bid_from_q = restored
            st.session_state.sidebar_business = label_from_q
            st.session_state.active_business_id = bid_from_q
        elif businesses:
            st.session_state.sidebar_business = businesses[0].name
            st.session_state.active_business_id = businesses[0].id
        else:
            st.session_state.sidebar_business = "전체"
            st.session_state.active_business_id = None

    # 등록/삭제·종목클릭 직후 강제 지정된 라벨 적용 (selectbox 생성 전에만)
    pending = st.session_state.pop("_pending_business_label", None)
    if pending is not None and pending in labels:
        st.session_state.sidebar_business = pending

    # 삭제된 사업자 등이 남아 있으면 보정
    if st.session_state.get("sidebar_business") not in labels:
        if businesses:
            st.session_state.sidebar_business = businesses[0].name
        else:
            st.session_state.sidebar_business = "전체"

    st.sidebar.subheader("🏢 사업자 관리")
    sel_col, add_col, cfg_col = st.sidebar.columns([4, 1, 1])
    with sel_col:
        label = st.selectbox(
            "사업자 선택",
            labels,
            key="sidebar_business",
            label_visibility="collapsed",
            on_change=_on_sidebar_business_change,
        )
    with add_col:
        with st.popover("＋"):
            st.caption("새 사업자 추가")
            new_biz = st.text_input(
                "사업자명",
                placeholder="예: 사업자 A",
                key="popover_new_business",
                label_visibility="collapsed",
            )
            if st.button("추가", type="primary", use_container_width=True, key="popover_add_biz"):
                try:
                    name = new_biz.strip()
                    if not name:
                        st.error("사업자명을 입력하세요.")
                    else:
                        bid = storage.add_business(name)
                        set_active_business(bid, name)
                        if "popover_new_business" in st.session_state:
                            del st.session_state["popover_new_business"]
                        st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    if label == "전체":
        st.session_state.active_business_id = None
        selected_id = None
    else:
        selected_id = id_by_name[label]
        st.session_state.active_business_id = selected_id

    can_manage = bool(businesses)
    with cfg_col:
        if st.button(
            "⚙️",
            use_container_width=True,
            disabled=not can_manage,
            key="sidebar_manage_biz",
            help="사업자 명칭 수정·삭제" if can_manage else "사업자를 먼저 추가하세요",
        ):
            manage_business_modal(storage, current_biz_id=selected_id)

    # URL과 세션 최종 동기화 (콜백 외 경로·최초 진입 포함)
    sync_business_to_query(selected_id)

    # 사업자 바로 아래 — 스크롤 없이 보이도록
    sidebar_data_management(storage, selected_id)

    return selected_id


@st.dialog("🗑️ 선택 사업자 거래 삭제")
def confirm_clear_business_trades_dialog(
    storage: Storage,
    business_id: int,
    business_name: str,
) -> None:
    """현재 선택 사업자의 매매 거래만 삭제 (종목·사업자 마스터는 유지)."""
    db = get_storage()
    trades = db.list_trades(business_id=int(business_id))
    st.warning(
        f"**[{business_name}]** 사업자의 매매 거래 **{len(trades):,}건**을 삭제합니다.\n\n"
        "사업자·종목 마스터와 이자·배당 내역은 유지됩니다. 되돌릴 수 없습니다."
    )
    confirm = st.checkbox(
        "위 안내를 확인했으며 삭제를 진행합니다.",
        key=f"confirm_clear_trades_{business_id}",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("취소", use_container_width=True, key="clear_trades_cancel"):
            st.rerun()
    with c2:
        if st.button(
            "🗑️ 삭제 실행",
            type="primary",
            use_container_width=True,
            disabled=not confirm,
            key="clear_trades_confirm",
        ):
            try:
                n = db.clear_trades_for_business(int(business_id))
                st.session_state["_pending_toast"] = (
                    f"[{business_name}] 거래 {n:,}건이 삭제되었습니다."
                )
                # 증권사 변환기 임시 상태도 비움
                for k in (
                    "broker_edit_df",
                    "broker_parse_token",
                    "broker_parse_notes",
                    "broker_parse_name",
                ):
                    st.session_state.pop(k, None)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))


def sidebar_data_management(
    storage: Storage,
    business_id: int | None,
) -> None:
    """사이드바 데이터 관리 — 로컬 백업 · 선택 사업자 거래 삭제."""
    from src.backup import BACKUP_ROOT, create_backup, is_ephemeral_host, latest_backup_time

    st.sidebar.markdown("---")
    st.sidebar.subheader("데이터 관리")

    if is_ephemeral_host():
        st.sidebar.caption("클라우드 배포 — 로컬 백업은 PC에서 실행하세요.")
    else:
        last_bt = latest_backup_time()
        if last_bt is None:
            st.sidebar.caption("로컬 백업: 없음")
        else:
            st.sidebar.caption(f"로컬 백업: {last_bt.strftime('%Y-%m-%d %H:%M')}")

        if st.sidebar.button(
            "💾 지금 로컬 백업",
            use_container_width=True,
            key="sidebar_backup_now",
            help=f"Supabase 전체를 {BACKUP_ROOT} 에 JSON으로 저장",
        ):
            try:
                result = create_backup(label="manual")
                st.session_state._pending_toast = (
                    f"백업 완료 · {result.total_rows:,}행 → {result.path.name}"
                )
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.sidebar.error(f"백업 실패: {exc}")

    if business_id is None:
        st.sidebar.caption("사업자를 선택하면 해당 사업자의 거래만 삭제할 수 있습니다.")
        st.sidebar.button(
            "🗑️ 선택 사업자 거래 삭제",
            use_container_width=True,
            disabled=True,
            key="sidebar_clear_trades_disabled",
            help="사이드바에서 사업자를 먼저 선택하세요 (전체 제외)",
        )
        return

    biz_name = next(
        (b.name for b in storage.list_businesses() if b.id == business_id),
        str(business_id),
    )
    trade_n = len(storage.list_trades(business_id=business_id))
    st.sidebar.caption(f"대상: **{biz_name}** · 거래 {trade_n:,}건")
    if st.sidebar.button(
        "🗑️ 선택 사업자 거래 삭제",
        use_container_width=True,
        type="secondary",
        key="sidebar_clear_trades",
        help="선택한 사업자의 매매 거래만 삭제합니다",
    ):
        confirm_clear_business_trades_dialog(storage, int(business_id), biz_name)


def _activate_menu(menu_key: str) -> None:
    st.session_state.active_menu = menu_key
    if menu_key in DOMESTIC_MENU_KEYS:
        st.session_state.active_category = "domestic"
        st.session_state.active_market = MARKET_DOMESTIC
    elif menu_key in OVERSEAS_MENU_KEYS:
        st.session_state.active_category = "overseas"
        st.session_state.active_market = MARKET_OVERSEAS
    else:
        st.session_state.active_category = "interest"
    st.session_state.market_type = _market_type_param(
        st.session_state.get("active_market")
    )
    sync_url_params()


def _sidebar_nav_button(label: str, menu_key: str) -> None:
    """트리형 하위 메뉴 버튼. 현재 선택 항목은 primary로 강조."""
    is_active = st.session_state.get("active_menu") == menu_key
    st.button(
        label,
        key=f"nav_{menu_key}",
        use_container_width=True,
        type="primary" if is_active else "secondary",
        on_click=_activate_menu,
        args=(menu_key,),
    )


def sidebar_tree_menu() -> str:
    """대메뉴 expander + 하위 버튼 트리. active_menu 키를 반환."""
    menu = st.session_state.get("active_menu", "domestic_dashboard")
    cat = st.session_state.get("active_category")
    if cat not in {"domestic", "overseas", "interest"}:
        cat = (
            "domestic"
            if menu in DOMESTIC_MENU_KEYS
            else ("overseas" if menu in OVERSEAS_MENU_KEYS else "interest")
        )

    with st.sidebar.expander(
        "📈 국내주식 매매일지·잔고 관리",
        expanded=(cat == "domestic"),
    ):
        for key, label in DOMESTIC_MENU_ITEMS:
            _sidebar_nav_button(label, key)

    with st.sidebar.expander(
        "🌎 해외주식 매매일지·잔고 관리",
        expanded=(cat == "overseas"),
    ):
        for key, label in OVERSEAS_MENU_ITEMS:
            _sidebar_nav_button(label, key)

    with st.sidebar.expander(
        "💰 이자·배당소득 관리",
        expanded=(cat == "interest"),
    ):
        for key, label in INCOME_MENU_ITEMS:
            _sidebar_nav_button(label, key)

    return st.session_state.get("active_menu", "domestic_dashboard")


def route_active_menu(
    storage: Storage,
    business_id: int | None,
    menu: str,
) -> None:
    """active_menu 키에 따라 본문 페이지 렌더."""
    market = menu_market(menu)
    action = menu_action(menu)

    if market is not None:
        if action == "dashboard":
            page_dashboard(storage, business_id, market=market)
        elif action == "trade_input":
            page_trades(storage, business_id, market=market)
        elif action == "import_export":
            page_excel_download(storage, market=market)
        elif action == "converter":
            page_broker(storage, market=market)
        elif action == "base_data":
            page_legacy_journal(storage, business_id, market=market)
        elif action == "stock_masters":
            page_masters(storage, business_id, market=market)
        elif action == "stock_settings":
            page_settings(storage, business_id, mode="stock", market=market)
        else:
            page_dashboard(storage, business_id, market=market)
        return

    if action == "interest_list":
        page_income(storage, business_id)
    elif action == "interest_settings":
        page_settings(storage, business_id, mode="income")
    else:
        page_dashboard(storage, business_id, market=MARKET_DOMESTIC)


def page_dashboard(
    storage: Storage,
    business_id: int | None,
    market: str = MARKET_DOMESTIC,
) -> None:
    market = normalize_market(market)

    _, all_trades = refresh_fifo(storage, business_id, market=market)
    default_start, default_end = _default_dashboard_date_range(all_trades)
    mkt = normalize_market(market)
    start_key = f"dash_start_date_{mkt}_{business_id}"
    end_key = f"dash_end_date_{mkt}_{business_id}"
    applied_key = f"dash_applied_period_{mkt}_{business_id}"
    legacy_range_key = f"dash_date_range_{mkt}_{business_id}"
    query_btn_key = f"dash_query_btn_{mkt}_{business_id}"

    # 구 범위 위젯 → 시작/종료 키 마이그레이션
    if legacy_range_key in st.session_state:
        old = st.session_state.pop(legacy_range_key, None)
        if isinstance(old, (tuple, list)) and len(old) == 2:
            if start_key not in st.session_state:
                st.session_state[start_key] = old[0]
            if end_key not in st.session_state:
                st.session_state[end_key] = old[1]
    if start_key not in st.session_state:
        st.session_state[start_key] = default_start
    if end_key not in st.session_state:
        st.session_state[end_key] = default_end

    def _as_date(v) -> date:
        if isinstance(v, date):
            return v
        return date.fromisoformat(str(v)[:10])

    # 실제 필터에 쓰는 적용 기간 (조회 버튼으로만 갱신)
    if applied_key not in st.session_state:
        st.session_state[applied_key] = {
            "start": _as_date(st.session_state[start_key]),
            "end": _as_date(st.session_state[end_key]),
        }

    st.markdown("##### 조회 기간")
    d1, d2, d3 = st.columns([1, 1, 0.45], vertical_alignment="bottom")
    with d1:
        draft_start = st.date_input(
            "시작일",
            format="YYYY-MM-DD",
            key=start_key,
        )
    with d2:
        draft_end = st.date_input(
            "종료일",
            format="YYYY-MM-DD",
            key=end_key,
            help="보유잔고·원가합은 종료일 기준 FIFO로 계산합니다. 날짜 변경 후 조회를 눌러 주세요.",
        )
    with d3:
        do_query = st.button(
            "조회",
            type="primary",
            use_container_width=True,
            key=query_btn_key,
        )

    applied = st.session_state[applied_key]
    applied_start = _as_date(applied["start"])
    applied_end = _as_date(applied["end"])
    draft_changed = (draft_start != applied_start) or (draft_end != applied_end)

    if draft_start > draft_end:
        st.caption("시작일이 종료일보다 늦습니다. 조회 시 순서를 바꿔 적용합니다.")
    elif draft_changed:
        st.caption(
            f"선택 중: **{draft_start.isoformat()} ~ {draft_end.isoformat()}** "
            "· 조회를 누르면 반영됩니다."
        )
    else:
        st.caption(
            f"적용 중: **{applied_start.isoformat()} ~ {applied_end.isoformat()}**"
        )

    if do_query:
        q_start, q_end = draft_start, draft_end
        if q_start > q_end:
            q_start, q_end = q_end, q_start
        st.session_state[applied_key] = {"start": q_start, "end": q_end}
        st.rerun()

    start_date = applied_start
    end_date = applied_end
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    # 종료일까지 이력 → 잔고/FIFO, 기간 내 거래 → 건수·처분·상세 표
    trades_asof = _filter_trades_by_date_range(all_trades, None, end_date)
    trades_period = _filter_trades_by_date_range(all_trades, start_date, end_date)
    positions, sells_asof, warnings = compute_positions(trades_asof)
    sells_period = [
        s
        for s in sells_asof
        if start_date.isoformat() <= str(s.trade_date)[:10] <= end_date.isoformat()
    ]

    st.session_state["_dash_period"] = {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "market": market,
    }

    # 종목명 링크(?stock=...) 클릭 → 상세 모달 (표=기간, FIFO=종료일까지)
    _open_stock_detail_from_query(
        storage,
        trades_asof,
        period_trades=trades_period,
    )

    held_positions = [p for p in positions if p.quantity > 1e-12]
    held_stocks = {(p.business_id, p.stock_id) for p in held_positions}
    total_cost = sum(p.total_cost for p in held_positions)
    total_disposal = sum(
        float(getattr(s, "disposal_pnl", s.realized_pnl) or 0) for s in sells_period
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("기간 거래 건수", f"{len(trades_period):,}")
    c2.metric("보유 종목 수", f"{len(held_stocks):,}")
    c3.metric("잔고 원가합", money(total_cost))
    c4.metric("기간 처분손익", money(total_disposal))

    if warnings:
        st.warning(
            "선입선출 매수 수량이 부족한 매도가 있습니다. "
            "이전 매수(또는 기초잔고)를 등록해야 원가가 맞습니다.\n\n"
            + "\n".join(f"- {w}" for w in warnings)
        )

    st.subheader("보유 잔고")

    held_positions = [p for p in positions if p.quantity > 1e-12]
    if not held_positions and not any(abs(p.realized_pnl) > 1e-9 for p in positions):
        st.info("선택한 종료일 기준 보유 잔고가 없습니다.")
        return

    tab_broker, tab_total = st.tabs(
        ["🏦 증권사별 보유", "📊 종목 합산 (전체)"]
    )

    with tab_broker:
        st.caption("증권사(계좌) × 종목 단위 FIFO 잔고 — 종목명 클릭 시 상세 내역")
        pos_df = positions_to_dataframe(positions)
        if pos_df.empty:
            st.info("표시할 증권사별 잔고가 없습니다.")
        else:
            for col in ("잔여수량", "평단가", "원가잔액", "누적실현손익"):
                if col in pos_df.columns:
                    pos_df[col] = pd.to_numeric(pos_df[col], errors="coerce")
            _render_holdings_html_table(
                pos_df,
                active_menu=st.session_state.get("active_menu"),
                market=market,
                show_broker=True,
            )

    with tab_total:
        st.caption(
            "동일 종목을 증권사와 무관하게 합산 — 종목명 클릭 시 상세 내역"
        )
        agg = aggregate_positions_by_stock(positions)
        agg_df = positions_to_dataframe(agg)
        if agg_df.empty:
            st.info("합산할 잔고가 없습니다.")
        else:
            for col in ("잔여수량", "평단가", "원가잔액", "누적실현손익"):
                if col in agg_df.columns:
                    agg_df[col] = pd.to_numeric(agg_df[col], errors="coerce")
            _render_holdings_html_table(
                agg_df,
                active_menu=st.session_state.get("active_menu"),
                market=market,
                show_broker=True,
            )


def _fx_to_krw(amount_fx: float, fx_rate: float) -> float:
    return float(amount_fx or 0) * float(fx_rate or 0)


def _ov_row_float(row: object, key: str) -> float:
    """pandas Series/dict에서 숫자 안전 추출 (NA·None → 0)."""
    try:
        val = row.get(key) if hasattr(row, "get") else None  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        val = None
    if val is None:
        return 0.0
    try:
        if pd.isna(val):
            return 0.0
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _ov_resolve_preview_df(*candidates: object):
    """비어 있지 않은 미리보기 DataFrame을 후보에서 고른다."""
    for cand in candidates:
        if cand is None:
            continue
        try:
            if getattr(cand, "empty", True):
                continue
            if len(cand) <= 0:  # type: ignore[arg-type]
                continue
            return cand
        except Exception:  # noqa: BLE001
            continue
    return None


def _ov_broker_queue_apply() -> None:
    """일괄 등록 버튼 on_click: 등록 플래그 + 직전 세션 미리보기 스냅샷."""
    snap = st.session_state.get("ov_broker_df")
    st.session_state["ov_broker_apply_snapshot"] = (
        snap.copy(deep=True) if snap is not None else None
    )
    st.session_state["ov_broker_do_apply"] = True
    # 환율 재계산 리런과 버튼이 겹쳐도 등록이 유실되지 않게 함
    st.session_state["ov_broker_apply_pending"] = True


def page_overseas_trades(storage: Storage, business_id: int | None) -> None:
    """해외주식 매매 입력 (외화·환율)."""
    st.caption("시장: **해외주식** · 단가/수수료는 외화, 장부는 환율 적용 원화로 저장합니다.")
    render_overseas_trade_input(storage, business_id)
    st.divider()
    _render_overseas_trade_list(storage, business_id)


def render_overseas_trade_input(storage: Storage, business_id: int | None) -> None:
    businesses = storage.list_businesses()
    stocks = storage.list_stocks(business_id, market=MARKET_OVERSEAS)

    if not businesses:
        st.warning("사이드바 ＋ 버튼으로 사업자를 먼저 추가하세요.")
        return
    if business_id is None:
        st.warning("사이드바에서 사업자를 선택한 뒤 입력해 주세요. (전체 제외)")
        return

    business = next(b for b in businesses if b.id == business_id)
    st.markdown(f"**선택된 사업자:** `{business.name}`")

    if st.session_state.pop("_show_ov_trade_toast", False):
        st.toast(st.session_state.pop("_ov_trade_toast_msg", "저장되었습니다."))

    row1 = st.columns(3)
    with row1[0]:
        trade_date = st.date_input(
            "거래일자",
            value=date.today(),
            format="YYYY/MM/DD",
            key="ov_trade_date",
        )
    with row1[1]:
        currency = st.selectbox(
            "통화코드",
            list(FX_CURRENCIES),
            index=0,
            key="ov_currency",
        )
    with row1[2]:
        side_label = st.radio(
            "거래유형",
            ["해외매수", "해외매도", "외화배당"],
            horizontal=True,
            key="ov_trade_side",
        )

    ticker = st.text_input(
        "종목 선택 / 티커",
        value=st.session_state.get("_ov_preferred_ticker", ""),
        placeholder="예: NVDA, QQQ, VOO",
        key="ov_ticker",
    ).strip().upper()
    stock_name = st.text_input(
        "종목명 (선택)",
        value="",
        placeholder="비우면 티커를 종목명으로 사용",
        key="ov_stock_name",
    ).strip()

    accounts = storage.list_accounts(business_id, market=MARKET_OVERSEAS)
    acct_names = [a.name for a in accounts]
    broker_choice = st.selectbox(
        "증권사 / 계좌",
        ["(직접 입력)", *acct_names],
        key="ov_broker_select",
        help="동일 종목이어도 증권사별로 FIFO·잔고가 분리됩니다.",
    )
    broker_typed = ""
    if broker_choice == "(직접 입력)":
        broker_typed = st.text_input(
            "증권사명",
            value=st.session_state.get("_ov_preferred_broker", ""),
            placeholder="예: 미래에셋증권, KB증권, 메리츠증권",
            key="ov_broker_typed",
        ).strip()
    broker_name = (
        broker_typed if broker_choice == "(직접 입력)" else broker_choice
    )

    c_qty, c_price, c_fee, c_tax = st.columns(4)
    with c_qty:
        quantity = st.number_input(
            "거래수량",
            min_value=0.0,
            value=0.0,
            step=0.01,
            format="%.4f",
            key="ov_qty",
        )
    with c_price:
        price_fx = st.number_input(
            f"외화 단가 ({currency})",
            min_value=0.0,
            value=0.0,
            step=0.0001,
            format="%.4f",
            key="ov_price_fx",
        )
    with c_fee:
        fee_fx = st.number_input(
            f"외화 수수료 ({currency})",
            min_value=0.0,
            value=0.0,
            step=0.01,
            format="%.4f",
            key="ov_fee_fx",
        )
    with c_tax:
        tax_fx = st.number_input(
            f"외화 제세금 ({currency})",
            min_value=0.0,
            value=0.0,
            step=0.01,
            format="%.4f",
            key="ov_tax_fx",
        )

    fx_rate = st.number_input(
        f"적용 환율 (₩/{currency})",
        min_value=0.0,
        value=float(st.session_state.get("_ov_last_fx", 1400.0)),
        step=0.1,
        format="%.2f",
        key="ov_fx_rate",
        help="예: 1467.30",
    )

    # 실시간 미리보기
    if side_label == "외화배당":
        gross_fx = float(price_fx) * (float(quantity) if quantity > 0 else 1.0)
        total_fx = gross_fx + float(fee_fx)  # tax는 원천징수로 별도 표기
        preview_note = "배당 총액(외화, 세전 단가×수량)"
    elif side_label == "해외매수":
        gross_fx = float(quantity) * float(price_fx)
        total_fx = gross_fx + float(fee_fx) + float(tax_fx)
        preview_note = "외화 총 지급액 = 수량×단가 + 수수료 + 제세금"
    else:
        gross_fx = float(quantity) * float(price_fx)
        total_fx = gross_fx - float(fee_fx) - float(tax_fx)
        preview_note = "외화 순수령액 = 수량×단가 − 수수료 − 제세금"

    total_krw = _fx_to_krw(total_fx, fx_rate)
    gross_krw = _fx_to_krw(gross_fx, fx_rate)

    p1, p2, p3 = st.columns(3)
    p1.info(f"**외화 총액**\n\n{total_fx:,.4f} {currency}\n\n_{preview_note}_")
    p2.info(f"**원화 환산 (총액)**\n\n₩ {total_krw:,.0f}\n\n_{total_fx:,.4f} × {fx_rate:,.2f}_")
    p3.info(f"**원화 환산 (원금)**\n\n₩ {gross_krw:,.0f}\n\n_{gross_fx:,.4f} × {fx_rate:,.2f}_")

    if st.button("💾 해외주식 거래 저장", type="primary", use_container_width=True, key="ov_save"):
        try:
            if not ticker:
                raise ValueError("티커(종목코드)를 입력하세요.")
            if fx_rate <= 0:
                raise ValueError("적용 환율을 입력하세요.")
            q = float(quantity)
            if side_label == "외화배당":
                if float(price_fx) <= 0:
                    raise ValueError("배당 금액(외화 단가)을 입력하세요.")
                if q <= 0:
                    q = 1.0
                side = "DIVIDEND"
            else:
                if q <= 0:
                    raise ValueError("거래수량을 입력하세요.")
                if float(price_fx) <= 0:
                    raise ValueError("외화 단가를 입력하세요.")
                side = "BUY" if side_label == "해외매수" else "SELL"

            name = stock_name or next(
                (s.name for s in stocks if s.code.upper() == ticker), ticker
            )
            stock = storage.get_or_create_stock(
                ticker, name, business_id=int(business_id), market=MARKET_OVERSEAS
            )
            if not broker_name:
                raise ValueError("증권사(계좌)를 선택하거나 입력하세요.")
            acct = storage.get_or_create_account(
                broker_name,
                int(business_id),
                market=MARKET_OVERSEAS,
            )

            price_krw = _fx_to_krw(price_fx, fx_rate)
            fee_krw = _fx_to_krw(fee_fx, fx_rate)
            tax_krw = _fx_to_krw(tax_fx, fx_rate)
            if side == "BUY":
                settle = q * price_krw + fee_krw + tax_krw
            elif side == "SELL":
                settle = q * price_krw - fee_krw - tax_krw
            else:
                settle = q * price_krw - tax_krw

            trade = Trade(
                id=None,
                trade_date=trade_date.isoformat(),
                business_id=int(business_id),
                stock_id=int(stock.id),  # type: ignore[arg-type]
                side=side,  # type: ignore[arg-type]
                quantity=q,
                price=price_krw,
                fee=fee_krw,
                tax=tax_krw,
                settlement_amount=settle,
                memo=f"{side_label} {currency}@{fx_rate:g}",
                source="manual-overseas",
                currency=normalize_currency(currency),
                fx_rate=float(fx_rate),
                price_fx=float(price_fx),
                fee_fx=float(fee_fx),
                tax_fx=float(tax_fx),
                account_id=int(acct.id) if acct.id is not None else None,
                account_name=acct.name,
            )
            storage.add_trade(trade)
            st.session_state["_ov_last_fx"] = float(fx_rate)
            st.session_state["_ov_preferred_ticker"] = ticker
            st.session_state["_ov_preferred_broker"] = broker_name
            st.session_state["_show_ov_trade_toast"] = True
            st.session_state["_ov_trade_toast_msg"] = (
                f"✅ {side_label} 저장 · {acct.name} · {ticker} {q:g}주 · ₩{settle:,.0f}"
            )
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))


def _render_overseas_trade_list(storage: Storage, business_id: int | None) -> None:
    st.subheader("해외주식 거래 내역")
    trades = storage.list_trades(business_id=business_id, market=MARKET_OVERSEAS)
    if not trades:
        st.info("해외주식 거래 내역이 없습니다.")
        return
    _, sells, _ = compute_positions(trades)
    by_id = sells_by_trade_id(sells)
    rows = []
    for t in reversed(trades[-200:]):
        sell = None
        try:
            if t.id is not None:
                sell = by_id.get(int(t.id))
        except (TypeError, ValueError):
            sell = None
        pnl = None
        if sell is not None:
            pnl = float(getattr(sell, "disposal_pnl", sell.realized_pnl) or 0)
        rows.append(
            {
                "ID": t.id,
                "거래일자": t.trade_date,
                "증권사": getattr(t, "account_name", "") or "미지정",
                "유형": (
                    "매수"
                    if t.side == "BUY"
                    else ("매도" if t.side == "SELL" else "배당")
                ),
                "티커": t.stock_code,
                "종목명": t.stock_name,
                "수량": t.quantity,
                "거래금액(원가)": float(t.quantity or 0) * float(t.price or 0),
                "외화단가": getattr(t, "price_fx", 0) or 0,
                "환율": getattr(t, "fx_rate", 0) or 0,
                "통화": getattr(t, "currency", "") or "",
                "원화단가": t.price,
                "원화수수료": t.fee,
                "원화장산": t.settlement_amount,
                "처분손익": pnl,
            }
        )
    odf = pd.DataFrame(rows)
    st.dataframe(
        odf,
        use_container_width=True,
        hide_index=True,
        column_config={
            "수량": st.column_config.NumberColumn("수량", format="%,.4f"),
            "거래금액(원가)": st.column_config.NumberColumn(
                "거래금액(원가)", format="%,d 원", help="수량 × 원화단가"
            ),
            "외화단가": st.column_config.NumberColumn("외화단가", format="%.4f"),
            "환율": st.column_config.NumberColumn("환율", format="%.2f"),
            "원화단가": st.column_config.NumberColumn("원화단가", format="%,d 원"),
            "원화수수료": st.column_config.NumberColumn("원화수수료", format="%,d 원"),
            "원화장산": st.column_config.NumberColumn("원화장산", format="%,d 원"),
            "처분손익": st.column_config.NumberColumn("처분손익", format="%,d 원"),
            "ID": st.column_config.NumberColumn("ID", format="%d"),
        },
    )

def page_trades(
    storage: Storage,
    business_id: int | None,
    market: str = MARKET_DOMESTIC,
) -> None:
    market = normalize_market(market)
    if market == MARKET_OVERSEAS:
        page_overseas_trades(storage, business_id)
        return
    st.caption(f"시장: **{market_label(market)}**")
    # 국내 매매 입력 (기존)
    # ---- 위젯 생성 전: 저장 직후 초기화/토스트 반영 ----
    pending = st.session_state.pop("_pending_trade_reset", None)
    if pending:
        for k in pending.get("clear_keys", []):
            st.session_state.pop(k, None)
        next_stock = pending.get("stock")
        if next_stock:
            st.session_state["_preferred_stock"] = next_stock

    if st.session_state.pop("_show_trade_toast", False):
        st.toast(
            st.session_state.pop(
                "_trade_toast_msg",
                "✅ 거래가 성공적으로 저장되었습니다!",
            )
        )

    businesses = storage.list_businesses()
    stocks = storage.list_stocks(business_id, market=market)

    st.subheader("매수/매도 입력")

    if not businesses:
        st.warning("사이드바 사업자 선택 옆 **＋** 버튼으로 사업자를 먼저 추가하세요.")
        return

    if business_id is None:
        st.warning("사이드바 **사업자 선택**에서 입력할 사업자를 먼저 선택해 주세요. (전체 제외)")
        _render_trade_list(storage, business_id, market=market)
        return

    business = next(b for b in businesses if b.id == business_id)

    st.markdown(
        f"""
        <div style="
            display:inline-flex;align-items:center;gap:0.5rem;
            padding:0.45rem 0.9rem;border-radius:999px;
            background:linear-gradient(90deg,#eef5ff,#f7faff);
            border:1px solid #c9dcff;color:#1f3b66;
            font-size:0.95rem;font-weight:600;margin-bottom:0.75rem;">
            <span style="opacity:0.75;font-weight:500;">선택된 사업자</span>
            <span>{business.name}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 거래일자 / 거래유형 — 폼 밖 (연속 입력 시 유지)
    row1_l, row1_r = st.columns(2, gap="large")
    with row1_l:
        trade_date = st.date_input(
            "거래일자",
            value=date.today(),
            format="YYYY/MM/DD",
            key="trade_date",
        )
    with row1_r:
        side_label = st.radio(
            "거래 유형",
            ["매수", "매도"],
            horizontal=True,
            key="trade_side",
        )

    accounts = storage.list_accounts(business_id, market=market)
    acct_names = [a.name for a in accounts]
    broker_choice = st.selectbox(
        "증권사 / 계좌",
        ["(직접 입력)", *acct_names],
        key="trade_broker_select",
        help="동일 종목이어도 증권사별로 FIFO·잔고가 분리됩니다.",
    )
    broker_typed = ""
    if broker_choice == "(직접 입력)":
        broker_typed = st.text_input(
            "증권사명",
            value=st.session_state.get("_preferred_broker", ""),
            placeholder="예: 키움증권, DB금융투자, 미래에셋증권",
            key="trade_broker_typed",
        ).strip()
    broker_name = (
        broker_typed if broker_choice == "(직접 입력)" else broker_choice
    )

    stock_options = [NEW_STOCK_OPTION, *[f"{s.name} ({s.code})" for s in stocks]]
    preferred = st.session_state.get("_preferred_stock")
    default_idx = (
        stock_options.index(preferred) if preferred in stock_options else 0
    )

    # 수량·단가·수수료·메모 — 폼 안 (제출 시 clear_on_submit으로 초기화)
    with st.form("trade_input_form", clear_on_submit=True):
        st.markdown("**종목**")
        stock_label = st.selectbox(
            "종목 선택",
            stock_options,
            index=default_idx,
        )
        new_name = st.text_input(
            "신규 종목명",
            placeholder="신규 종목 선택 시에만 입력 (예: 삼성전자)",
            help="'➕ 신규 종목 직접 입력' 선택 시 필수입니다.",
        )
        is_new_stock = stock_label == NEW_STOCK_OPTION

        st.markdown("**수량 · 단가 · 수수료**" + (" · 제세금" if side_label == "매도" else ""))
        if side_label == "매도":
            q_col, p_col, f_col, t_col = st.columns(4, gap="large")
        else:
            q_col, p_col, f_col = st.columns(3, gap="large")
            t_col = None
        with q_col:
            quantity = st.number_input(
                "수량 (주)",
                min_value=0,
                step=1,
                value=0,
                format="%d",
            )
        with p_col:
            price = st.number_input(
                "단가 (원)",
                min_value=0,
                step=100,
                value=0,
                format="%d",
            )
        with f_col:
            fee = st.number_input(
                "수수료 (원)",
                min_value=0,
                step=10,
                value=0,
                format="%d",
            )
        tax = 0
        if t_col is not None:
            with t_col:
                tax = st.number_input(
                    "제세금 (원)",
                    min_value=0,
                    step=10,
                    value=0,
                    format="%d",
                    key="trade_tax_input",
                )

        memo = st.text_input("메모 (선택)", value="", placeholder="비고를 입력하세요")
        submitted = st.form_submit_button(
            "거래 저장",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        try:
            if quantity <= 0:
                raise ValueError("수량은 1주 이상 입력하세요.")
            if price < 0:
                raise ValueError("단가를 확인하세요.")

            if is_new_stock:
                if not str(new_name).strip():
                    raise ValueError("신규 종목명을 입력하세요.")
                stock = storage.get_or_create_stock_by_name(
                    str(new_name).strip(),
                    business_id=int(business.id),
                    market=market,
                )
            else:
                stock = next(
                    s for s in stocks if f"{s.name} ({s.code})" == stock_label
                )

            side = "BUY" if side_label == "매수" else "SELL"
            tax_val = float(tax) if side == "SELL" else 0.0
            settlement = (
                float(quantity) * float(price) + float(fee)
                if side == "BUY"
                else float(quantity) * float(price) - float(fee)
            )
            if not broker_name:
                raise ValueError("증권사(계좌)를 선택하거나 입력하세요.")
            acct = storage.get_or_create_account(
                broker_name,
                int(business.id),  # type: ignore[arg-type]
                market=market,
            )

            trade = Trade(
                id=None,
                trade_date=trade_date.isoformat(),
                business_id=business.id,  # type: ignore[arg-type]
                stock_id=stock.id,  # type: ignore[arg-type]
                side=side,  # type: ignore[arg-type]
                quantity=float(quantity),
                price=float(price),
                fee=float(fee),
                tax=tax_val,
                settlement_amount=float(settlement),
                memo=str(memo or ""),
                source="manual",
                created_at=now_str(),
                account_id=int(acct.id) if acct.id is not None else None,
                account_name=acct.name,
            )
            tid = storage.add_trade(trade)
            st.session_state["_preferred_broker"] = broker_name

            toast_msg = f"✅ 거래 저장 · {acct.name}"
            if side == "SELL":
                preview = storage.list_trades(
                    business_id=business.id, stock_id=stock.id
                )
                _, sells, _warnings = compute_positions(preview)
                if sells:
                    last = sells[-1]
                    pnl = float(getattr(last, "disposal_pnl", last.realized_pnl) or 0)
                    toast_msg += f" · 처분손익 {money(pnl)}원"

            st.session_state["_trade_toast_msg"] = toast_msg
            st.session_state["_show_trade_toast"] = True
            # 일자/유형 유지 · 수량·단가·수수료·메모는 form clear_on_submit
            # 종목은 연속 입력을 위해 유지
            st.session_state["_pending_trade_reset"] = {
                "stock": f"{stock.name} ({stock.code})",
                "clear_keys": [],
            }
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    st.divider()
    _render_trade_list(storage, business_id, market=market)


def _render_trade_list(
    storage: Storage,
    business_id: int | None,
    market: str = MARKET_DOMESTIC,
) -> None:
    st.subheader("거래 내역")
    trades = storage.list_trades(business_id=business_id, market=market)
    df = trades_to_dataframe(trades)
    if df.empty:
        st.info("거래 내역이 없습니다.")
        return

    _, sells, _ = compute_positions(trades)
    df = _attach_disposal_pnl(df, sells)
    view = df.drop(columns=["출처"], errors="ignore").copy()
    for col in ("수량", "거래금액(원가)", "단가", "수수료", "제세금", "정산금액", "처분손익", "ID"):
        if col in view.columns:
            view[col] = pd.to_numeric(view[col], errors="coerce")

    # 표시 순서: 수량 → 거래금액(원가) → 단가 …
    preferred = [
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
        "처분손익",
        "메모",
        "ID",
    ]
    ordered = [c for c in preferred if c in view.columns]
    ordered += [c for c in view.columns if c not in ordered]
    view = view.loc[:, ordered]

    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        column_config=trade_table_column_config(),
    )

    del_id = st.number_input("삭제할 거래 ID", min_value=0, step=1, value=0, key="del_trade_id")
    if st.button("선택 거래 삭제", type="secondary"):
        if del_id > 0:
            storage.delete_trade(int(del_id))
            st.success(f"거래 #{int(del_id)} 삭제")
            st.rerun()


def _apply_export_memo_mode(trades_df: pd.DataFrame, memo_mode: str) -> pd.DataFrame:
    """원본 거래 DF에서 적요/메모를 선택 형식으로 재생성.

    memo_mode: 'stock' | 'amount'
    - 엑셀·CSV 모두 '적요' 컬럼을 쓰며, 기존 '메모'는 동일 값으로 맞춤.
    - amount 모드는 최장 적요 길이에 공백 패딩.
    """
    if trades_df is None or trades_df.empty:
        return trades_df
    out = trades_df.copy()
    mode = (memo_mode or "stock").strip().lower()
    if mode == "amount":
        raw = [_format_memo_ccy_amount_qty(row) for _, row in out.iterrows()]
        memos = _pad_memo_strings(raw)
    else:
        memos = [_format_memo_stock_qty_fx(row) for _, row in out.iterrows()]

    out["적요"] = memos
    out["메모"] = memos
    return out


_IE_MEMO_OPTIONS: dict[str, str] = {
    "옵션1: 종목명 / @수량 * 단가 * 환율": "stock",
    "옵션2: 통화·외화총액 / 수량*단가 * 환율": "amount",
}


def _resolve_ie_memo_mode(market: str, label: object | None = None) -> str:
    """라디오·session_state에서 적요 모드(stock|amount) 확정."""
    key = f"ie_memo_fmt_v3_{normalize_market(market)}"
    raw = label if label is not None else st.session_state.get(key)
    if raw in _IE_MEMO_OPTIONS:
        return _IE_MEMO_OPTIONS[raw]
    text = str(raw or "")
    if "옵션2" in text or "외화총액" in text or "통화" in text:
        return "amount"
    return "stock"


def _build_ledger_excel_bytes(
    trades_df: pd.DataFrame,
    pos_df: pd.DataFrame,
    sell_df: pd.DataFrame,
    memo_mode: str,
) -> bytes:
    """엑셀 바이트 생성 직전 적요를 선택 형식으로 재적용."""
    export_df = _apply_export_memo_mode(trades_df, memo_mode)
    # 다운로드 시트에는 적요만 노출 (메모 중복 제거, 메모 자리에 적요)
    if export_df is not None and not export_df.empty and "적요" in export_df.columns:
        ordered: list[str] = []
        for c in export_df.columns:
            if c == "메모":
                if "적요" not in ordered:
                    ordered.append("적요")
                continue
            if c not in ordered:
                ordered.append(c)
        if "적요" not in ordered:
            ordered.append("적요")
        export_df = export_df.loc[:, [c for c in ordered if c in export_df.columns]]
    return export_to_excel_bytes(
        export_df,
        pos_df,
        sell_df,
        text_columns=["적요"],
    )


def page_excel_download(
    storage: Storage,
    market: str = MARKET_DOMESTIC,
) -> None:
    """엑셀 다운로드(적요 형식·회계 전표). 구 page_import_export."""
    market = normalize_market(market)
    st.subheader("엑셀 다운로드")
    st.caption("기간을 선택한 뒤 회계 전표 엑셀을 내려받습니다.")

    business_id = st.session_state.get("active_business_id")
    trades = storage.list_trades(business_id=business_id, market=market)
    _, _, fifo_warnings = compute_positions(trades)

    company_name = ""
    if business_id is not None:
        for b in storage.list_businesses():
            if b.id == business_id:
                company_name = b.name
                break

    # ── 적요 형식 ──────────────────────────────────────────────
    st.subheader("적요 형식")
    radio_key = f"ie_memo_fmt_v3_{market}"
    memo_labels = list(_IE_MEMO_OPTIONS.keys())
    memo_mode_label = st.radio(
        "적요 형식",
        options=memo_labels,
        index=0,
        horizontal=False,
        key=radio_key,
        label_visibility="collapsed",
        help=(
            "회계 전표 엑셀의 적요에 적용됩니다. "
            "옵션2는 '/' 앞부분(통화·금액)을 최장 길이에 맞춰 공백 패딩합니다."
        ),
    )
    memo_mode = _resolve_ie_memo_mode(market, memo_mode_label)
    st.session_state[f"ie_memo_mode_resolved_{market}"] = memo_mode
    resolved = st.session_state.get(f"ie_memo_mode_resolved_{market}", memo_mode)
    if memo_mode == "stock":
        st.caption("예: INVESCO NASDAQ 100 매수 / @40주 * $247.67 * 1442.00원")
    else:
        st.caption("예: USD 1862.29 / 5주*371.529 * 1528.6  ('/' 앞부분 공백 패딩)")

    if fifo_warnings:
        st.caption(
            f"⚠️ FIFO 매수 수량 부족 {len(fifo_warnings)}건 · "
            + " · ".join(str(w) for w in fifo_warnings[:3])
            + (" …" if len(fifo_warnings) > 3 else "")
        )

    # ── 조회 기간 ──────────────────────────────────────────────
    today = date.today()
    if "voucher_date_range" in st.session_state and "voucher_start_date" not in st.session_state:
        old = st.session_state.pop("voucher_date_range", None)
        if isinstance(old, (list, tuple)) and len(old) == 2:
            st.session_state["voucher_start_date"] = old[0]
            st.session_state["voucher_end_date"] = old[1]
        else:
            st.session_state.pop("voucher_date_range", None)
    if "voucher_start_date" not in st.session_state:
        st.session_state["voucher_start_date"] = date(today.year, 1, 1)
    if "voucher_end_date" not in st.session_state:
        st.session_state["voucher_end_date"] = today

    st.subheader("조회 기간")
    p1, p2, p3 = st.columns(3)
    if p1.button("이번 달", use_container_width=True, key=f"voucher_preset_month_{market}"):
        st.session_state["voucher_start_date"] = date(today.year, today.month, 1)
        st.session_state["voucher_end_date"] = today
        st.rerun()
    if p2.button("올해", use_container_width=True, key=f"voucher_preset_year_{market}"):
        st.session_state["voucher_start_date"] = date(today.year, 1, 1)
        st.session_state["voucher_end_date"] = today
        st.rerun()
    if p3.button("전체", use_container_width=True, key=f"voucher_preset_all_{market}"):
        if trades:
            dates = sorted(str(t.trade_date)[:10] for t in trades)
            st.session_state["voucher_start_date"] = date.fromisoformat(dates[0])
            st.session_state["voucher_end_date"] = date.fromisoformat(dates[-1])
        else:
            st.session_state["voucher_start_date"] = date(2020, 1, 1)
            st.session_state["voucher_end_date"] = today
        st.rerun()

    d1, d2 = st.columns(2)
    with d1:
        start_date = st.date_input(
            "시작일",
            format="YYYY-MM-DD",
            key="voucher_start_date",
        )
    with d2:
        end_date = st.date_input(
            "종료일",
            format="YYYY-MM-DD",
            key="voucher_end_date",
        )
    if start_date > end_date:
        start_date, end_date = end_date, start_date
        st.caption("시작일이 종료일보다 늦어 순서를 바꿔 조회합니다.")

    period_trades = storage.get_trades_by_period(
        business_id, start_date, end_date, market=market
    )
    _, all_sells, _ = compute_positions(trades)
    account_config = storage.get_account_config(business_id, market=market)
    partner_by_stock_id = {
        int(s.id): (s.partner_code or "")
        for s in storage.list_stocks(business_id, market=market)
        if s.id is not None
    }

    # ── 다운로드 ────────────────────────────────────────────────
    st.markdown(
        """
<style>
div[data-testid="stDownloadButton"] button {
    border-radius: 10px !important;
    font-weight: 600 !important;
    font-size: 0.95rem !important;
    padding: 0.5rem 1.25rem !important;
    min-height: 2.4rem !important;
    border: 1px solid #cbd5e1 !important;
    background-color: #f8fafc !important;
    color: #334155 !important;
    box-shadow: none !important;
}
div[data-testid="stDownloadButton"] button:hover:not(:disabled) {
    background-color: #eff6ff !important;
    border-color: #93c5fd !important;
    color: #1d4ed8 !important;
}
div[data-testid="stDownloadButton"] button:disabled {
    opacity: 0.55 !important;
}
/* 엑셀 다운로드 페이지 · 섹션 간격 압축 */
div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stRadio"]),
div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stButton"]) {
    margin-bottom: 0.15rem;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    _l, mid, _r = st.columns([1.35, 1.3, 1.35])
    with mid:
        st.caption(
            "기간 선택 후 아래 버튼을 클릭하여 회계 전표 엑셀 파일을 다운로드하세요."
        )
        if not period_trades:
            st.download_button(
                "회계 전표 엑셀 다운로드",
                data=b"",
                file_name="empty.xlsx",
                disabled=True,
                use_container_width=True,
                type="secondary",
                key=f"voucher_dl_empty_{market}",
            )
            st.caption("선택한 기간에 거래가 없습니다.")
        else:
            voucher_bytes = export_voucher_excel_bytes(
                period_trades,
                all_sells,
                company_name=company_name,
                account_config=account_config,
                partner_by_stock_id=partner_by_stock_id,
                remark_mode=resolved,
            )
            filename = (
                f"엑셀자료일반전표전송_주식매매_"
                f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
            )
            st.download_button(
                "회계 전표 엑셀 다운로드",
                data=voucher_bytes,
                file_name=filename,
                mime=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"
                ),
                type="secondary",
                use_container_width=True,
                key=(
                    f"voucher_dl_{resolved}_{market}_"
                    f"{start_date}_{end_date}_{len(voucher_bytes)}"
                ),
            )


# 하위 호환 별칭
page_import_export = page_excel_download


def page_broker_overseas(storage: Storage) -> None:
    """해외주식 증권사 변환기: 파일 자동 식별 → 검수 후 일괄 등록."""
    from src.brokers.mirae_overseas import (
        apply_overseas_preview_fx,
        ensure_overseas_preview_columns,
        mirae_rows_to_preview_df,
    )

    st.subheader("해외주식 증권사 변환기")
    st.caption(
        "파일을 올리면 컬럼·파일명으로 증권사를 자동 인식합니다. "
        "지원: 미래에셋 PDF, KB·메리츠 해외주식 엑셀. "
        "인식되지 않을 때만 증권사를 선택하세요."
    )

    # 일괄 등록 직후 rerun 에서도 보이도록 플래시 메시지
    flash = st.session_state.pop("_ov_broker_flash", None)
    if isinstance(flash, dict):
        level = str(flash.get("level") or "success")
        text = str(flash.get("message") or "").strip()
        if text:
            if level == "error":
                st.error(text)
            elif level == "warning":
                st.warning(text)
            else:
                st.success(text)
            detail = flash.get("detail")
            if detail:
                with st.expander("상세", expanded=False):
                    if isinstance(detail, list):
                        for line in detail:
                            st.write(line)
                    else:
                        st.write(detail)

    businesses = storage.list_businesses()
    if not businesses:
        st.warning("사이드바 ＋ 버튼으로 사업자를 먼저 등록하세요.")
        return

    biz_names = [b.name for b in businesses]
    default_idx = 0
    active_id = st.session_state.get("active_business_id")
    if active_id is not None:
        for i, b in enumerate(businesses):
            if b.id == active_id:
                default_idx = i
                break

    biz = st.selectbox("반영할 사업자", biz_names, index=default_idx, key="ov_broker_biz")

    need_manual = bool(st.session_state.get("ov_broker_need_manual"))
    broker_hint = "자동 감지"
    if need_manual:
        broker_hint = st.selectbox(
            "증권사 (자동 인식 실패 — 선택 필요)",
            ["자동 감지", "미래에셋증권", "KB증권", "메리츠증권"],
            key="ov_broker_manual",
            help="파일 양식을 자동으로 찾지 못했습니다. 증권사를 고른 뒤 같은 파일을 다시 올려 주세요.",
        )

    # 파서 로직 변경 시 같은 파일도 재파싱 (세션에 옛 메리츠 결과 남는 문제 방지)
    OV_PARSE_VER = 3
    up_ver = int(st.session_state.get("ov_broker_up_ver") or 0)
    up = st.file_uploader(
        "해외주식 거래내역 (PDF / Excel)",
        type=["pdf", "xlsx", "xls"],
        key=f"ov_broker_up_{up_ver}",
        help="파일만 올리면 증권사를 자동 판별합니다.",
    )

    if up:
        token = f"{up.name}:{up.size}:{broker_hint}:v{OV_PARSE_VER}"
        if st.session_state.get("ov_broker_token") != token:
            name = up.name or ""
            file_bytes = up.getvalue()
            if need_manual and broker_hint != "자동 감지":
                result = parse_overseas_with_hint(file_bytes, name, broker_hint)
            else:
                result = detect_and_parse_overseas(file_bytes, name)

            identified = bool(result.get("identified")) and bool(result.get("rows"))
            # 인식은 됐지만 0건이면 미리보기는 비움 — 오판(예: 메리츠) 방지
            if result.get("broker_name") and not result.get("rows"):
                # 컬럼 불일치로 0건이면 미식별로 보고 수동 선택 유도
                if any("찾지 못했" in str(n) for n in (result.get("notes") or [])):
                    identified = False
                    result = {
                        **result,
                        "identified": False,
                        "notes": [
                            *[
                                n
                                for n in (result.get("notes") or [])
                                if "양식으로 인식" not in str(n)
                            ],
                            "증권사 자동 인식이 맞지 않은 것 같습니다. "
                            "아래에서 증권사를 선택한 뒤 같은 파일을 다시 올려 주세요.",
                        ],
                    }

            st.session_state.ov_broker_need_manual = not identified
            st.session_state.ov_broker_token = token
            st.session_state.ov_broker_notes = result.get("notes") or []
            st.session_state.ov_broker_source = result.get("source") or "overseas"
            st.session_state.ov_broker_name = result.get("broker_name")
            st.session_state.ov_broker_df = mirae_rows_to_preview_df(
                result.get("rows") or []
            )
            st.session_state.pop("ov_broker_editor", None)
            st.session_state.pop("ov_broker_editor_ver", None)

    for note in st.session_state.get("ov_broker_notes") or []:
        note_s = str(note)
        if "양식으로 인식" in note_s or "양식으로 처리" in note_s:
            st.success(note_s)
        elif "인식하지" in note_s or ("선택" in note_s and "증권사" in note_s):
            st.warning(note_s)
        else:
            st.info(note_s)

    df = st.session_state.get("ov_broker_df")
    if df is None or getattr(df, "empty", True):
        if st.session_state.get("ov_broker_need_manual"):
            st.warning("위에서 증권사를 선택한 뒤, 동일 파일을 다시 업로드해 주세요.")
        else:
            st.info("PDF 또는 엑셀을 업로드하면 자동 인식 후 미리보기가 표시됩니다.")
        return

    st.markdown("### 파싱 미리보기 (검수 및 수정)")
    st.caption(
        "적용환율이 0인 행은 **적용환율** 칸에 직접 입력하세요. "
        "입력 즉시 원화단가·외화거래금액·거래금액(원)·메모가 재계산됩니다. "
        "외화거래금액 = 수량 × 외화단가, "
        "거래금액(원) = 수량 × 외화단가 × 적용환율 (수수료·제세금 제외). "
        "아래 버튼은 미리보기 **전체 행**을 선택한 사업자로 등록합니다."
    )
    df = ensure_overseas_preview_columns(df)
    # 내부 계산용 수치는 에디터에 노출하지 않음
    editor_df = df.drop(columns=["외화거래금액_수치"], errors="ignore")
    editor_ver = int(st.session_state.get("ov_broker_editor_ver") or 0)
    edited = st.data_editor(
        editor_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"ov_broker_editor_{editor_ver}",
        column_config={
            "증권사": st.column_config.TextColumn("증권사", width="small"),
            "거래유형": st.column_config.SelectboxColumn(
                "거래유형",
                options=["해외매수", "해외매도", "외화배당"],
                required=True,
            ),
            "수량": st.column_config.NumberColumn("수량", format="%.4f"),
            "외화단가": st.column_config.NumberColumn("외화단가", format="%.4f"),
            "외화수수료": st.column_config.NumberColumn("외화수수료", format="%.4f"),
            "외화제세금": st.column_config.NumberColumn("외화제세금", format="%.4f"),
            "적용환율": st.column_config.NumberColumn(
                "적용환율",
                format="%.2f",
                min_value=0.0,
                step=0.1,
                help="0이면 직접 입력 → 원화·메모 자동 환산",
            ),
            "원화단가": st.column_config.NumberColumn(
                "원화단가", format="%.0f", help="외화단가 × 적용환율"
            ),
            "원화수수료": st.column_config.NumberColumn(
                "원화수수료", format="%.0f", help="외화수수료 × 적용환율"
            ),
            "원화재세금": st.column_config.NumberColumn(
                "원화재세금", format="%.0f", help="외화제세금 × 적용환율"
            ),
            "외화거래금액": st.column_config.TextColumn(
                "외화거래금액",
                help="수량 × 외화단가 (예: 5,588.40 USD). 원본 외화 거래금액이 있으면 우선",
                width="medium",
            ),
            "거래금액(원)": st.column_config.NumberColumn(
                "거래금액(원)",
                format="%.0f",
                help="수량 × 외화단가 × 적용환율 (수수료·제세금 제외)",
            ),
        },
        disabled=[
            "원화단가",
            "원화수수료",
            "원화재세금",
            "외화거래금액",
            "거래금액(원)",
        ],
    )

    recalced = apply_overseas_preview_fx(edited)
    # data_editor가 빈 DF를 반환하는 경우(위젯 리셋 경합) 세션 데이터를 지키다
    if recalced is None or getattr(recalced, "empty", True):
        if editor_df is not None and not getattr(editor_df, "empty", True):
            recalced = editor_df
            st.session_state.ov_broker_df = editor_df
        else:
            st.session_state.ov_broker_df = recalced
    else:
        st.session_state.ov_broker_df = recalced

    # 적용환율(사용자 입력)만 보고 에디터 새로고침 — 계산컬럼 dtype 차이로 인한 오탐 방지
    fx_changed = False
    try:
        if (
            recalced is not None
            and not getattr(recalced, "empty", True)
            and len(recalced) == len(editor_df)
        ):
            prev_fx = (
                pd.to_numeric(editor_df["적용환율"], errors="coerce")
                .fillna(0.0)
                .astype(float)
                .reset_index(drop=True)
            )
            new_fx = (
                pd.to_numeric(recalced["적용환율"], errors="coerce")
                .fillna(0.0)
                .astype(float)
                .reset_index(drop=True)
            )
            if not prev_fx.equals(new_fx):
                fx_changed = True
        elif (
            recalced is not None
            and not getattr(recalced, "empty", True)
            and len(recalced) != len(editor_df)
        ):
            # 행 추가/삭제 시에만 에디터 새로고침 (빈 DF 오탐은 제외)
            fx_changed = True
    except Exception:  # noqa: BLE001
        fx_changed = False

    if fx_changed:
        st.session_state.ov_broker_editor_ver = editor_ver + 1
        st.rerun()

    st.button(
        "선택된 사업자로 해외주식 거래 일괄 등록",
        type="primary",
        use_container_width=True,
        key="ov_broker_apply",
        on_click=_ov_broker_queue_apply,
    )

    do_apply = bool(
        st.session_state.pop("ov_broker_do_apply", False)
        or st.session_state.pop("ov_broker_apply_pending", False)
    )
    if not do_apply:
        return

    try:
        business = storage.get_or_create_business(biz)
        bid = int(business.id)  # type: ignore[arg-type]
        trades: list = []
        errors: list[str] = []
        skipped_div = 0

        snap = st.session_state.pop("ov_broker_apply_snapshot", None)
        # 이번 렌더 에디터 결과 우선, 없으면 on_click 스냅샷/세션
        to_save = _ov_resolve_preview_df(
            recalced,
            edited,
            snap,
            st.session_state.get("ov_broker_df"),
        )
        if to_save is None:
            err_msg = "등록 중 오류가 발생했습니다. 등록할 미리보기 행이 없습니다."
            st.error(
                f"{err_msg} "
                f"(snapshot={0 if snap is None else len(snap)}, "
                f"editor={0 if edited is None else len(edited)}, "
                f"session={0 if st.session_state.get('ov_broker_df') is None else len(st.session_state['ov_broker_df'])})"
            )
            st.toast(err_msg, icon="⚠️")
            return

        to_save = apply_overseas_preview_fx(to_save)
        st.session_state.ov_broker_df = to_save

        for idx, row in to_save.iterrows():
            row_label = str(int(idx) + 1) if isinstance(idx, (int, float)) else str(idx)
            try:
                ticker = str(row.get("종목코드") or "").strip().upper()
                name = str(row.get("종목명") or ticker).strip() or ticker
                if not ticker:
                    ticker = name[:12].upper() if name else ""
                if not ticker:
                    raise ValueError("종목코드 없음")
                qty = _ov_row_float(row, "수량")
                price_fx = _ov_row_float(row, "외화단가")
                fee_fx = _ov_row_float(row, "외화수수료")
                tax_fx = _ov_row_float(row, "외화제세금")
                fx = coerce_fx_rate(row.get("적용환율"))
                ccy = normalize_currency(str(row.get("통화코드") or "USD"))
                kind = str(row.get("거래유형") or "").strip()
                if "배당" in kind:
                    skipped_div += 1
                    continue
                # 해외매수/매도 및 '매수'/'매도' 포함 표기 허용
                side = normalize_side(kind)
                if qty <= 0 or price_fx < 0:
                    raise ValueError(f"수량/단가를 확인하세요. (수량={qty}, 단가={price_fx})")

                stock = storage.get_or_create_stock(
                    ticker, name, business_id=bid, market=MARKET_OVERSEAS
                )
                broker_raw = str(row.get("증권사") or "").strip()
                src_tag = str(st.session_state.get("ov_broker_source") or "")
                if "메리츠" in broker_raw or "meritz" in src_tag:
                    source = "broker:meritz-overseas"
                elif "KB" in broker_raw.upper() or "kb" in src_tag:
                    source = "broker:kb-overseas"
                else:
                    source = "broker:mirae-overseas"
                broker_name = broker_name_from_source(source, broker_raw) or "미지정"
                acct = storage.get_or_create_account(
                    broker_name, bid, market=MARKET_OVERSEAS
                )
                price_krw = _fx_to_krw(price_fx, fx)
                fee_krw = _fx_to_krw(fee_fx, fx)
                tax_krw = _fx_to_krw(tax_fx, fx)
                if side == "BUY":
                    settle = qty * price_krw + fee_krw + tax_krw
                elif side == "SELL":
                    settle = qty * price_krw - fee_krw - tax_krw
                else:
                    settle = qty * price_krw - tax_krw

                trades.append(
                    Trade(
                        id=None,
                        trade_date=str(row.get("거래일자") or "")[:10],
                        business_id=bid,
                        stock_id=int(stock.id),  # type: ignore[arg-type]
                        side=side,  # type: ignore[arg-type]
                        quantity=qty,
                        price=price_krw,
                        fee=fee_krw,
                        tax=tax_krw,
                        settlement_amount=settle,
                        memo=str(row.get("메모") or "overseas-broker"),
                        source=source,
                        currency=ccy,
                        fx_rate=fx,
                        price_fx=price_fx,
                        fee_fx=fee_fx,
                        tax_fx=tax_fx,
                        account_id=int(acct.id) if acct.id is not None else None,
                        account_name=acct.name,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"행 {row_label}: {exc}")

        if not trades:
            err_msg = (
                "등록 중 오류가 발생했습니다. 저장할 매수/매도 행이 없습니다. "
                f"(미리보기 {len(to_save)}행 · 배당스킵 {skipped_div}건 · 오류 {len(errors)}건)"
            )
            st.error(err_msg)
            st.toast(err_msg, icon="⚠️")
            if errors:
                with st.expander("오류 상세", expanded=True):
                    for e in errors:
                        st.write(e)
            else:
                st.warning(
                    "모든 행이 배당으로 스킵되었거나 거래유형이 비어 있습니다. "
                    "미리보기의 거래유형(해외매수/해외매도)을 확인하세요."
                )
            return

        storage.add_trades_bulk(trades)
        zero_fx_n = sum(1 for t in trades if float(getattr(t, "fx_rate", 0) or 0) <= 0)
        msg = f"✅ 등록이 완료되었습니다! {len(trades)}건 반영 → {biz}"
        if zero_fx_n:
            msg += f" (환율 0 등록 {zero_fx_n}건 · 임의 환산 없음)"
        if skipped_div:
            msg += f" · 배당스킵 {skipped_div}건"

        # rerun 이후에도 보이도록 세션에 남김 (즉시 success는 rerun에 가려짐)
        st.session_state["_ov_broker_flash"] = {
            "level": "success",
            "message": msg,
            "detail": errors if errors else None,
        }
        st.session_state["_pending_toast"] = msg

        for k in (
            "ov_broker_token",
            "ov_broker_notes",
            "ov_broker_df",
            "ov_broker_source",
            "ov_broker_editor_ver",
            "ov_broker_apply_snapshot",
            "ov_broker_do_apply",
            "ov_broker_apply_pending",
        ):
            st.session_state.pop(k, None)
        # 버전 키 기반 에디터·업로더 위젯 상태 정리 (새 파일 업로드 가능)
        for k in list(st.session_state.keys()):
            if str(k).startswith("ov_broker_editor") or str(k).startswith("ov_broker_up_"):
                st.session_state.pop(k, None)
        st.session_state["ov_broker_up_ver"] = (
            int(st.session_state.get("ov_broker_up_ver") or 0) + 1
        )
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        import traceback

        err_msg = (
            "등록 중 오류가 발생했습니다. 다시 시도해 주세요.\n"
            f"원인: {exc}"
        )
        st.error(err_msg)
        st.toast("등록 중 오류가 발생했습니다. 다시 시도해 주세요.", icon="❌")
        st.session_state["_ov_broker_flash"] = {
            "level": "error",
            "message": err_msg,
            "detail": traceback.format_exc(),
        }
        with st.expander("예외 상세", expanded=True):
            st.code(traceback.format_exc())
        # 실패 시에도 등록 플래그만 정리 (미리보기는 유지)
        for k in (
            "ov_broker_do_apply",
            "ov_broker_apply_pending",
            "ov_broker_apply_snapshot",
        ):
            st.session_state.pop(k, None)


def page_broker(
    storage: Storage,
    market: str = MARKET_DOMESTIC,
) -> None:
    market = normalize_market(market)
    st.caption(f"시장: **{market_label(market)}**")
    if market == MARKET_OVERSEAS:
        page_broker_overseas(storage)
        return
    st.subheader("증권사 거래내역 변환기")
    st.caption(
        "파일을 올리면 컬럼 구조로 증권사를 자동 인식합니다. "
        "지원: 키움 / 미래에셋 / DB금융투자 / 한국투자(PDF) 등. "
        "인식되지 않을 때만 증권사를 선택하세요."
    )

    businesses = storage.list_businesses()
    if not businesses:
        st.warning("사이드바 ＋ 버튼으로 사업자를 먼저 등록하세요.")
        return

    # 사이드바 선택 사업자가 있으면 기본값으로 사용
    biz_names = [b.name for b in businesses]
    default_idx = 0
    active_id = st.session_state.get("active_business_id")
    if active_id is not None:
        for i, b in enumerate(businesses):
            if b.id == active_id:
                default_idx = i
                break

    biz = st.selectbox("반영할 사업자", biz_names, index=default_idx)

    need_manual = bool(st.session_state.get("broker_need_manual"))
    broker = "자동 감지"
    if need_manual:
        broker = st.selectbox(
            "증권사 (자동 인식 실패 — 선택 필요)",
            ["자동 감지", *list_brokers()],
            key="broker_manual_select",
            help="양식을 자동으로 찾지 못했습니다. 증권사를 고른 뒤 같은 파일을 다시 올려 주세요.",
        )
    else:
        with st.expander("증권사 수동 지정 (선택)", expanded=False):
            broker = st.selectbox(
                "증권사",
                ["자동 감지", *list_brokers()],
                key="broker_optional_select",
                help="자동 인식이 틀리면 여기서 지정할 수 있습니다.",
            )

    up = st.file_uploader(
        "증권사 거래내역 업로드",
        type=["csv", "xlsx", "xls", "pdf"],
        key="broker_up",
        help="파일만 올리면 증권사를 자동 판별합니다.",
    )

    if up:
        file_ext = up.name.split(".")[-1].lower().strip()
        file_token = f"{up.name}:{up.size}:{broker}:{biz}:{file_ext}"
        if st.session_state.get("broker_parse_token") != file_token:
            try:
                if file_ext not in {"csv", "xlsx", "xls", "pdf"}:
                    # MIME/매직넘버 기반 PDF 감지
                    raw = up.getvalue()
                    if raw[:4] != b"%PDF":
                        st.error(f"지원하지 않는 파일 형식입니다: {file_ext}")
                        return
                    file_ext = "pdf"

                result = detect_and_parse(
                    up.getvalue(),
                    up.name if file_ext != "pdf" or up.name.lower().endswith(".pdf") else f"{up.name}.pdf",
                    default_business=biz,
                    broker_hint=broker,
                )
                st.session_state.broker_need_manual = False
                edited = result.dataframe.copy()
                if "거래유형" in edited.columns:
                    kind = edited["거래유형"].astype(str)
                    drop_div = kind.str.contains("배당", na=False)
                    n_div = int(drop_div.sum())
                    if n_div:
                        edited = edited.loc[~drop_div].reset_index(drop=True)
                        result.notes.append(
                            f"배당 {n_div}건은 증권사 변환기에서 제외했습니다."
                        )
                # 0건이거나 컬럼만 있는 경우 → 수동 입력용 빈 행 5개
                if edited.empty or (
                    "수량" in edited.columns
                    and edited["수량"].isna().all()
                    and edited.get("종목명", pd.Series(dtype=str)).astype(str).str.strip().eq("").all()
                ):
                    if edited.empty or len(edited) < 5:
                        edited = empty_trade_rows(biz, n=5)
                if "사업자" in edited.columns:
                    edited["사업자"] = edited["사업자"].fillna(biz).replace("", biz)
                st.session_state.broker_parse_token = file_token
                st.session_state.broker_parse_notes = result.notes
                st.session_state.broker_parse_name = result.broker_name
                st.session_state.broker_edit_df = edited
            except Exception as exc:  # noqa: BLE001
                st.session_state.broker_need_manual = True
                st.session_state.pop("broker_parse_token", None)
                st.error(str(exc))
                st.warning("아래에서 증권사를 선택한 뒤, 동일 파일을 다시 업로드해 주세요.")
                return

    if "broker_edit_df" not in st.session_state:
        st.info("변환할 CSV / Excel / PDF 파일을 업로드하세요. 증권사는 자동으로 인식됩니다.")
        return

    for note in st.session_state.get("broker_parse_notes", []):
        note_s = str(note)
        if OCR_TIP in note_s or "이미지형 PDF" in note_s or "Tesseract" in note_s:
            st.warning(note_s)
        elif "양식으로 인식" in note_s:
            st.success(note_s)
        else:
            st.info(note_s)

    st.markdown("### 파싱 및 변환 결과 (검수 및 수정)")
    st.caption(
        "셀을 직접 수정하거나, 엑셀/PDF에서 복사한 값을 붙여넣기(Ctrl+V)할 수 있습니다. "
        "행 추가/삭제 후 아래 버튼으로 등록하세요."
    )

    # 사업자 컬럼을 현재 선택값으로 동기화 제안
    preview_df = st.session_state.broker_edit_df.copy()
    if "사업자" in preview_df.columns:
        preview_df["사업자"] = preview_df["사업자"].fillna(biz)
        preview_df.loc[preview_df["사업자"].astype(str).str.strip() == "", "사업자"] = biz

    column_config = {
        "거래일자": st.column_config.TextColumn("거래일자", help="YYYY-MM-DD"),
        "사업자": st.column_config.TextColumn("사업자"),
        "종목코드": st.column_config.TextColumn("종목코드"),
        "종목명": st.column_config.TextColumn("종목명"),
        "거래유형": st.column_config.SelectboxColumn(
            "거래유형", options=["매수", "매도"], required=True
        ),
        "수량": st.column_config.NumberColumn("수량", min_value=0, step=1, format="%g"),
        "유가금잔": st.column_config.NumberColumn(
            "유가금잔",
            format="%g",
            help="시간순 정렬 후 종목별 매수(+)/매도(-) 누적 잔여수량",
            disabled=True,
        ),
        "단가": st.column_config.NumberColumn("단가", min_value=0, step=1, format="%g"),
        "수수료": st.column_config.NumberColumn("수수료", min_value=0, step=1, format="%g"),
        "제세금": st.column_config.NumberColumn("제세금", min_value=0, step=1, format="%g"),
        "정산금액": st.column_config.NumberColumn("정산금액", step=1, format="%g"),
        "메모": st.column_config.TextColumn("메모"),
    }

    edited_df = st.data_editor(
        preview_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config=column_config,
        key="broker_data_editor",
    )
    st.session_state.broker_edit_df = edited_df

    st.markdown("")
    apply = st.button(
        "선택된 사업자로 거래 내역 일괄 등록",
        type="primary",
        use_container_width=True,
    )

    if not apply:
        return

    try:
        to_save = edited_df.copy()
        if to_save.empty:
            st.warning("등록할 행이 없습니다.")
            return

        # 빈 행 제거
        if "수량" in to_save.columns:
            to_save = to_save[to_save["수량"].notna()]
            to_save = to_save[pd.to_numeric(to_save["수량"], errors="coerce").fillna(0) > 0]

        if "사업자" not in to_save.columns:
            to_save["사업자"] = biz
        else:
            to_save["사업자"] = to_save["사업자"].fillna(biz).replace("", biz)

        source_name = st.session_state.get("broker_parse_name", "broker")
        trades, errors = dataframe_to_trades(
            to_save,
            storage,
            default_business=biz,
            source=f"broker:{source_name}",
            market=market,
        )
        if trades:
            storage.add_trades_bulk(trades)
        st.success(f"{len(trades)}건 반영 완료 ({source_name} → {biz})")
        if errors:
            with st.expander(f"오류/스킵 {len(errors)}건"):
                for e in errors:
                    st.write(e)

        positions, _, _warnings = compute_positions(
            storage.list_trades(market=market)
        )
        st.write("업데이트된 잔고 요약")
        st.dataframe(
            positions_to_dataframe(positions),
            use_container_width=True,
            hide_index=True,
        )

        # 검수 상태 초기화
        for key in (
            "broker_parse_token",
            "broker_parse_notes",
            "broker_parse_name",
            "broker_edit_df",
            "broker_data_editor",
        ):
            st.session_state.pop(key, None)
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))


def page_legacy_journal(
    storage: Storage,
    business_id: int | None,
    market: str = MARKET_DOMESTIC,
) -> None:
    market = normalize_market(market)
    st.caption(f"시장: **{market_label(market)}**")
    st.subheader("기초 데이터 등록")
    st.caption(
        "기존 시트별 '주식 매매일지' 엑셀을 업로드하면 전 시트 거래를 추출·검수 후 "
        "선택한 사업자로 일괄 등록합니다."
    )

    businesses = storage.list_businesses()
    if not businesses:
        st.warning("사이드바 ＋ 버튼으로 사업자를 먼저 등록하세요.")
        return

    biz_names = [b.name for b in businesses]
    default_idx = 0
    if business_id is not None:
        for i, b in enumerate(businesses):
            if b.id == business_id:
                default_idx = i
                break

    biz = st.selectbox("등록할 사업자", biz_names, index=default_idx, key="legacy_biz")

    if st.session_state.pop("_legacy_toast", False):
        st.toast(
            st.session_state.pop(
                "_legacy_toast_msg",
                "✅ 기초 데이터가 일괄 등록되었습니다!",
            )
        )
    if st.session_state.pop("_legacy_parse_toast", False):
        st.toast(
            st.session_state.pop(
                "_legacy_parse_toast_msg",
                "✅ 거래 내역이 정상 추출되었습니다!",
            )
        )

    up = st.file_uploader(
        "기존 주식 매매일지 엑셀 일괄 업로드",
        type=["xlsx", "xls"],
        key="legacy_up",
        help="시트마다 종목 매매일지가 있는 엑셀 파일을 업로드하세요.",
    )

    if up:
        token = f"{up.name}:{up.size}:{biz}"
        if st.session_state.get("legacy_token") != token:
            try:
                df, notes, stats = parse_legacy_journal_excel(
                    up.getvalue(), default_business=biz
                )
                if "사업자" in df.columns:
                    df["사업자"] = biz
                st.session_state.legacy_token = token
                st.session_state.legacy_notes = notes
                st.session_state.legacy_df = df
                st.session_state.legacy_stats = stats
                st.session_state["_legacy_parse_toast"] = True
                st.session_state["_legacy_parse_toast_msg"] = (
                    f"총 {stats['trade_count']}건의 거래 내역"
                    f"({stats['sheet_count']}개 종목 시트)이 정상 추출되었습니다."
                )
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
                return

    if "legacy_df" not in st.session_state:
        st.info("매매일지 엑셀(.xlsx)을 업로드하세요. Summary 등 요약 시트는 자동 제외됩니다.")
        with st.expander("인식 컬럼 안내"):
            st.markdown(
                """
                - **종목명**: 시트명 또는 종목명 열 (병합 셀은 자동 채움)
                - **매매 구분** / **매수 일자** / **매도 일자**
                - **체결 단가**, **체결 수량** 또는 **매도수량**
                - **매매 비용(수수료+제세금)**
                """
            )
        return

    stats = st.session_state.get("legacy_stats") or {}
    if stats:
        st.success(
            f"총 {stats.get('trade_count', 0)}건의 거래 내역"
            f"({stats.get('sheet_count', 0)}개 종목 시트)이 정상 추출되었습니다."
        )

    for note in st.session_state.get("legacy_notes", [])[1:]:
        st.caption(note)

    st.markdown("### 추출된 매매 내역 (검수 및 수정)")
    preview = st.session_state.legacy_df.copy()
    if "사업자" in preview.columns:
        preview["사업자"] = biz

    edited = st.data_editor(
        preview,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "거래일자": st.column_config.TextColumn("거래일자", help="YYYY-MM-DD"),
            "사업자": st.column_config.TextColumn("사업자"),
            "종목코드": st.column_config.TextColumn("종목코드"),
            "종목명": st.column_config.TextColumn("종목명"),
            "거래유형": st.column_config.SelectboxColumn(
                "거래유형", options=["매수", "매도"], required=True
            ),
            "수량": st.column_config.NumberColumn("수량", min_value=0, step=1, format="%,d"),
            "단가": st.column_config.NumberColumn("단가", min_value=0, step=1, format="%,d 원"),
            "수수료": st.column_config.NumberColumn("수수료", min_value=0, step=1, format="%,d 원"),
            "정산금액": st.column_config.NumberColumn("정산금액", step=1, format="%,d 원"),
            "메모": st.column_config.TextColumn("메모"),
        },
        key="legacy_editor",
    )
    st.session_state.legacy_df = edited

    st.markdown("")
    if st.button("기초 데이터 일괄 등록", type="primary", use_container_width=True):
        try:
            to_save = edited.copy()
            if to_save.empty:
                st.warning("등록할 행이 없습니다.")
                return
            if "수량" in to_save.columns:
                to_save = to_save[to_save["수량"].notna()]
                to_save = to_save[
                    pd.to_numeric(to_save["수량"], errors="coerce").fillna(0) > 0
                ]
            to_save["사업자"] = biz

            trades, errors = dataframe_to_trades(
                to_save,
                storage,
                default_business=biz,
                source="legacy_journal",
                market=market,
            )
            if trades:
                storage.add_trades_bulk(trades)

            st.session_state["_legacy_toast"] = True
            st.session_state["_legacy_toast_msg"] = (
                f"✅ 기초 데이터 {len(trades)}건이 '{biz}'에 등록되었습니다!"
            )
            if errors:
                st.session_state["_legacy_toast_msg"] += f" (스킵 {len(errors)}건)"

            for key in (
                "legacy_token",
                "legacy_notes",
                "legacy_df",
                "legacy_editor",
                "legacy_stats",
            ):
                st.session_state.pop(key, None)
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))


def page_masters(
    storage: Storage,
    business_id: int | None,
    market: str = MARKET_DOMESTIC,
) -> None:
    market = normalize_market(market)
    st.caption(f"시장: **{market_label(market)}**")
    """선택한 사업자 소속 종목·증권사/계좌만 관리."""
    st.subheader("🏢 거래처 및 종목 관리")
    if business_id is None:
        st.warning("사이드바에서 사업자를 선택한 뒤 종목·거래처를 관리해 주세요.")
        return

    tab_stock, tab_account = st.tabs(["📈 종목 관리", "🏦 거래처(증권사/계좌) 관리"])

    with tab_stock:
        stocks = storage.get_all_stocks(business_id, market=market)
        st.markdown("##### 등록된 종목")
        st.caption("현재 선택된 사업자에 연결된 종목만 표시됩니다.")
        if stocks:
            stock_df = pd.DataFrame(
                [
                    {
                        "선택": False,
                        "ID": int(s.id),
                        "종목코드": s.code,
                        "종목명": s.name,
                        "회계거래처코드": s.partner_code or "",
                        "비고": s.note or "",
                        "거래건수": storage.count_trades_for_stock(int(s.id))
                        if s.id is not None
                        else 0,
                    }
                    for s in stocks
                    if s.id is not None
                ]
            )
            edited_stocks = st.data_editor(
                stock_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=[c for c in stock_df.columns if c != "선택"],
                column_config={
                    "선택": st.column_config.CheckboxColumn("선택", default=False),
                    "ID": st.column_config.NumberColumn("ID", format="%d"),
                    "거래건수": st.column_config.NumberColumn("거래건수", format="%d"),
                },
                column_order=[
                    "선택",
                    "ID",
                    "종목코드",
                    "종목명",
                    "회계거래처코드",
                    "비고",
                    "거래건수",
                ],
                key="master_stock_editor",
            )
            selected_stocks = edited_stocks[edited_stocks["선택"] == True]  # noqa: E712

            if st.button(
                "🗑️ 선택한 종목 삭제",
                type="primary",
                key="master_stock_bulk_delete",
            ):
                if selected_stocks.empty:
                    st.info("삭제할 종목을 선택해 주세요.")
                elif int(selected_stocks["거래건수"].fillna(0).gt(0).sum()) > 0:
                    st.warning("거래 내역이 존재하는 종목은 삭제할 수 없습니다.")
                else:
                    try:
                        ids = [int(x) for x in selected_stocks["ID"].tolist()]
                        for stock_id in ids:
                            storage.delete_stock(stock_id, force=False)
                        msg = f"{len(ids)}개 종목이 삭제되었습니다."
                        st.session_state["_pending_toast"] = msg
                        st.toast(msg)
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
        else:
            st.info("등록된 종목이 없습니다.")

        st.divider()
        st.markdown("##### 신규 종목 추가")
        with st.form("master_stock_add_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            new_code = c1.text_input("종목코드", placeholder="005930")
            new_name = c2.text_input("종목명", placeholder="삼성전자")
            new_partner = c3.text_input("회계 거래처코드", placeholder="선택")
            if st.form_submit_button("종목 등록", type="primary", use_container_width=True):
                try:
                    storage.add_stock(
                        new_code,
                        new_name,
                        partner_code=new_partner,
                        business_id=int(business_id),
                        market=market,
                    )
                    st.session_state["_pending_toast"] = (
                        f"종목 '{new_name.strip()}'이(가) 등록되었습니다."
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    with tab_account:
        accounts = storage.get_all_accounts(business_id, market=market)
        st.markdown("##### 등록된 거래처(증권사/계좌)")
        st.caption("사이드바 사업자와 별개로, 해당 사업자 소속 증권사·계좌만 관리합니다.")
        if accounts:
            acc_df = pd.DataFrame(
                [
                    {
                        "선택": False,
                        "ID": int(a.id),
                        "거래처코드": a.code or "",
                        "거래처명": a.name,
                        "계좌번호": a.account_no or "",
                        "비고": a.note or "",
                    }
                    for a in accounts
                    if a.id is not None
                ]
            )
            edited_accounts = st.data_editor(
                acc_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=[c for c in acc_df.columns if c != "선택"],
                column_config={
                    "선택": st.column_config.CheckboxColumn("선택", default=False),
                    "ID": st.column_config.NumberColumn("ID", format="%d"),
                },
                column_order=[
                    "선택",
                    "ID",
                    "거래처코드",
                    "거래처명",
                    "계좌번호",
                    "비고",
                ],
                key="master_account_editor",
            )
            selected_accounts = edited_accounts[
                edited_accounts["선택"] == True  # noqa: E712
            ]

            if st.button(
                "🗑️ 선택한 거래처 삭제",
                type="primary",
                key="master_account_bulk_delete",
            ):
                if selected_accounts.empty:
                    st.info("삭제할 거래처를 선택해 주세요.")
                else:
                    try:
                        ids = [int(x) for x in selected_accounts["ID"].tolist()]
                        for account_id in ids:
                            storage.delete_account(account_id)
                        msg = f"{len(ids)}개 거래처가 삭제되었습니다."
                        st.session_state["_pending_toast"] = msg
                        st.toast(msg)
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
        else:
            st.info("등록된 거래처가 없습니다.")

        st.divider()
        st.markdown("##### 신규 거래처 추가")
        with st.form("master_account_add_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            new_acc_code = c1.text_input("거래처코드", placeholder="선택")
            new_acc_name = c2.text_input("거래처명", placeholder="예: KB증권")
            c3, c4 = st.columns(2)
            new_acc_no = c3.text_input("계좌번호", placeholder="선택")
            new_acc_note = c4.text_input("비고", placeholder="선택")
            if st.form_submit_button(
                "거래처 등록", type="primary", use_container_width=True
            ):
                try:
                    storage.add_account(
                        int(business_id),
                        new_acc_name,
                        code=new_acc_code,
                        account_no=new_acc_no,
                        note=new_acc_note,
                        market=market,
                    )
                    st.session_state["_pending_toast"] = (
                        f"거래처 '{new_acc_name.strip()}'이(가) 등록되었습니다."
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))


def page_settings(
    storage: Storage,
    business_id: int | None,
    *,
    mode: str = "stock",
    market: str = MARKET_DOMESTIC,
) -> None:
    market = normalize_market(market)
    """사업자별 환경설정.

    mode='stock'  → 주식 계정과목 + 종목 거래처코드
    mode='income' → 이자·배당 계정과목 + 증권사 거래처코드
    """
    if business_id is None:
        st.warning("사이드바에서 사업자를 선택한 뒤 환경설정을 저장해 주세요.")
        return

    company_name = next(
        (b.name for b in storage.list_businesses() if b.id == business_id),
        "",
    )
    st.caption(f"선택된 사업자: **{company_name or business_id}**")

    cfg = storage.get_account_config(business_id, market=market)

    if mode == "income":
        tab_income, tab_broker = st.tabs(
            [
                "💰 이자·배당 계정과목 설정",
                "🏦 증권사 거래처코드 매핑",
            ]
        )
        with tab_income:
            st.subheader("💰 이자·배당소득용 계정과목 코드")
            with st.form("settings_income_account_form"):
                c1, c2, c3 = st.columns(3)
                interest_code = c1.text_input(
                    "이자수익 계정코드",
                    value=cfg.interest_code,
                    placeholder="0901",
                )
                prepaid_tax_code = c2.text_input(
                    "선납세금 계정코드",
                    value=cfg.prepaid_tax_code,
                    placeholder="0136",
                )
                bank_code = c3.text_input(
                    "보통예금 계정코드",
                    value=cfg.bank_code,
                    placeholder="0103",
                )
                if st.form_submit_button(
                    "💾 이자·배당 계정과목 저장",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        storage.save_income_account_config(
                            business_id,
                            IncomeAccountConfig(
                                interest_code=interest_code,
                                prepaid_tax_code=prepaid_tax_code,
                                bank_code=bank_code,
                            ),
                        )
                        msg = "이자·배당 계정과목이 저장되었습니다."
                        st.toast(msg)
                        st.session_state["_pending_toast"] = msg
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))

        with tab_broker:
            st.subheader("🏦 증권사별 거래처코드 (이자·배당 전표)")
            st.caption("이자·배당 전표의 거래처코드 칸에 사용됩니다.")
            partners = storage.list_broker_partners(business_id)
            if not partners:
                storage.upsert_broker_partner(
                    "KB증권", "99200", business_id=business_id
                )
                partners = storage.list_broker_partners(business_id)

            partner_df = pd.DataFrame(
                [
                    {
                        "선택": False,
                        "ID": int(p.id),
                        "증권사": p.broker_name,
                        "거래처코드": p.partner_code or "",
                    }
                    for p in partners
                    if p.id is not None
                ]
            )
            edited_p = st.data_editor(
                partner_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key="settings_broker_partner_editor",
                disabled=["ID"],
                column_config={
                    "선택": st.column_config.CheckboxColumn("선택", default=False),
                },
            )
            p1, p2 = st.columns(2)
            if p1.button(
                "💾 증권사 거래처코드 저장",
                use_container_width=True,
                key="settings_bp_save",
            ):
                updates = {
                    int(row["ID"]): (
                        str(row["증권사"] or ""),
                        str(row["거래처코드"] or ""),
                    )
                    for _, row in edited_p.iterrows()
                }
                n = storage.update_broker_partners(
                    updates, business_id=business_id
                )
                st.toast(f"{n}건 저장했습니다.")
                st.rerun()
            if p2.button(
                "🗑️ 선택 삭제",
                use_container_width=True,
                key="settings_bp_del",
            ):
                ids = [
                    int(x)
                    for x in edited_p.loc[
                        edited_p["선택"] == True, "ID"  # noqa: E712
                    ].tolist()
                ]
                for pid in ids:
                    storage.delete_broker_partner(pid, business_id=business_id)
                st.toast(f"{len(ids)}건 삭제했습니다.")
                st.rerun()

            with st.form("settings_broker_partner_add", clear_on_submit=True):
                a1, a2 = st.columns(2)
                new_name = a1.text_input("증권사명", placeholder="KB증권")
                new_code = a2.text_input("거래처코드", placeholder="99200")
                if st.form_submit_button("증권사 추가", use_container_width=True):
                    try:
                        storage.upsert_broker_partner(
                            new_name, new_code, business_id=business_id
                        )
                        st.toast("증권사 매핑이 추가되었습니다.")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
        return

    # mode == "stock" (기본)
    tab_stock, tab_partner = st.tabs(
        [
            "📈 주식매매 계정과목 설정",
            "🏦 종목 거래처코드 매핑",
        ]
    )

    with tab_stock:
        st.subheader("📈 주식 매매용 계정과목 코드")
        with st.form("settings_stock_account_form"):
            c1, c2 = st.columns(2)
            security_code = c1.text_input(
                "투자유가증권 계정코드",
                value=cfg.security_code,
                placeholder="0178",
            )
            fee_code = c2.text_input(
                "지급수수료 계정코드",
                value=cfg.fee_code,
                placeholder="0965",
            )
            deposit_code = c1.text_input(
                "기타제예금 계정코드",
                value=cfg.deposit_code,
                placeholder="0104",
            )
            gain_code = c2.text_input(
                "투자자산처분이익 계정코드",
                value=cfg.gain_code,
                placeholder="0915",
            )
            loss_code = c1.text_input(
                "투자자산처분손실 계정코드",
                value=cfg.loss_code,
                placeholder="0953",
            )
            if st.form_submit_button(
                "💾 주식 계정과목 저장",
                type="primary",
                use_container_width=True,
            ):
                try:
                    base = storage.get_account_config(business_id, market=market)
                    storage.save_account_config(
                        business_id,
                        {
                            "security_code": security_code,
                            "fee_code": fee_code,
                            "deposit_code": deposit_code,
                            "gain_code": gain_code,
                            "loss_code": loss_code,
                            "interest_code": base.interest_code,
                            "prepaid_tax_code": base.prepaid_tax_code,
                            "bank_code": base.bank_code,
                        },
                        market=market,
                    )
                    msg = "주식 매매 계정과목이 저장되었습니다."
                    st.toast(msg)
                    st.session_state["_pending_toast"] = msg
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    with tab_partner:
        st.subheader("🏦 종목별 회계 거래처코드")
        st.caption("매매 전표의 거래처코드 칸에 사용됩니다. 비우면 종목코드를 사용합니다.")
        stocks = storage.get_all_stocks(business_id, market=market)
        if stocks:
            stock_df = pd.DataFrame(
                [
                    {
                        "ID": int(s.id),
                        "종목코드": s.code,
                        "종목명": s.name,
                        "회계거래처코드": s.partner_code or "",
                    }
                    for s in stocks
                    if s.id is not None
                ]
            )
            edited_stocks = st.data_editor(
                stock_df,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=["ID", "종목코드", "종목명"],
                column_config={
                    "ID": st.column_config.NumberColumn("ID", format="%d"),
                    "회계거래처코드": st.column_config.TextColumn(
                        "회계거래처코드",
                        help="예: 05418",
                        max_chars=20,
                    ),
                },
                column_order=["ID", "종목코드", "종목명", "회계거래처코드"],
                key="settings_stock_partner_editor",
            )
            if st.button(
                "💾 종목 거래처코드 저장",
                type="primary",
                use_container_width=True,
                key="settings_stock_partner_save",
            ):
                try:
                    original = {
                        int(r["ID"]): str(r["회계거래처코드"] or "").strip()
                        for _, r in stock_df.iterrows()
                    }
                    updates = {}
                    for _, row in edited_stocks.iterrows():
                        sid = int(row["ID"])
                        new_code = str(row["회계거래처코드"] or "").strip()
                        if original.get(sid, "") != new_code:
                            updates[sid] = new_code
                    n = storage.update_stock_partner_codes(
                        updates, business_id=business_id
                    )
                    msg = (
                        f"{n}개 종목의 거래처코드가 저장되었습니다."
                        if n
                        else "변경된 종목 거래처코드가 없습니다."
                    )
                    st.toast(msg)
                    st.session_state["_pending_toast"] = msg
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
        else:
            st.info(
                "등록된 종목이 없습니다. "
                "'거래처 및 종목 관리'에서 먼저 등록해 주세요."
            )


@st.dialog("🗑️ 이자·배당 내역 전체 삭제")
def confirm_clear_income_records_dialog(
    storage: Storage,
    business_id: int,
    business_name: str,
) -> None:
    """현재 선택 사업자의 이자·배당 내역 전체 삭제 확인."""
    db = get_storage()
    records = db.list_income_records(business_id=int(business_id))
    st.warning(
        f"**[{business_name}]** 사업자에 등록된 이자·배당 내역 "
        f"**{len(records):,}건**을 모두 삭제합니다.\n\n"
        "등록된 모든 거래 내역을 삭제하시겠습니까? 되돌릴 수 없습니다."
    )
    confirm = st.checkbox(
        "위 안내를 확인했으며 전체 삭제를 진행합니다.",
        key=f"confirm_clear_income_{business_id}",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("취소", use_container_width=True, key="clear_income_cancel"):
            st.rerun()
    with c2:
        if st.button(
            "🗑️ 전체 삭제 실행",
            type="primary",
            use_container_width=True,
            disabled=not confirm,
            key="clear_income_confirm",
        ):
            try:
                n = db.clear_income_records_for_business(int(business_id))
                st.session_state["_pending_toast"] = (
                    f"[{business_name}] 이자·배당 내역 {n:,}건이 삭제되었습니다."
                )
                for k in (
                    "income_preview_df",
                    "income_preview_source",
                    "income_records_editor",
                    "income_preview_editor",
                    "income_upload_token",
                ):
                    st.session_state.pop(k, None)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))


def page_income(storage: Storage, business_id: int | None) -> None:
    """이자·배당소득 업로드 · 조회 · 전표 다운로드 (설정은 환경설정 메뉴)."""
    st.caption(
        "원천징수영수증 업로드 및 전표 생성/엑셀 다운로드. "
        "계정과목·증권사 거래처코드는 ⚙️ 환경설정에서 관리합니다."
    )

    if business_id is None:
        st.warning("사이드바에서 사업자를 먼저 선택해 주세요.")
        return

    company_name = ""
    for b in storage.list_businesses():
        if b.id == business_id:
            company_name = b.name
            break

    st.markdown("##### 원천징수영수증 업로드")
    st.caption(
        "Excel 권장 컬럼: 지급일, 지급액, 법인세, 지방소득세, 금융상품명, 증권사, 소득구분. "
        "KB증권 증권계좌 과세내역조회(원천징수영수증) 엑셀(원거래일자·과세표준)도 지원합니다. "
        "PDF는 KB 계좌별과세내역, 미래에셋 원천징수영수증(원화), "
        "미래에셋 거래실적증명서(배당·예탁금이용료)를 지원합니다. "
        "파일만 올리면 양식을 자동 인식합니다. 미리보기에서 수정 후 저장하세요."
    )
    uploaded = st.file_uploader(
        "PDF / Excel / CSV",
        type=["pdf", "xlsx", "xls", "csv"],
        key="income_uploader",
    )
    if uploaded is not None:
        token = f"{uploaded.name}:{uploaded.size}"
        if st.session_state.get("income_upload_token") != token:
            result = parse_income_file(uploaded.getvalue(), uploaded.name)
            for note in result.notes:
                st.info(note)
            if result.rows:
                st.session_state["income_preview_df"] = rows_to_dataframe(result.rows)
                st.session_state["income_preview_source"] = result.source
                st.session_state["income_upload_token"] = token
                st.session_state.pop("income_preview_editor", None)
            else:
                for note in result.notes:
                    st.warning(note)
        else:
            # 동일 파일 유지 중 — 이전 파싱 노트만 필요 시 생략
            pass

    preview = st.session_state.get("income_preview_df")
    if preview is not None and not getattr(preview, "empty", True):
        st.markdown("##### 파싱 미리보기 (저장 전 수정)")
        st.caption(
            "환율이 0인 외화배당은 **환율** 칸에 직접 입력하세요. "
            "입력 즉시 지급액·법인세·메모가 재계산됩니다. "
            "(지급액 = 외화지급액 × 환율, 법인세 = 외화원천세 × 환율)"
        )
        preview = ensure_income_preview_columns(preview)
        edited_preview = st.data_editor(
            preview,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="income_preview_editor",
            column_config={
                "지급일": st.column_config.TextColumn("지급일", help="YYYY-MM-DD"),
                "종목코드": st.column_config.TextColumn("종목코드", width="small"),
                "통화": st.column_config.TextColumn("통화", width="small"),
                "외화지급액": st.column_config.NumberColumn(
                    "외화지급액", format="%.2f", help="외화 기준 지급액"
                ),
                "외화원천세": st.column_config.NumberColumn(
                    "외화원천세", format="%.2f", help="외화 기준 원천세"
                ),
                "환율": st.column_config.NumberColumn(
                    "환율",
                    format="%.2f",
                    min_value=0.0,
                    step=0.1,
                    help="미기재(0)인 경우 직접 입력 → 원화 자동 환산",
                ),
                "지급액": st.column_config.NumberColumn(
                    "지급액(원)", format="%.0f", help="외화×환율 자동계산"
                ),
                "법인세": st.column_config.NumberColumn(
                    "법인세(원)", format="%.0f", help="외화원천세×환율 자동계산"
                ),
                "지방소득세": st.column_config.NumberColumn(
                    "지방소득세", format="%.0f"
                ),
                "소득구분": st.column_config.SelectboxColumn(
                    "소득구분", options=["이자", "배당"]
                ),
            },
            disabled=["종목코드", "통화"],
        )
        # 환율 변경 → 지급액·법인세·메모 즉시 재계산
        recalced = apply_income_fx_rates(edited_preview)
        fx_changed = False
        try:
            prev_fx = pd.to_numeric(preview["환율"], errors="coerce").fillna(0.0).reset_index(drop=True)
            new_fx = pd.to_numeric(recalced["환율"], errors="coerce").fillna(0.0).reset_index(drop=True)
            prev_pay = pd.to_numeric(preview["지급액"], errors="coerce").fillna(0.0).reset_index(drop=True)
            new_pay = pd.to_numeric(recalced["지급액"], errors="coerce").fillna(0.0).reset_index(drop=True)
            prev_tax = pd.to_numeric(preview["법인세"], errors="coerce").fillna(0.0).reset_index(drop=True)
            new_tax = pd.to_numeric(recalced["법인세"], errors="coerce").fillna(0.0).reset_index(drop=True)
            has_fx_rows = bool(
                (pd.to_numeric(recalced["외화지급액"], errors="coerce").fillna(0) > 0).any()
            )
            if has_fx_rows and len(prev_fx) == len(new_fx):
                if (
                    not prev_fx.equals(new_fx)
                    or not prev_pay.equals(new_pay)
                    or not prev_tax.equals(new_tax)
                ):
                    fx_changed = True
        except Exception:  # noqa: BLE001
            fx_changed = False

        st.session_state["income_preview_df"] = recalced
        if fx_changed:
            st.session_state.pop("income_preview_editor", None)
            st.rerun()

        edited_preview = recalced
        c_save, c_clear = st.columns(2)
        if c_save.button(
            "💾 미리보기 내역 DB 저장",
            type="primary",
            use_container_width=True,
            key="income_preview_save",
        ):
            try:
                src = st.session_state.get("income_preview_source", "excel")
                to_save = apply_income_fx_rates(edited_preview)
                n = 0
                skipped_fx = 0
                for _, row in to_save.iterrows():
                    pay = str(row.get("지급일") or "").strip()[:10]
                    gross = float(row.get("지급액") or 0)
                    amt_fx = float(row.get("외화지급액") or 0)
                    fx = float(row.get("환율") or 0)
                    if amt_fx > 0 and fx <= 0:
                        skipped_fx += 1
                        continue
                    if not pay or gross <= 0:
                        continue
                    itype = (
                        "DIVIDEND"
                        if str(row.get("소득구분") or "").strip() == "배당"
                        else "INTEREST"
                    )
                    storage.add_income_record(
                        pay_date=pay,
                        business_id=int(business_id),
                        product_name=str(row.get("금융상품명") or ""),
                        broker_name=str(row.get("증권사") or ""),
                        income_type=itype,
                        gross_amount=gross,
                        corp_tax=float(row.get("법인세") or 0),
                        local_tax=float(row.get("지방소득세") or 0),
                        memo=str(row.get("메모") or ""),
                        source=src,
                    )
                    n += 1
                st.session_state.pop("income_preview_df", None)
                st.session_state.pop("income_preview_editor", None)
                st.session_state.pop("income_upload_token", None)
                msg = f"{n}건의 이자·배당 내역을 저장했습니다."
                if skipped_fx:
                    msg += f" (환율 미입력 외화 {skipped_fx}건 제외)"
                st.session_state["_pending_toast"] = msg
                st.toast(msg)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        if c_clear.button("미리보기 지우기", use_container_width=True):
            st.session_state.pop("income_preview_df", None)
            st.session_state.pop("income_preview_editor", None)
            st.session_state.pop("income_upload_token", None)
            st.rerun()

    st.divider()
    head_l, head_r = st.columns([3, 1])
    with head_l:
        st.markdown("##### 등록된 내역")
    records = storage.list_income_records(business_id=business_id)
    with head_r:
        if st.button(
            "🗑️ 전체 삭제",
            use_container_width=True,
            type="secondary",
            disabled=not records,
            key="income_clear_all",
            help="현재 사업자의 이자·배당 내역을 모두 삭제합니다",
        ):
            confirm_clear_income_records_dialog(
                storage, int(business_id), company_name or str(business_id)
            )
    if records:
        rec_df = pd.DataFrame(
            [
                {
                    "선택": False,
                    "ID": int(r.id),
                    "지급일": r.pay_date,
                    "금융상품명": r.product_name,
                    "증권사": r.broker_name,
                    "소득구분": "배당" if r.income_type == "DIVIDEND" else "이자",
                    "지급액": r.gross_amount,
                    "법인세": r.corp_tax,
                    "지방소득세": r.local_tax,
                    "정산입금": r.net_amount,
                    "메모": r.memo,
                }
                for r in records
                if r.id is not None
            ]
        )
        edited_recs = st.data_editor(
            rec_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key="income_records_editor",
            disabled=["ID", "정산입금"],
            column_config={
                "선택": st.column_config.CheckboxColumn("선택", default=False),
                "지급액": st.column_config.NumberColumn("지급액", format="%.0f"),
                "법인세": st.column_config.NumberColumn("법인세", format="%.0f"),
                "지방소득세": st.column_config.NumberColumn(
                    "지방소득세", format="%.0f"
                ),
                "정산입금": st.column_config.NumberColumn("정산입금", format="%.0f"),
                "소득구분": st.column_config.SelectboxColumn(
                    "소득구분", options=["이자", "배당"]
                ),
            },
        )
        b1, b2 = st.columns(2)
        if b1.button("💾 수정 내용 저장", use_container_width=True, key="income_upd"):
            try:
                for _, row in edited_recs.iterrows():
                    storage.update_income_record(
                        int(row["ID"]),
                        pay_date=str(row["지급일"]),
                        product_name=str(row.get("금융상품명") or ""),
                        broker_name=str(row.get("증권사") or ""),
                        income_type=(
                            "DIVIDEND"
                            if str(row.get("소득구분") or "") == "배당"
                            else "INTEREST"
                        ),
                        gross_amount=float(row.get("지급액") or 0),
                        corp_tax=float(row.get("법인세") or 0),
                        local_tax=float(row.get("지방소득세") or 0),
                        memo=str(row.get("메모") or ""),
                    )
                st.toast("이자·배당 내역이 저장되었습니다.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        if b2.button("🗑️ 선택 삭제", use_container_width=True, key="income_del"):
            ids = [
                int(x)
                for x in edited_recs.loc[edited_recs["선택"] == True, "ID"].tolist()  # noqa: E712
            ]
            if not ids:
                st.info("삭제할 행을 선택해 주세요.")
            else:
                storage.delete_income_records(ids)
                st.toast(f"{len(ids)}건 삭제했습니다.")
                st.rerun()
    else:
        st.info("등록된 이자·배당 내역이 없습니다. 위에서 파일을 업로드해 주세요.")

    with st.expander("➕ 수동으로 1건 추가"):
        with st.form("income_manual_add", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            m_date = c1.date_input("지급일", value=date.today())
            m_product = c2.text_input("금융상품명", placeholder="RP / CMA 등")
            m_broker = c3.text_input("증권사", placeholder="KB증권")
            c4, c5, c6, c7 = st.columns(4)
            m_type = c4.selectbox("소득구분", ["이자", "배당"])
            m_gross = c5.number_input("지급액", min_value=0.0, step=1000.0)
            m_corp = c6.number_input("법인세", min_value=0.0, step=100.0)
            m_local = c7.number_input("지방소득세", min_value=0.0, step=10.0)
            if st.form_submit_button("추가", type="primary", use_container_width=True):
                try:
                    storage.add_income_record(
                        pay_date=m_date.isoformat(),
                        business_id=int(business_id),
                        product_name=m_product,
                        broker_name=m_broker,
                        income_type="DIVIDEND" if m_type == "배당" else "INTEREST",
                        gross_amount=m_gross,
                        corp_tax=m_corp,
                        local_tax=m_local,
                        source="manual",
                    )
                    st.toast("1건 추가했습니다.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    st.divider()
    st.markdown("##### 회계 전표 다운로드")
    today = date.today()
    if "income_voucher_range" in st.session_state and "income_start_date" not in st.session_state:
        old = st.session_state.pop("income_voucher_range", None)
        if isinstance(old, (list, tuple)) and len(old) == 2:
            st.session_state["income_start_date"] = old[0]
            st.session_state["income_end_date"] = old[1]
        else:
            st.session_state.pop("income_voucher_range", None)
    if "income_start_date" not in st.session_state:
        st.session_state["income_start_date"] = date(today.year, 1, 1)
    if "income_end_date" not in st.session_state:
        st.session_state["income_end_date"] = today

    q1, q2, q3 = st.columns(3)
    if q1.button("이번 달", key="inc_preset_m", use_container_width=True):
        st.session_state["income_start_date"] = date(today.year, today.month, 1)
        st.session_state["income_end_date"] = today
        st.rerun()
    if q2.button("올해 (1월~현재)", key="inc_preset_y", use_container_width=True):
        st.session_state["income_start_date"] = date(today.year, 1, 1)
        st.session_state["income_end_date"] = today
        st.rerun()
    if q3.button("전체 기간", key="inc_preset_a", use_container_width=True):
        all_recs = storage.list_income_records(business_id=business_id)
        if all_recs:
            dates = sorted(str(r.pay_date)[:10] for r in all_recs)
            st.session_state["income_start_date"] = date.fromisoformat(dates[0])
            st.session_state["income_end_date"] = date.fromisoformat(dates[-1])
        else:
            st.session_state["income_start_date"] = date(2020, 1, 1)
            st.session_state["income_end_date"] = today
        st.rerun()

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_date = st.date_input(
            "📅 시작일 선택",
            format="YYYY-MM-DD",
            key="income_start_date",
        )
    with col_d2:
        end_date = st.date_input(
            "📅 종료일 선택",
            format="YYYY-MM-DD",
            key="income_end_date",
        )
    if start_date > end_date:
        start_date, end_date = end_date, start_date
        st.caption("※ 시작일이 종료일보다 늦어 순서를 바꿔 조회합니다.")

    period_recs = storage.get_income_records_by_period(
        business_id, start_date, end_date
    )
    inc_cfg = storage.get_income_account_config(business_id)
    broker_map = storage.broker_partner_map(business_id)
    line_n = len(
        income_to_voucher_lines(
            period_recs,
            account_config=inc_cfg,
            broker_partner_map=broker_map,
        )
    )
    st.info(
        f"선택된 기간: **{start_date} ~ {end_date}** "
        f"(총 {len(period_recs)}건, {line_n}줄 분개)"
    )
    if not period_recs:
        st.info("💡 선택한 기간에 해당하는 이자·배당 내역이 없습니다.")
        st.download_button(
            "📥 이자·배당 회계 전표 엑셀 다운로드",
            data=b"",
            file_name="empty.xlsx",
            disabled=True,
            use_container_width=True,
        )
    else:
        xbytes = export_income_voucher_excel_bytes(
            period_recs,
            company_name=company_name,
            account_config=inc_cfg,
            broker_partner_map=broker_map,
        )
        fname = (
            f"엑셀자료일반전표전송_이자배당_"
            f"{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
        )
        st.download_button(
            "📥 이자·배당 회계 전표 엑셀 다운로드",
            data=xbytes,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )


def main() -> None:
    from src.supabase_client import is_transient_network_error, reset_supabase_client

    init_session_state()
    inject_sidebar_styles()

    try:
        storage = get_storage()
    except Exception as exc:  # noqa: BLE001
        st.error("데이터베이스에 연결할 수 없습니다.")
        st.caption(str(exc))
        if st.button("🔄 연결 다시 시도", type="primary"):
            reset_supabase_client()
            try:
                _storage_singleton.clear()
            except Exception:  # noqa: BLE001
                pass
            st.rerun()
        return

    # 12시간마다 자동 로컬 백업 (세션당 1회만 시도)
    if not st.session_state.get("_auto_backup_checked"):
        st.session_state._auto_backup_checked = True
        try:
            from src.backup import maybe_auto_backup

            auto = maybe_auto_backup()
            if auto is not None:
                st.session_state._pending_toast = (
                    f"자동 백업 완료 · {auto.total_rows:,}행"
                )
        except Exception:  # noqa: BLE001
            pass

    pending_toast = st.session_state.pop("_pending_toast", None)
    if pending_toast:
        st.toast(pending_toast)

    try:
        # 사이드바: 사업자(+데이터 관리) → 트리형 메뉴
        business_id = sidebar_business_selector(storage)
        st.sidebar.divider()
        menu = sidebar_tree_menu()
        st.sidebar.divider()
        st.sidebar.caption(f"DB: {storage.db_path}")

        # F5 복원용 URL 동기화 (사업자·메뉴·시장)
        sync_url_params()

        title, caption = MENU_PAGE_META.get(
            menu, MENU_PAGE_META["domestic_dashboard"]
        )
        st.title(title)
        if caption:
            st.caption(caption)
        route_active_menu(storage, business_id, menu)
    except Exception as exc:  # noqa: BLE001
        if is_transient_network_error(exc) or "데이터베이스 연결에 실패" in str(exc):
            st.error(
                "데이터베이스 연결이 일시적으로 끊겼습니다. "
                "네트워크 상태를 확인한 뒤 다시 시도해 주세요."
            )
            with st.expander("오류 상세"):
                st.code(str(exc))
            if st.button("🔄 다시 연결", type="primary", key="db_reconnect"):
                reset_supabase_client()
                try:
                    _storage_singleton.clear()
                except Exception:  # noqa: BLE001
                    pass
                st.rerun()
            return
        raise


if __name__ == "__main__":
    main()
