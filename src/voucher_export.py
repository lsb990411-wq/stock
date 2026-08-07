"""회계 프로그램 '엑셀자료일반전표전송' 원본 양식에 매매분개 채우기."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable, Mapping

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment

from .models import AccountConfig, SellResult, Trade

# 기본 계정과목 (코드는 AccountConfig로 덮어씀, 이름은 고정)
_ACCT_NAMES = {
    "security": "투자유가증권",
    "fee": "지급수수료",
    "deposit": "기타제예금",
    "gain": "투자자산처분이익",
    "loss": "투자자산처분손실",
}

DR, CR = 3, 4  # 차변, 대변

# 원본 xls: 헤더=10행, 데이터=11행부터, 컬럼 28개
HEADER_ROW = 10
DATA_START_ROW = 11
NUM_COLUMNS = 28

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_XLS = TEMPLATE_DIR / "엑셀자료일반전표전송.xls"
TEMPLATE_XLSX = TEMPLATE_DIR / "엑셀자료일반전표전송.xlsx"


@dataclass(frozen=True)
class VoucherAccounts:
    security: tuple[str, str]
    fee: tuple[str, str]
    deposit: tuple[str, str]
    gain: tuple[str, str]
    loss: tuple[str, str]

    @classmethod
    def from_config(cls, config: AccountConfig | None = None) -> VoucherAccounts:
        cfg = (config or AccountConfig()).normalize()
        return cls(
            security=(cfg.security_code, _ACCT_NAMES["security"]),
            fee=(cfg.fee_code, _ACCT_NAMES["fee"]),
            deposit=(cfg.deposit_code, _ACCT_NAMES["deposit"]),
            gain=(cfg.gain_code, _ACCT_NAMES["gain"]),
            loss=(cfg.loss_code, _ACCT_NAMES["loss"]),
        )


@dataclass
class VoucherLine:
    ymd: str
    side: int  # 3=차변, 4=대변
    acct_code: str
    acct_name: str
    partner_code: str
    partner_name: str
    summary_code: str
    summary: str
    amount: int

    def as_row(self) -> list:
        """원본 양식 28컬럼 순서에 맞춤 (값 없는 칸은 공란)."""
        row = [""] * NUM_COLUMNS
        row[0] = self.ymd
        row[1] = self.side
        row[2] = self.acct_code
        row[3] = self.acct_name
        row[4] = self.partner_code
        row[5] = (self.partner_name or "")[:30]
        row[6] = self.summary_code
        row[7] = self.summary
        row[8] = self.amount
        return row


def _ymd(trade_date: str) -> str:
    return "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]


def _won(value: float | int | None) -> int:
    if value is None:
        return 0
    return int(round(float(value)))


def _fmt_num(value: float | int | None) -> str:
    """적요용 천단위 구분 숫자(원화·정수)."""
    return f"{_won(value):,}"


def _fmt_qty_overseas(qty: float | int | None) -> str:
    """해외 수량: 정수면 정수, 아니면 소수(끝 0 제거)."""
    q = float(qty or 0)
    if abs(q - round(q)) < 1e-9:
        return f"{int(round(q))}"
    return f"{q:.4f}".rstrip("0").rstrip(".")


def _fmt_fx_price(value: float | int | None) -> str:
    """외화 단가: 최대 소수 4자리, 끝 0 제거."""
    s = f"{float(value or 0):.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _fmt_fx_amount(value: float | int | None) -> str:
    """외화 지급액: 소수 2자리."""
    return f"{float(value or 0):.2f}"


def _fmt_fx_rate(value: float | int | None) -> str:
    """적용 환율: 소수 2자리 (예시: 1470.50)."""
    return f"{float(value or 0):.2f}"


def _is_overseas_trade(trade: Trade) -> bool:
    if getattr(trade, "is_overseas", False):
        return True
    return float(getattr(trade, "fx_rate", 0) or 0) > 0


def build_overseas_remark(trade: Trade) -> str:
    """해외주식 전표 적요.

    - 매수: `{종목} 매수 @{수량}주 * ${외화단가} * {환율}원`
    - 매도: `{종목} 매도 @{수량}주 * ${외화단가} * {환율}원`
    - 배당: `{종목} 배당입금 ${외화지급액} * {환율}원`
    """
    name = (trade.stock_name or trade.stock_code or "").strip()
    qty = float(trade.quantity or 0)
    fx = float(getattr(trade, "fx_rate", 0) or 0)
    price_fx = float(getattr(trade, "price_fx", 0) or 0)
    side = str(trade.side or "").upper()

    if side == "BUY":
        return (
            f"{name} 매수 @{_fmt_qty_overseas(qty)}주"
            f" * ${_fmt_fx_price(price_fx)} * {_fmt_fx_rate(fx)}원"
        )
    if side == "SELL":
        return (
            f"{name} 매도 @{_fmt_qty_overseas(qty)}주"
            f" * ${_fmt_fx_price(price_fx)} * {_fmt_fx_rate(fx)}원"
        )
    if side == "DIVIDEND":
        # price_fx가 총액(qty=1)이거나 단가×수량인 경우를 모두 수용
        amount_usd = price_fx * (qty if qty > 0 else 1.0)
        return (
            f"{name} 배당입금 ${_fmt_fx_amount(amount_usd)}"
            f" * {_fmt_fx_rate(fx)}원"
        )
    return f"{name} {trade.side}"


def _partner_for_trade(
    trade: Trade,
    partner_by_stock_id: Mapping[int, str] | None,
) -> str:
    """종목별 회계 거래처코드. 없으면 종목코드(ticker) 사용."""
    mapped = ""
    if partner_by_stock_id and trade.stock_id in partner_by_stock_id:
        mapped = (partner_by_stock_id.get(trade.stock_id) or "").strip()
    if mapped:
        return mapped
    return (trade.stock_code or "").strip()


def _line(
    *,
    ymd: str,
    side: int,
    acct: tuple[str, str],
    partner_code: str,
    partner_name: str,
    amount: int,
    summary: str,
    summary_code: str = "",
) -> VoucherLine | None:
    if amount == 0:
        return None
    return VoucherLine(
        ymd=ymd,
        side=side,
        acct_code=acct[0],
        acct_name=acct[1],
        partner_code=partner_code or "",
        partner_name=partner_name or "",
        summary_code=summary_code,
        summary=summary,
        amount=abs(amount),
    )


def _trade_summary_buy(trade: Trade) -> str:
    if _is_overseas_trade(trade):
        return build_overseas_remark(trade)
    return f"주식매수 @{_fmt_num(trade.quantity)} * {_fmt_num(trade.price)}"


def _trade_summary_sell(trade: Trade) -> str:
    if _is_overseas_trade(trade):
        return build_overseas_remark(trade)
    return f"주식매도 @{_fmt_num(trade.quantity)} * {_fmt_num(trade.price)}"


def _trade_summary_dividend(trade: Trade) -> str:
    if _is_overseas_trade(trade):
        return build_overseas_remark(trade)
    name = (trade.stock_name or trade.stock_code or "").strip()
    return f"{name} 배당입금 {_fmt_num(trade.settlement_amount or trade.price)}"


def _buy_lines(
    trade: Trade,
    accounts: VoucherAccounts,
    partner_code: str,
) -> list[VoucherLine]:
    ymd = _ymd(trade.trade_date)
    qty = trade.quantity
    price = trade.price
    stock_name = (trade.stock_name or "").strip()
    broker_code, broker_name = "", ""

    principal = _won(qty * price)
    fee = _won(trade.fee)
    tax = _won(getattr(trade, "tax", 0) or 0)
    if trade.settlement_amount is not None:
        total_out = _won(trade.settlement_amount)
    else:
        total_out = principal + fee + tax

    memo = _trade_summary_buy(trade)
    lines: list[VoucherLine] = []
    for item in (
        _line(
            ymd=ymd,
            side=DR,
            acct=accounts.security,
            partner_code=partner_code,
            partner_name=stock_name,
            amount=principal,
            summary=memo,
        ),
        _line(
            ymd=ymd,
            side=DR,
            acct=accounts.fee,
            partner_code=partner_code,
            partner_name=stock_name,
            amount=fee,
            summary="주식매수수수료",
        ),
        _line(
            ymd=ymd,
            side=CR,
            acct=accounts.deposit,
            partner_code=broker_code,
            partner_name=broker_name,
            amount=total_out,
            summary=memo,
        ),
    ):
        if item:
            lines.append(item)
    return lines


def _sell_lines(
    trade: Trade,
    sell: SellResult | None,
    accounts: VoucherAccounts,
    partner_code: str,
) -> list[VoucherLine]:
    """매도 분개: 투자유가증권 대변을 FIFO 매수 레이어별로 분할."""
    ymd = _ymd(trade.trade_date)
    qty = trade.quantity
    price = trade.price
    stock_name = (trade.stock_name or "").strip()
    broker_code, broker_name = "", ""

    lines: list[VoucherLine] = []
    book = 0

    # 1) FIFO 소진 레이어별 투자유가증권 대변
    if sell and sell.matches:
        for match in sell.matches:
            used_qty = match.matched_qty
            layer_price = match.buy_price
            layer_amt = _won(used_qty * layer_price)
            book += layer_amt
            item = _line(
                ymd=ymd,
                side=CR,
                acct=accounts.security,
                partner_code=partner_code,
                partner_name=stock_name,
                amount=layer_amt,
                summary=(
                    f"주식매도 원가 @{_fmt_num(used_qty)} * {_fmt_num(layer_price)}"
                ),
            )
            if item:
                lines.append(item)

    fee = _won(trade.fee)
    tax = _won(getattr(trade, "tax", 0) or 0)
    gross = _won(qty * price)
    if trade.settlement_amount is not None:
        bank_in = _won(trade.settlement_amount)
    else:
        bank_in = max(gross - fee - tax, 0)

    sell_memo = _trade_summary_sell(trade)
    pnl = bank_in + fee + tax - book

    # 2) 기타제예금 / 수수료 / 제세금 / 처분손익
    for item in (
        _line(
            ymd=ymd,
            side=DR,
            acct=accounts.deposit,
            partner_code=broker_code,
            partner_name=broker_name,
            amount=bank_in,
            summary=sell_memo,
        ),
        _line(
            ymd=ymd,
            side=DR,
            acct=accounts.fee,
            partner_code=broker_code,
            partner_name=broker_name,
            amount=fee,
            summary="주식매도수수료",
        ),
        _line(
            ymd=ymd,
            side=DR,
            acct=accounts.fee,
            partner_code=broker_code,
            partner_name=broker_name,
            amount=tax,
            summary="주식매도제세금",
        ),
    ):
        if item:
            lines.append(item)

    if pnl > 0:
        gain = _line(
            ymd=ymd,
            side=CR,
            acct=accounts.gain,
            partner_code=partner_code,
            partner_name=stock_name,
            amount=pnl,
            summary="주식매도",
        )
        if gain:
            lines.append(gain)
    elif pnl < 0:
        loss = _line(
            ymd=ymd,
            side=DR,
            acct=accounts.loss,
            partner_code=partner_code,
            partner_name=stock_name,
            amount=abs(pnl),
            summary="주식매도",
        )
        if loss:
            lines.append(loss)

    return lines


def trades_to_voucher_lines(
    trades: Iterable[Trade],
    sells: Iterable[SellResult],
    *,
    account_config: AccountConfig | None = None,
    partner_by_stock_id: Mapping[int, str] | None = None,
) -> list[VoucherLine]:
    """
    sells는 전체 이력 FIFO 결과여야 매도 원가가 정확하다.
    trades는 전표에 포함할 기간 필터된 거래.
    """
    accounts = VoucherAccounts.from_config(account_config)
    sell_by_id = {s.trade_id: s for s in sells if s.trade_id is not None}
    ordered = sorted(trades, key=lambda t: (t.trade_date, t.id or 0))
    lines: list[VoucherLine] = []
    for trade in ordered:
        partner = _partner_for_trade(trade, partner_by_stock_id)
        if trade.side == "DIVIDEND":
            continue
        if trade.side == "BUY":
            lines.extend(_buy_lines(trade, accounts, partner))
        else:
            lines.extend(
                _sell_lines(trade, sell_by_id.get(trade.id), accounts, partner)
            )
    return lines


def count_voucher_lines(
    trades: Iterable[Trade],
    sells: Iterable[SellResult],
    *,
    account_config: AccountConfig | None = None,
    partner_by_stock_id: Mapping[int, str] | None = None,
) -> int:
    return len(
        trades_to_voucher_lines(
            trades,
            sells,
            account_config=account_config,
            partner_by_stock_id=partner_by_stock_id,
        )
    )


def _convert_xls_to_xlsx(xls_path: Path, xlsx_path: Path) -> None:
    """원본 .xls 템플릿을 openpyxl용 .xlsx로 1회 변환(값·병합 유지)."""
    import xlrd

    book = xlrd.open_workbook(str(xls_path), formatting_info=False)
    sheet = book.sheet_by_index(0)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet.name or "Sheet1"

    for r in range(sheet.nrows):
        for c in range(sheet.ncols):
            val = sheet.cell_value(r, c)
            if val == "":
                continue
            cell = ws.cell(row=r + 1, column=c + 1, value=val)
            if r + 1 == HEADER_ROW:
                cell.alignment = Alignment(
                    wrap_text=True, horizontal="center", vertical="center"
                )

    for rlo, rhi, clo, chi in sheet.merged_cells:
        # xlrd: rhi/chi exclusive
        ws.merge_cells(
            start_row=rlo + 1,
            end_row=rhi,
            start_column=clo + 1,
            end_column=chi,
        )

    ws.row_dimensions[HEADER_ROW].height = 40
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)


def _load_template_workbook() -> Workbook:
    """원본 양식(xlsx 캐시, 없으면 xls에서 변환)을 로드한다."""
    if not TEMPLATE_XLSX.exists():
        if not TEMPLATE_XLS.exists():
            raise FileNotFoundError(
                f"전표 양식 파일이 없습니다: {TEMPLATE_XLS}\n"
                "데스크톱의 '엑셀자료일반전표전송.xls'를 templates 폴더에 복사해 주세요."
            )
        _convert_xls_to_xlsx(TEMPLATE_XLS, TEMPLATE_XLSX)
    return load_workbook(TEMPLATE_XLSX)


def _clear_data_rows(ws) -> None:
    """템플릿 데이터 영역(11행~)을 비운다."""
    if ws.max_row >= DATA_START_ROW:
        ws.delete_rows(DATA_START_ROW, ws.max_row - DATA_START_ROW + 1)


def _fill_company(ws, company_name: str = "", biz_reg_no: str = "") -> None:
    """원본 양식 위치: 회사명 L3(12), 사업자등록번호 O3(15)."""
    if company_name:
        ws.cell(row=3, column=12, value=company_name)
    if biz_reg_no:
        ws.cell(row=3, column=15, value=biz_reg_no)


def build_voucher_workbook(
    trades: list[Trade],
    sells: list[SellResult],
    *,
    company_name: str = "",
    biz_reg_no: str = "",
    account_config: AccountConfig | None = None,
    partner_by_stock_id: Mapping[int, str] | None = None,
) -> Workbook:
    wb = _load_template_workbook()
    ws = wb.active
    _clear_data_rows(ws)
    _fill_company(ws, company_name=company_name, biz_reg_no=biz_reg_no)

    lines = trades_to_voucher_lines(
        trades,
        sells,
        account_config=account_config,
        partner_by_stock_id=partner_by_stock_id,
    )
    for offset, line in enumerate(lines):
        row_idx = DATA_START_ROW + offset
        for col_idx, value in enumerate(line.as_row(), start=1):
            if value == "" or value is None:
                continue
            ws.cell(row=row_idx, column=col_idx, value=value)
    return wb


def export_voucher_excel_bytes(
    trades: list[Trade],
    sells: list[SellResult],
    *,
    company_name: str = "",
    biz_reg_no: str = "",
    account_config: AccountConfig | None = None,
    partner_by_stock_id: Mapping[int, str] | None = None,
) -> bytes:
    wb = build_voucher_workbook(
        trades,
        sells,
        company_name=company_name,
        biz_reg_no=biz_reg_no,
        account_config=account_config,
        partner_by_stock_id=partner_by_stock_id,
    )
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
