"""Supabase 기반 데이터 저장소."""

from __future__ import annotations

import time
from dataclasses import MISSING, fields
from datetime import date
from typing import Any, Callable, TypeVar

from .models import (
    Account,
    AccountConfig,
    BrokerPartner,
    Business,
    IncomeAccountConfig,
    IncomeRecord,
    MARKET_DOMESTIC,
    Stock,
    Trade,
    coerce_fx_rate,
    normalize_market,
    now_str,
)
from .supabase_client import get_supabase_client, is_transient_network_error

DEFAULT_DB_PATH = None  # 호환용 (더 이상 SQLite 파일을 쓰지 않음)
_PAGE = 1000
_RETRY_ATTEMPTS = 4
_RETRY_BASE_DELAY = 0.5

T = TypeVar("T")


def _from_row(cls: type, row: Any) -> Any:
    """API/DB row를 dataclass 생성자 인자에만 맞게 변환한다."""
    raw = dict(row or {})
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name in raw and raw[f.name] is not None:
            kwargs[f.name] = raw[f.name]
        elif f.default is not MISSING:
            kwargs[f.name] = f.default
        elif f.default_factory is not MISSING:  # type: ignore[misc]
            kwargs[f.name] = f.default_factory()  # type: ignore[misc]
        else:
            kwargs[f.name] = raw.get(f.name)
    return cls(**kwargs)


def _with_retry(op: Callable[[], T], *, label: str = "supabase") -> T:
    """일시적 네트워크 오류 시 지수 백오프로 재시도."""
    last: BaseException | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return op()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not is_transient_network_error(exc):
                raise
            if attempt >= _RETRY_ATTEMPTS - 1:
                break
            delay = _RETRY_BASE_DELAY * (2**attempt)
            time.sleep(delay)
    assert last is not None
    raise RuntimeError(
        f"데이터베이스 연결에 실패했습니다 ({label}, {_RETRY_ATTEMPTS}회 재시도). "
        f"잠시 후 새로고침해 주세요. 원인: {last}"
    ) from last


class Storage:
    def __init__(self, db_path: str | None = None) -> None:  # noqa: ARG002
        self._client = get_supabase_client()
        self.db_path = "supabase"

    def _sb(self):
        return self._client

    def close(self) -> None:
        return None

    def _fetch(
        self,
        table: str,
        *,
        eq: dict[str, Any] | None = None,
        order: str | None = None,
        desc: bool = False,
        extra: Any | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            def _run(start: int = start) -> list[dict[str, Any]]:
                q = self._sb().table(table).select("*")
                if eq:
                    for key, value in eq.items():
                        if value is not None:
                            q = q.eq(key, value)
                if extra is not None:
                    q = extra(q)
                if order:
                    q = q.order(order, desc=desc)
                res = q.range(start, start + _PAGE - 1).execute()
                return list(res.data or [])

            chunk = _with_retry(_run, label=f"{table}.select")
            rows.extend(chunk)
            if len(chunk) < _PAGE:
                break
            start += _PAGE
        return rows

    def _one(
        self,
        table: str,
        *,
        eq: dict[str, Any] | None = None,
        extra: Any | None = None,
    ) -> dict[str, Any] | None:
        def _run() -> dict[str, Any] | None:
            q = self._sb().table(table).select("*")
            if eq:
                for key, value in eq.items():
                    if value is not None:
                        q = q.eq(key, value)
            if extra is not None:
                q = extra(q)
            res = q.limit(1).execute()
            data = res.data or []
            return data[0] if data else None

        return _with_retry(_run, label=f"{table}.one")

    def _insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            res = self._sb().table(table).insert(payload).select("*").execute()
            data = res.data or []
            if not data:
                raise RuntimeError(f"{table} 저장에 실패했습니다.")
            return data[0]

        return _with_retry(_run, label=f"{table}.insert")

    def _insert_many(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        chunk_size: int = 500,
    ) -> list[dict[str, Any]]:
        """리스트를 청크 단위로 한 번에 insert (네트워크 왕복 최소화)."""
        if not rows:
            return []
        out: list[dict[str, Any]] = []
        size = max(1, int(chunk_size))
        for i in range(0, len(rows), size):
            batch = rows[i : i + size]

            def _run(b: list[dict[str, Any]] = batch) -> list[dict[str, Any]]:
                res = self._sb().table(table).insert(b).select("*").execute()
                return list(res.data or [])

            out.extend(_with_retry(_run, label=f"{table}.bulk_insert"))
        return out

    def _update(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        eq: dict[str, Any],
    ) -> int:
        def _run() -> int:
            q = self._sb().table(table).update(payload)
            for key, value in eq.items():
                q = q.eq(key, value)
            res = q.select("*").execute()
            return len(res.data or [])

        return _with_retry(_run, label=f"{table}.update")

    def _delete(self, table: str, *, eq: dict[str, Any]) -> int:
        def _run() -> int:
            q = self._sb().table(table).delete()
            for key, value in eq.items():
                q = q.eq(key, value)
            res = q.select("*").execute()
            return len(res.data or [])

        return _with_retry(_run, label=f"{table}.delete")

    def _count(self, table: str, *, eq: dict[str, Any] | None = None) -> int:
        def _run() -> int:
            q = self._sb().table(table).select("id", count="exact")
            if eq:
                for key, value in eq.items():
                    q = q.eq(key, value)
            res = q.limit(1).execute()
            return int(res.count or 0)

        return _with_retry(_run, label=f"{table}.count")


    # ------------------------------------------------------------------
    # Account config
    # ------------------------------------------------------------------
    def get_account_config(
        self,
        business_id: int | None,
        market: str | None = MARKET_DOMESTIC,
    ) -> AccountConfig:
        if business_id is None:
            return AccountConfig()
        mkt = normalize_market(market)
        row = self._one(
            "account_config",
            eq={"business_id": int(business_id), "market": mkt},
        )
        if row is None:
            return AccountConfig()
        return AccountConfig(
            security_code=str(row.get("security_code") or "0178"),
            fee_code=str(row.get("fee_code") or "0965"),
            deposit_code=str(row.get("deposit_code") or "0104"),
            gain_code=str(row.get("gain_code") or "0915"),
            loss_code=str(row.get("loss_code") or "0953"),
            interest_code=str(row.get("interest_code") or "0901"),
            prepaid_tax_code=str(row.get("prepaid_tax_code") or "0136"),
            bank_code=str(row.get("bank_code") or "0103"),
        ).normalize()

    def save_account_config(
        self,
        business_id: int | None,
        config: AccountConfig | dict[str, Any],
        market: str | None = MARKET_DOMESTIC,
    ) -> AccountConfig:
        if business_id is None:
            raise ValueError("사업자를 선택한 뒤 계정과목을 저장해 주세요.")
        mkt = normalize_market(market)
        if isinstance(config, dict):
            cfg = AccountConfig(
                security_code=str(
                    config.get("security_code")
                    or config.get("stock_code")
                    or "0178"
                ),
                fee_code=str(config.get("fee_code") or "0965"),
                deposit_code=str(config.get("deposit_code") or "0104"),
                gain_code=str(config.get("gain_code") or "0915"),
                loss_code=str(config.get("loss_code") or "0953"),
                interest_code=str(config.get("interest_code") or "0901"),
                prepaid_tax_code=str(config.get("prepaid_tax_code") or "0136"),
                bank_code=str(config.get("bank_code") or "0103"),
            ).normalize()
        else:
            cfg = config.normalize()
        for label, code in (
            ("투자유가증권", cfg.security_code),
            ("지급수수료", cfg.fee_code),
            ("기타제예금", cfg.deposit_code),
            ("투자자산처분이익", cfg.gain_code),
            ("투자자산처분손실", cfg.loss_code),
            ("이자수익", cfg.interest_code),
            ("선납세금", cfg.prepaid_tax_code),
            ("보통예금", cfg.bank_code),
        ):
            if not code:
                raise ValueError(f"{label} 계정코드는 필수입니다.")
        payload = {
            "business_id": int(business_id),
            "market": mkt,
            "security_code": cfg.security_code,
            "fee_code": cfg.fee_code,
            "deposit_code": cfg.deposit_code,
            "gain_code": cfg.gain_code,
            "loss_code": cfg.loss_code,
            "interest_code": cfg.interest_code,
            "prepaid_tax_code": cfg.prepaid_tax_code,
            "bank_code": cfg.bank_code,
        }
        _with_retry(
            lambda: self._sb()
            .table("account_config")
            .upsert(payload, on_conflict="business_id,market")
            .execute(),
            label="account_config.upsert",
        )
        return cfg

    def update_stock_partner_codes(
        self,
        updates: dict[int, str],
        business_id: int | None = None,
    ) -> int:
        if not updates:
            return 0
        changed = 0
        for stock_id, partner_code in updates.items():
            eq: dict[str, Any] = {"id": int(stock_id)}
            if business_id is not None:
                eq["business_id"] = int(business_id)
            changed += self._update(
                "stocks",
                {"partner_code": str(partner_code or "").strip()},
                eq=eq,
            )
        return changed

    # ------------------------------------------------------------------
    # Business
    # ------------------------------------------------------------------
    def add_business(
        self,
        name: str,
        note: str = "",
        code: str = "",
        account_no: str = "",
    ) -> int:
        name = name.strip()
        if not name:
            raise ValueError("사업자명은 필수입니다.")
        row = self._insert(
            "businesses",
            {
                "name": name,
                "note": note.strip(),
                "created_at": now_str(),
                "code": code.strip(),
                "account_no": account_no.strip(),
            },
        )
        return int(row["id"])

    def update_business(
        self,
        business_id: int,
        name: str,
        note: str = "",
        code: str = "",
        account_no: str = "",
    ) -> None:
        self._update(
            "businesses",
            {
                "name": name.strip(),
                "note": note.strip(),
                "code": code.strip(),
                "account_no": account_no.strip(),
            },
            eq={"id": int(business_id)},
        )

    def delete_entity(self, business_id: int) -> None:
        bid = int(business_id)
        self._delete("trades", eq={"business_id": bid})
        self._delete("income_records", eq={"business_id": bid})
        self._delete("stocks", eq={"business_id": bid})
        self._delete("accounts", eq={"business_id": bid})
        self._delete("broker_partners", eq={"business_id": bid})
        self._delete("account_config", eq={"business_id": bid})
        self._delete("businesses", eq={"id": bid})

    def delete_business(self, business_id: int) -> None:
        self.delete_entity(business_id)

    def count_trades_for_business(self, business_id: int) -> int:
        return self._count("trades", eq={"business_id": int(business_id)})

    def delete_account(self, account_id: int, *, force: bool = False) -> None:
        _ = force
        self._delete("accounts", eq={"id": int(account_id)})

    def get_all_accounts(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Account]:
        return self.list_accounts(business_id, market=market)

    def list_accounts(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Account]:
        if business_id is None:
            return []
        eq: dict[str, Any] = {"business_id": int(business_id)}
        if market is not None:
            eq["market"] = normalize_market(market)
        rows = self._fetch("accounts", eq=eq, order="name")
        return [_from_row(Account, r) for r in rows]

    def add_account(
        self,
        business_id: int,
        name: str,
        code: str = "",
        account_no: str = "",
        note: str = "",
        market: str | None = MARKET_DOMESTIC,
    ) -> int:
        name = name.strip()
        if not name:
            raise ValueError("거래처명은 필수입니다.")
        row = self._insert(
            "accounts",
            {
                "business_id": int(business_id),
                "name": name,
                "code": code.strip(),
                "account_no": account_no.strip(),
                "note": note.strip(),
                "created_at": now_str(),
                "market": normalize_market(market),
            },
        )
        return int(row["id"])

    def get_account_by_name(
        self,
        name: str,
        business_id: int,
        market: str | None = MARKET_DOMESTIC,
    ) -> Account | None:
        name = name.strip()
        if not name:
            return None
        mkt = normalize_market(market)
        for acct in self.list_accounts(business_id, market=mkt):
            if (acct.name or "").strip() == name:
                return acct
        return None

    def get_or_create_account(
        self,
        name: str,
        business_id: int,
        *,
        market: str | None = MARKET_DOMESTIC,
        code: str = "",
        account_no: str = "",
        note: str = "",
    ) -> Account:
        """증권사/계좌 마스터 조회·생성."""
        name = (name or "").strip()
        if not name:
            raise ValueError("증권사(거래처)명은 필수입니다.")
        mkt = normalize_market(market)
        existing = self.get_account_by_name(name, business_id, market=mkt)
        if existing:
            return existing
        aid = self.add_account(
            business_id,
            name,
            code=code,
            account_no=account_no,
            note=note,
            market=mkt,
        )
        return Account(
            id=aid,
            business_id=int(business_id),
            name=name,
            code=code.strip(),
            account_no=account_no.strip(),
            note=note.strip(),
            created_at=now_str(),
            market=mkt,
        )

    def count_trades_for_account(self, account_id: int) -> int:
        rows = self._fetch("trades", eq={"account_id": int(account_id)})
        return len(rows)

    def list_businesses(self) -> list[Business]:
        rows = self._fetch("businesses", order="name")
        return [_from_row(Business, r) for r in rows]

    def get_business_by_name(self, name: str) -> Business | None:
        row = self._one("businesses", eq={"name": name.strip()})
        return _from_row(Business, row) if row else None

    def get_or_create_business(self, name: str) -> Business:
        existing = self.get_business_by_name(name)
        if existing:
            return existing
        bid = self.add_business(name)
        return Business(
            id=bid,
            name=name.strip(),
            note="",
            created_at=now_str(),
            code="",
            account_no="",
        )

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------
    def add_stock(
        self,
        code: str,
        name: str,
        market: str = MARKET_DOMESTIC,
        note: str = "",
        partner_code: str = "",
        business_id: int | None = None,
    ) -> int:
        if business_id is None:
            raise ValueError("종목 등록 시 사업자가 필요합니다.")
        code = code.strip()
        name = name.strip()
        if not code or not name:
            raise ValueError("종목코드와 종목명은 필수입니다.")
        row = self._insert(
            "stocks",
            {
                "business_id": int(business_id),
                "code": code,
                "name": name,
                "market": normalize_market(market),
                "note": note.strip(),
                "partner_code": partner_code.strip(),
                "created_at": now_str(),
            },
        )
        return int(row["id"])

    def update_stock(
        self,
        stock_id: int,
        code: str,
        name: str,
        market: str = MARKET_DOMESTIC,
        note: str = "",
        partner_code: str = "",
        business_id: int | None = None,
    ) -> None:
        eq: dict[str, Any] = {"id": int(stock_id)}
        if business_id is not None:
            eq["business_id"] = int(business_id)
        self._update(
            "stocks",
            {
                "code": code.strip(),
                "name": name.strip(),
                "market": normalize_market(market),
                "note": note.strip(),
                "partner_code": partner_code.strip(),
            },
            eq=eq,
        )

    def count_trades_for_stock(self, stock_id: int) -> int:
        return self._count("trades", eq={"stock_id": int(stock_id)})

    def delete_stock(self, stock_id: int, *, force: bool = False) -> None:
        used = self.count_trades_for_stock(stock_id)
        if used and not force:
            raise ValueError(
                f"이 종목과 연결된 거래 내역이 {used}건 존재합니다. "
                "확인 후 강제 삭제해 주세요."
            )
        if force:
            self._delete("trades", eq={"stock_id": int(stock_id)})
        self._delete("stocks", eq={"id": int(stock_id)})

    def delete_stock_by_code(
        self,
        stock_code: str,
        *,
        force: bool = False,
        business_id: int | None = None,
        market: str | None = None,
    ) -> None:
        stock = self.get_stock_by_code(
            stock_code, business_id=business_id, market=market
        )
        if not stock or stock.id is None:
            raise ValueError(f"종목코드 '{stock_code}'를 찾을 수 없습니다.")
        self.delete_stock(int(stock.id), force=force)

    def list_stocks(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Stock]:
        eq: dict[str, Any] = {}
        if business_id is not None:
            eq["business_id"] = int(business_id)
        if market is not None:
            eq["market"] = normalize_market(market)
        rows = self._fetch("stocks", eq=eq or None, order="name")
        return [_from_row(Stock, r) for r in rows]

    def get_all_stocks(
        self,
        business_id: int | None = None,
        market: str | None = None,
    ) -> list[Stock]:
        return self.list_stocks(business_id, market=market)

    def get_stock_by_code(
        self,
        code: str,
        business_id: int | None = None,
        market: str | None = None,
    ) -> Stock | None:
        eq: dict[str, Any] = {"code": code.strip()}
        if business_id is not None:
            eq["business_id"] = int(business_id)
        if market is not None:
            eq["market"] = normalize_market(market)
        row = self._one("stocks", eq=eq)
        return _from_row(Stock, row) if row else None

    def get_stock_by_name(
        self,
        name: str,
        business_id: int | None = None,
        market: str | None = None,
    ) -> Stock | None:
        eq: dict[str, Any] = {}
        if business_id is not None:
            eq["business_id"] = int(business_id)
        if market is not None:
            eq["market"] = normalize_market(market)

        def extra(q):
            return q.ilike("name", name.strip())

        row = self._one("stocks", eq=eq or None, extra=extra)
        return _from_row(Stock, row) if row else None

    def get_or_create_stock(
        self,
        code: str,
        name: str,
        business_id: int | None = None,
        market: str | None = MARKET_DOMESTIC,
    ) -> Stock:
        if business_id is None:
            raise ValueError("종목 조회/등록 시 사업자가 필요합니다.")
        mkt = normalize_market(market)
        existing = self.get_stock_by_code(code, business_id=business_id, market=mkt)
        if existing:
            if name and existing.name != name.strip():
                self.update_stock(
                    existing.id,  # type: ignore[arg-type]
                    existing.code,
                    name.strip(),
                    mkt,
                    existing.note,
                    existing.partner_code,
                    business_id=business_id,
                )
                existing.name = name.strip()
            return existing
        sid = self.add_stock(code, name, market=mkt, business_id=business_id)
        return Stock(
            id=sid,
            code=code.strip(),
            name=name.strip(),
            market=mkt,
            created_at=now_str(),
            business_id=int(business_id),
        )

    def get_or_create_stock_by_name(
        self,
        name: str,
        code: str | None = None,
        business_id: int | None = None,
        market: str | None = MARKET_DOMESTIC,
    ) -> Stock:
        if business_id is None:
            raise ValueError("종목 조회/등록 시 사업자가 필요합니다.")
        mkt = normalize_market(market)
        name = name.strip()
        if not name:
            raise ValueError("종목명은 필수입니다.")
        existing = self.get_stock_by_name(name, business_id=business_id, market=mkt)
        if existing:
            return existing
        clean_code = (code or "").strip()
        if clean_code:
            return self.get_or_create_stock(
                clean_code, name, business_id=business_id, market=mkt
            )
        n = 1
        while True:
            candidate = f"TMP{n:04d}"
            if not self.get_stock_by_code(
                candidate, business_id=business_id, market=mkt
            ):
                break
            n += 1
        sid = self.add_stock(candidate, name, market=mkt, business_id=business_id)
        return Stock(
            id=sid,
            code=candidate,
            name=name,
            market=mkt,
            created_at=now_str(),
            business_id=int(business_id),
        )

    # ------------------------------------------------------------------
    # Trade
    # ------------------------------------------------------------------
    def _trade_payload(self, trade: Trade) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "trade_date": trade.trade_date,
            "business_id": trade.business_id,
            "stock_id": trade.stock_id,
            "side": trade.side,
            "quantity": trade.quantity,
            "price": trade.price,
            "fee": trade.fee,
            "tax": float(getattr(trade, "tax", 0) or 0),
            "settlement_amount": trade.settlement_amount,
            "memo": trade.memo or "",
            "source": trade.source or "manual",
            "created_at": trade.created_at or now_str(),
            "currency": getattr(trade, "currency", None) or "KRW",
            "fx_rate": float(coerce_fx_rate(getattr(trade, "fx_rate", 0))),
            "price_fx": float(getattr(trade, "price_fx", 0) or 0),
            "fee_fx": float(getattr(trade, "fee_fx", 0) or 0),
            "tax_fx": float(getattr(trade, "tax_fx", 0) or 0),
        }
        aid = getattr(trade, "account_id", None)
        if aid is not None:
            try:
                payload["account_id"] = int(aid)
            except (TypeError, ValueError):
                payload["account_id"] = None
        else:
            payload["account_id"] = None
        # 처분손익 오버라이드: None이면 DB에 null
        disp = getattr(trade, "disposal_pnl", None)
        if disp is None:
            payload["disposal_pnl"] = None
        else:
            try:
                payload["disposal_pnl"] = float(disp)
            except (TypeError, ValueError):
                payload["disposal_pnl"] = None
        return payload

    def add_trade(self, trade: Trade) -> int:
        row = self._insert("trades", self._trade_payload(trade))
        return int(row["id"])

    def add_trades_bulk(self, trades: list[Trade], *, chunk_size: int = 500) -> int:
        """거래 리스트를 청크 단위로 대량 insert."""
        if not trades:
            return 0
        payload = [self._trade_payload(t) for t in trades]
        inserted = self._insert_many("trades", payload, chunk_size=chunk_size)
        return len(inserted) if inserted else len(payload)

    def ensure_stocks_bulk(
        self,
        items: list[tuple[str, str]],
        *,
        business_id: int,
        market: str | None = MARKET_DOMESTIC,
    ) -> dict[str, Stock]:
        """(code, name) 목록을 미리 로드·일괄 생성 후 code→Stock 맵 반환.

        행마다 get_or_create_stock을 호출하지 않도록, 누락 종목만 한 번에 insert한다.
        """
        if business_id is None:
            raise ValueError("종목 일괄 등록 시 사업자가 필요합니다.")
        bid = int(business_id)
        mkt = normalize_market(market)

        existing = self.list_stocks(business_id=bid, market=mkt)
        by_code: dict[str, Stock] = {
            (s.code or "").strip(): s for s in existing if (s.code or "").strip()
        }

        to_create: list[dict[str, Any]] = []
        seen_new: set[str] = set()
        for code_raw, name_raw in items:
            code = (code_raw or "").strip()
            name = (name_raw or "").strip() or code
            if not code or code in by_code or code in seen_new:
                continue
            seen_new.add(code)
            to_create.append(
                {
                    "business_id": bid,
                    "code": code,
                    "name": name,
                    "market": mkt,
                    "note": "",
                    "partner_code": "",
                    "created_at": now_str(),
                }
            )

        if to_create:
            created_rows = self._insert_many("stocks", to_create, chunk_size=500)
            for row in created_rows:
                stock = _from_row(Stock, row)
                by_code[(stock.code or "").strip()] = stock

        return by_code

    def delete_trade(self, trade_id: int) -> None:
        self._delete("trades", eq={"id": int(trade_id)})

    def update_trade_date(self, trade_id: int, new_date: str) -> None:
        date_str = str(new_date or "").strip()[:10]
        if not date_str:
            raise ValueError("거래일자는 필수입니다.")
        n = self._update("trades", {"trade_date": date_str}, eq={"id": int(trade_id)})
        if n == 0:
            raise ValueError(f"거래 ID {trade_id}를 찾을 수 없습니다.")

    def update_trade_disposal_pnl(
        self, trade_id: int, disposal_pnl: float | None
    ) -> None:
        """매도 거래의 사용자 지정 처분손익(원)을 Supabase trades에 반영.

        Args:
            trade_id: trades.id
            disposal_pnl: 저장할 처분손익(원). None이면 NULL로 두어 FIFO 계산값 사용.

        Raises:
            ValueError: trade_id/값이 잘못되었거나 대상 행이 없을 때
            RuntimeError: DB 업데이트 실패 시 (원인 메시지 포함)
        """
        try:
            tid = int(trade_id)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"유효하지 않은 거래 ID: {trade_id!r}") from exc

        payload: dict[str, Any]
        if disposal_pnl is None:
            expected: float | None = None
            payload = {"disposal_pnl": None}
        else:
            try:
                expected = float(disposal_pnl)
                payload = {"disposal_pnl": expected}
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"처분손익 값이 숫자가 아닙니다: {disposal_pnl!r}"
                ) from exc

        try:
            n = self._update("trades", payload, eq={"id": tid})
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"처분손익 저장 실패 (trade_id={tid}, value={disposal_pnl!r}): {exc}"
            ) from exc

        if n > 0:
            return

        # RETURNING 이 비는 환경(RLS 등) 대비: 재조회로 반영 여부 확인
        row = self._one("trades", eq={"id": tid})
        if row is None:
            raise ValueError(
                f"거래 ID {tid}를 찾을 수 없어 처분손익을 저장하지 못했습니다."
            )
        actual = row.get("disposal_pnl")
        if expected is None:
            if actual is None:
                return
        else:
            try:
                if actual is not None and abs(float(actual) - float(expected)) < 0.5:
                    return
            except (TypeError, ValueError):
                pass
        raise RuntimeError(
            f"처분손익 저장 후 값이 일치하지 않습니다 "
            f"(trade_id={tid}, expected={expected!r}, actual={actual!r})."
        )

    def list_trades(
        self,
        business_id: int | None = None,
        stock_id: int | None = None,
        market: str | None = None,
    ) -> list[Trade]:
        eq: dict[str, Any] = {}
        if business_id is not None:
            eq["business_id"] = int(business_id)
        if stock_id is not None:
            eq["stock_id"] = int(stock_id)
        if market is not None:
            eq["stock_market"] = normalize_market(market)
        rows = self._fetch("trades_enriched", eq=eq or None, order="trade_date")
        rows.sort(key=lambda r: (str(r.get("trade_date") or ""), int(r.get("id") or 0)))
        return [_from_row(Trade, r) for r in rows]

    def get_trades_by_period(
        self,
        business_id: int | None,
        start_date: str | date,
        end_date: str | date,
        market: str | None = None,
    ) -> list[Trade]:
        def _as_ymd(v: str | date) -> str:
            if isinstance(v, date):
                return v.isoformat()
            return str(v).strip()[:10]

        start = _as_ymd(start_date)
        end = _as_ymd(end_date)
        if start > end:
            start, end = end, start
        trades = self.list_trades(business_id=business_id, market=market)
        return [t for t in trades if start <= str(t.trade_date)[:10] <= end]

    def clear_all_trades(self) -> None:
        _with_retry(
            lambda: self._sb().table("trades").delete().neq("id", 0).execute(),
            label="trades.clear_all",
        )

    def clear_trades_for_business(self, business_id: int) -> int:
        return self._delete("trades", eq={"business_id": int(business_id)})

    # ------------------------------------------------------------------
    # Income
    # ------------------------------------------------------------------
    def get_income_account_config(
        self, business_id: int | None
    ) -> IncomeAccountConfig:
        return self.get_account_config(business_id).to_income_config()

    def save_income_account_config(
        self,
        business_id: int | None,
        config: IncomeAccountConfig,
    ) -> IncomeAccountConfig:
        if business_id is None:
            raise ValueError("사업자를 선택한 뒤 계정과목을 저장해 주세요.")
        base = self.get_account_config(business_id)
        cfg = config.normalize()
        base.interest_code = cfg.interest_code
        base.prepaid_tax_code = cfg.prepaid_tax_code
        base.bank_code = cfg.bank_code
        saved = self.save_account_config(business_id, base)
        return saved.to_income_config()

    def list_broker_partners(
        self, business_id: int | None = None
    ) -> list[BrokerPartner]:
        if business_id is None:
            return []
        rows = self._fetch(
            "broker_partners",
            eq={"business_id": int(business_id)},
            order="broker_name",
        )
        return [_from_row(BrokerPartner, r) for r in rows]

    def upsert_broker_partner(
        self,
        broker_name: str,
        partner_code: str = "",
        business_id: int | None = None,
    ) -> int:
        if business_id is None:
            raise ValueError("증권사 매핑 저장 시 사업자가 필요합니다.")
        name = broker_name.strip()
        if not name:
            raise ValueError("증권사명은 필수입니다.")
        code = (partner_code or "").strip()
        existing = self._one(
            "broker_partners",
            eq={"business_id": int(business_id)},
            extra=lambda q: q.ilike("broker_name", name),
        )
        if existing:
            self._update(
                "broker_partners",
                {"partner_code": code, "broker_name": name},
                eq={"id": int(existing["id"]), "business_id": int(business_id)},
            )
            return int(existing["id"])
        row = self._insert(
            "broker_partners",
            {
                "business_id": int(business_id),
                "broker_name": name,
                "partner_code": code,
                "created_at": now_str(),
            },
        )
        return int(row["id"])

    def update_broker_partners(
        self,
        updates: dict[int, tuple[str, str]],
        business_id: int | None = None,
    ) -> int:
        if not updates:
            return 0
        changed = 0
        for pid, (broker_name, partner_code) in updates.items():
            name = str(broker_name or "").strip()
            if not name:
                continue
            eq: dict[str, Any] = {"id": int(pid)}
            if business_id is not None:
                eq["business_id"] = int(business_id)
            changed += self._update(
                "broker_partners",
                {
                    "broker_name": name,
                    "partner_code": str(partner_code or "").strip(),
                },
                eq=eq,
            )
        return changed

    def delete_broker_partner(
        self, partner_id: int, business_id: int | None = None
    ) -> None:
        eq: dict[str, Any] = {"id": int(partner_id)}
        if business_id is not None:
            eq["business_id"] = int(business_id)
        self._delete("broker_partners", eq=eq)

    def broker_partner_map(self, business_id: int | None = None) -> dict[str, str]:
        return {
            (b.broker_name or "").strip().lower(): (b.partner_code or "").strip()
            for b in self.list_broker_partners(business_id)
            if (b.broker_name or "").strip()
        }

    def add_income_record(
        self,
        *,
        pay_date: str,
        business_id: int,
        product_name: str = "",
        broker_name: str = "",
        income_type: str = "INTEREST",
        gross_amount: float = 0.0,
        corp_tax: float = 0.0,
        local_tax: float = 0.0,
        memo: str = "",
        source: str = "manual",
    ) -> int:
        itype = str(income_type or "INTEREST").strip().upper()
        if itype not in ("INTEREST", "DIVIDEND"):
            itype = "INTEREST"
        pay = str(pay_date).strip()[:10]
        if not pay:
            raise ValueError("지급일은 필수입니다.")
        row = self._insert(
            "income_records",
            {
                "pay_date": pay,
                "business_id": int(business_id),
                "product_name": (product_name or "").strip(),
                "broker_name": (broker_name or "").strip(),
                "income_type": itype,
                "gross_amount": float(gross_amount or 0),
                "corp_tax": float(corp_tax or 0),
                "local_tax": float(local_tax or 0),
                "memo": (memo or "").strip(),
                "source": (source or "manual").strip(),
                "created_at": now_str(),
            },
        )
        return int(row["id"])

    def add_income_records_bulk(self, records: list[dict[str, Any]]) -> int:
        n = 0
        for r in records:
            self.add_income_record(**r)
            n += 1
        return n

    def update_income_record(
        self,
        record_id: int,
        *,
        pay_date: str,
        product_name: str = "",
        broker_name: str = "",
        income_type: str = "INTEREST",
        gross_amount: float = 0.0,
        corp_tax: float = 0.0,
        local_tax: float = 0.0,
        memo: str = "",
    ) -> None:
        itype = str(income_type or "INTEREST").strip().upper()
        if itype not in ("INTEREST", "DIVIDEND"):
            itype = "INTEREST"
        self._update(
            "income_records",
            {
                "pay_date": str(pay_date).strip()[:10],
                "product_name": (product_name or "").strip(),
                "broker_name": (broker_name or "").strip(),
                "income_type": itype,
                "gross_amount": float(gross_amount or 0),
                "corp_tax": float(corp_tax or 0),
                "local_tax": float(local_tax or 0),
                "memo": (memo or "").strip(),
            },
            eq={"id": int(record_id)},
        )

    def delete_income_records(self, record_ids: list[int]) -> int:
        if not record_ids:
            return 0
        n = 0
        for rid in record_ids:
            n += self._delete("income_records", eq={"id": int(rid)})
        return n

    def clear_income_records_for_business(self, business_id: int) -> int:
        return self._delete("income_records", eq={"business_id": int(business_id)})

    def list_income_records(
        self, business_id: int | None = None
    ) -> list[IncomeRecord]:
        eq: dict[str, Any] = {}
        if business_id is not None:
            eq["business_id"] = int(business_id)
        rows = self._fetch(
            "income_records_enriched",
            eq=eq or None,
            order="pay_date",
        )
        rows.sort(key=lambda r: (str(r.get("pay_date") or ""), int(r.get("id") or 0)))
        return [_from_row(IncomeRecord, r) for r in rows]

    def get_income_records_by_period(
        self,
        business_id: int | None,
        start_date: str | date,
        end_date: str | date,
    ) -> list[IncomeRecord]:
        def _as_ymd(v: str | date) -> str:
            if isinstance(v, date):
                return v.isoformat()
            return str(v).strip()[:10]

        start = _as_ymd(start_date)
        end = _as_ymd(end_date)
        if start > end:
            start, end = end, start
        records = self.list_income_records(business_id=business_id)
        return [r for r in records if start <= str(r.pay_date)[:10] <= end]
