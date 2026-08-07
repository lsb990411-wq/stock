"""데이터 모델 정의."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal

TradeSide = Literal["BUY", "SELL", "DIVIDEND"]
MarketType = Literal["domestic", "overseas"]

MARKET_DOMESTIC: MarketType = "domestic"
MARKET_OVERSEAS: MarketType = "overseas"
VALID_MARKETS: frozenset[str] = frozenset({MARKET_DOMESTIC, MARKET_OVERSEAS})

FX_CURRENCIES = ("USD", "EUR", "JPY", "HKD", "CNY", "GBP", "SGD", "AUD")


def normalize_market(value: str | None) -> MarketType:
    """국내/해외 시장 키로 정규화. 미지정·레거시는 domestic."""
    text = (value or "").strip().lower()
    if text in {
        MARKET_OVERSEAS,
        "해외",
        "해외주식",
        "foreign",
        "us",
        "usa",
        "global",
        "overseas",
    }:
        return MARKET_OVERSEAS
    return MARKET_DOMESTIC


def normalize_currency(value: str | None) -> str:
    text = (value or "USD").strip().upper()
    return text if text else "USD"


@dataclass
class Business:
    id: int | None
    name: str
    note: str = ""
    created_at: str = ""
    code: str = ""  # 거래처코드
    account_no: str = ""  # 계좌번호

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Stock:
    id: int | None
    code: str
    name: str
    market: str = MARKET_DOMESTIC  # domestic | overseas
    note: str = ""
    created_at: str = ""
    partner_code: str = ""  # 회계 거래처코드
    business_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Account:
    """사업자·시장별 증권사/계좌(거래처) 마스터."""

    id: int | None
    business_id: int
    name: str
    code: str = ""
    account_no: str = ""
    note: str = ""
    created_at: str = ""
    market: str = MARKET_DOMESTIC  # domestic | overseas

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trade:
    id: int | None
    trade_date: str  # YYYY-MM-DD
    business_id: int
    stock_id: int
    side: TradeSide
    quantity: float
    price: float  # 원화 환산 단가 (해외: price_fx * fx_rate)
    fee: float = 0.0  # 원화 환산 수수료
    tax: float = 0.0  # 원화 환산 제세금
    settlement_amount: float | None = None  # 원화 환산 정산
    memo: str = ""
    source: str = "manual"  # manual | csv | broker
    created_at: str = ""
    # 외화 필드 (해외주식)
    currency: str = "KRW"
    fx_rate: float = 0.0  # 적용 환율 (₩/외화)
    price_fx: float = 0.0  # 외화 단가
    fee_fx: float = 0.0
    tax_fx: float = 0.0
    # join fields (optional)
    business_name: str = ""
    stock_code: str = ""
    stock_name: str = ""

    @property
    def is_overseas(self) -> bool:
        return float(self.fx_rate or 0) > 0 and normalize_currency(self.currency) != "KRW"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Lot:
    """FIFO 잔여 매수 로트."""

    trade_id: int
    trade_date: str
    business_id: int
    stock_id: int
    original_qty: float
    remaining_qty: float
    price: float
    fee: float
    stock_code: str = ""
    stock_name: str = ""
    business_name: str = ""

    @property
    def remaining_fee(self) -> float:
        if self.original_qty <= 0:
            return 0.0
        return self.fee * (self.remaining_qty / self.original_qty)

    @property
    def cost_basis(self) -> float:
        """FIFO 잔여 매수원가(원가잔액) = 잔여수량 × 매수단가. 수수료는 포함하지 않음."""
        return self.remaining_qty * self.price


@dataclass
class MatchDetail:
    """매도 1건에 대해 소비된 매수 로트 상세."""

    buy_trade_id: int
    buy_date: str
    matched_qty: float
    buy_price: float
    buy_fee_allocated: float
    sell_fee_allocated: float
    sell_price: float
    realized_pnl: float


@dataclass
class SellResult:
    trade_id: int | None
    trade_date: str
    business_id: int
    stock_id: int
    quantity: float
    price: float
    fee: float
    realized_pnl: float
    matches: list[MatchDetail] = field(default_factory=list)
    shortfall_qty: float = 0.0
    stock_code: str = ""
    stock_name: str = ""
    business_name: str = ""


@dataclass
class Position:
    """사업자+종목별 잔고 요약."""

    business_id: int
    business_name: str
    stock_id: int
    stock_code: str
    stock_name: str
    quantity: float
    avg_price: float
    total_cost: float
    realized_pnl: float
    lots: list[Lot] = field(default_factory=list)


@dataclass
class AccountConfig:
    """사업자별 회계 전표용 계정과목 코드 (매매 + 이자·배당)."""

    security_code: str = "0178"  # 투자유가증권
    fee_code: str = "0965"  # 지급수수료
    deposit_code: str = "0104"  # 기타제예금
    gain_code: str = "0915"  # 투자자산처분이익
    loss_code: str = "0953"  # 투자자산처분손실
    interest_code: str = "0901"  # 이자수익
    prepaid_tax_code: str = "0136"  # 선납세금
    bank_code: str = "0103"  # 보통예금

    def normalize(self) -> AccountConfig:
        return AccountConfig(
            security_code=(self.security_code or "0178").strip(),
            fee_code=(self.fee_code or "0965").strip(),
            deposit_code=(self.deposit_code or "0104").strip(),
            gain_code=(self.gain_code or "0915").strip(),
            loss_code=(self.loss_code or "0953").strip(),
            interest_code=(self.interest_code or "0901").strip(),
            prepaid_tax_code=(self.prepaid_tax_code or "0136").strip(),
            bank_code=(self.bank_code or "0103").strip(),
        )

    def to_income_config(self) -> IncomeAccountConfig:
        n = self.normalize()
        return IncomeAccountConfig(
            interest_code=n.interest_code,
            prepaid_tax_code=n.prepaid_tax_code,
            bank_code=n.bank_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalize())


IncomeType = Literal["INTEREST", "DIVIDEND"]


@dataclass
class IncomeAccountConfig:
    """이자·배당소득 전표용 계정코드 (AccountConfig에서 추출 가능)."""

    interest_code: str = "0901"  # 이자수익
    prepaid_tax_code: str = "0136"  # 선납세금
    bank_code: str = "0103"  # 보통예금

    def normalize(self) -> IncomeAccountConfig:
        return IncomeAccountConfig(
            interest_code=(self.interest_code or "0901").strip(),
            prepaid_tax_code=(self.prepaid_tax_code or "0136").strip(),
            bank_code=(self.bank_code or "0103").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalize())


@dataclass
class BrokerPartner:
    """사업자별 증권사(금융기관) → 회계 거래처코드."""

    id: int | None
    broker_name: str
    partner_code: str = ""
    created_at: str = ""
    business_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IncomeRecord:
    """이자·배당소득(원천징수) 1건."""

    id: int | None
    pay_date: str  # YYYY-MM-DD
    business_id: int
    product_name: str = ""
    broker_name: str = ""
    income_type: IncomeType = "INTEREST"
    gross_amount: float = 0.0  # 지급액(소득금액)
    corp_tax: float = 0.0  # 법인세
    local_tax: float = 0.0  # 지방소득세
    memo: str = ""
    source: str = "manual"  # manual | excel | pdf
    created_at: str = ""
    business_name: str = ""

    @property
    def net_amount(self) -> float:
        return float(self.gross_amount) - float(self.corp_tax) - float(self.local_tax)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def today_str() -> str:
    return date.today().isoformat()


def now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_side(value: Any) -> TradeSide:
    text = str(value).strip().upper()
    buy_tokens = {"BUY", "매수", "매입", "B", "1", "BID"}
    sell_tokens = {"SELL", "매도", "S", "2", "ASK"}
    if text in buy_tokens:
        return "BUY"
    if text in sell_tokens:
        return "SELL"
    if "매수" in str(value) or "매입" in str(value):
        return "BUY"
    if "매도" in str(value):
        return "SELL"
    raise ValueError(f"알 수 없는 거래유형: {value}")
