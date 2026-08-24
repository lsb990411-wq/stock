"""선입선출(FIFO) 실현손익 및 잔고 계산 엔진."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .models import Lot, MatchDetail, Position, SellResult, Trade


def _account_key(trade: Trade) -> int:
    """증권사/계좌 미지정이면 0으로 묶어 기존 단일 풀과 호환."""
    try:
        aid = getattr(trade, "account_id", None)
        return int(aid) if aid is not None else 0
    except (TypeError, ValueError):
        return 0


def sells_by_trade_id(sells: list[SellResult]) -> dict[int, SellResult]:
    """매도 결과를 trade.id 로 조회. 전표·화면 조인 시 int 로 통일."""
    out: dict[int, SellResult] = {}
    for sell in sells:
        try:
            if sell.trade_id is None:
                continue
            out[int(sell.trade_id)] = sell
        except (TypeError, ValueError):
            continue
    return out


def aggregate_positions_by_stock(positions: list[Position]) -> list[Position]:
    """증권사별로 나뉜 잔고를 종목(사업자+stock_id) 단위로 합산."""
    buckets: dict[tuple[int, int], list[Position]] = defaultdict(list)
    for p in positions:
        buckets[(p.business_id, p.stock_id)].append(p)

    merged: list[Position] = []
    for key in sorted(
        buckets.keys(),
        key=lambda k: (
            buckets[k][0].business_name,
            buckets[k][0].stock_name,
            buckets[k][0].stock_code,
        ),
    ):
        group = buckets[key]
        qty = sum(p.quantity for p in group)
        total_cost = sum(p.total_cost for p in group)
        realized = sum(p.realized_pnl for p in group)
        lots: list[Lot] = []
        for p in group:
            lots.extend(deepcopy(p.lots))
        sample = group[0]
        brokers = sorted(
            {
                (p.account_name or "").strip() or "미지정"
                for p in group
                if p.quantity > 1e-12 or abs(p.realized_pnl) > 1e-9
            }
        )
        merged.append(
            Position(
                business_id=sample.business_id,
                business_name=sample.business_name,
                stock_id=sample.stock_id,
                stock_code=sample.stock_code,
                stock_name=sample.stock_name,
                quantity=qty,
                avg_price=(total_cost / qty) if qty > 0 else 0.0,
                total_cost=total_cost,
                realized_pnl=realized,
                lots=lots,
                account_id=None,
                account_name=" · ".join(brokers) if brokers else "합산",
            )
        )
    return merged


class FifoEngine:
    """거래 리스트를 시간순으로 재생하여 FIFO 잔고/실현손익을 계산.

    키 = (business_id, stock_id, account_id) — 동일 종목이어도 증권사별로 독립.
    """

    def __init__(self, trades: list[Trade]) -> None:
        self.trades = sorted(trades, key=lambda t: (t.trade_date, t.id or 0))

    def run(self) -> tuple[list[Position], list[SellResult], list[str]]:
        lots: dict[tuple[int, int, int], list[Lot]] = defaultdict(list)
        sell_results: list[SellResult] = []
        realized_by_key: dict[tuple[int, int, int], float] = defaultdict(float)
        warnings: list[str] = []

        meta: dict[tuple[int, int, int], dict[str, object]] = {}

        for trade in self.trades:
            aid = _account_key(trade)
            key = (trade.business_id, trade.stock_id, aid)
            acct_name = (getattr(trade, "account_name", "") or "").strip() or (
                "미지정" if aid == 0 else ""
            )
            meta[key] = {
                "business_name": trade.business_name,
                "stock_code": trade.stock_code,
                "stock_name": trade.stock_name,
                "account_id": trade.account_id,
                "account_name": acct_name,
            }

            if trade.side == "DIVIDEND":
                continue

            if trade.side == "BUY":
                lots[key].append(
                    Lot(
                        trade_id=trade.id or 0,
                        trade_date=trade.trade_date,
                        business_id=trade.business_id,
                        stock_id=trade.stock_id,
                        original_qty=trade.quantity,
                        remaining_qty=trade.quantity,
                        price=trade.price,
                        fee=trade.fee,
                        stock_code=trade.stock_code,
                        stock_name=trade.stock_name,
                        business_name=trade.business_name,
                        account_id=trade.account_id,
                        account_name=acct_name,
                    )
                )
                continue

            # SELL — 같은 증권사 로트만 소진
            remaining_to_sell = trade.quantity
            matches: list[MatchDetail] = []
            realized = 0.0

            while remaining_to_sell > 1e-12 and lots[key]:
                lot = lots[key][0]
                matched = min(lot.remaining_qty, remaining_to_sell)
                ratio_buy = matched / lot.original_qty if lot.original_qty else 0.0
                buy_fee_alloc = lot.fee * ratio_buy

                ratio_sell = matched / trade.quantity if trade.quantity else 0.0
                sell_fee_alloc = trade.fee * ratio_sell

                pnl = (trade.price - lot.price) * matched - buy_fee_alloc - sell_fee_alloc
                realized += pnl

                matches.append(
                    MatchDetail(
                        buy_trade_id=lot.trade_id,
                        buy_date=lot.trade_date,
                        matched_qty=matched,
                        buy_price=lot.price,
                        buy_fee_allocated=buy_fee_alloc,
                        sell_fee_allocated=sell_fee_alloc,
                        sell_price=trade.price,
                        realized_pnl=pnl,
                    )
                )

                lot.remaining_qty -= matched
                remaining_to_sell -= matched
                if lot.remaining_qty <= 1e-12:
                    lots[key].pop(0)

            shortfall = remaining_to_sell if remaining_to_sell > 1e-12 else 0.0
            if shortfall > 0:
                label = f"{trade.business_name}/{acct_name}/{trade.stock_name}".replace(
                    "//", "/"
                )
                warnings.append(
                    f"[{trade.trade_date}] {label}"
                    f" 매도수량 부족 (부족 {shortfall:.4f}주)"
                )

            realized_by_key[key] += realized
            book_cost = sum(m.matched_qty * m.buy_price for m in matches)
            tax = float(getattr(trade, "tax", 0) or 0)
            if trade.settlement_amount is not None:
                bank_in = float(trade.settlement_amount)
            else:
                bank_in = max(
                    float(trade.quantity) * float(trade.price) - trade.fee - tax, 0.0
                )
            disposal_pnl = bank_in + float(trade.fee) + tax - book_cost
            # DB에 사용자 지정 처분손익이 있으면 우선 사용
            override = getattr(trade, "disposal_pnl", None)
            if override is not None:
                try:
                    disposal_pnl = float(override)
                except (TypeError, ValueError):
                    pass
            sell_results.append(
                SellResult(
                    trade_id=trade.id,
                    trade_date=trade.trade_date,
                    business_id=trade.business_id,
                    stock_id=trade.stock_id,
                    quantity=trade.quantity,
                    price=trade.price,
                    fee=trade.fee,
                    realized_pnl=realized,
                    matches=matches,
                    shortfall_qty=shortfall,
                    book_cost=book_cost,
                    disposal_pnl=disposal_pnl,
                    stock_code=trade.stock_code,
                    stock_name=trade.stock_name,
                    business_name=trade.business_name,
                    account_id=trade.account_id,
                    account_name=acct_name,
                )
            )

        positions: list[Position] = []
        all_keys = set(lots.keys()) | set(realized_by_key.keys()) | set(meta.keys())

        def _sort_key(k: tuple[int, int, int]) -> tuple:
            info = meta.get(k, {})
            return (
                str(info.get("business_name", "")),
                str(info.get("stock_name", "")),
                str(info.get("account_name", "")),
                k[2],
            )

        for key in sorted(all_keys, key=_sort_key):
            active_lots = [
                deepcopy(lot) for lot in lots.get(key, []) if lot.remaining_qty > 1e-12
            ]
            qty = sum(lot.remaining_qty for lot in active_lots)
            total_cost = sum(lot.cost_basis for lot in active_lots)
            avg_price = (total_cost / qty) if qty > 0 else 0.0
            info = meta.get(key, {})
            positions.append(
                Position(
                    business_id=key[0],
                    business_name=str(info.get("business_name", "")),
                    stock_id=key[1],
                    stock_code=str(info.get("stock_code", "")),
                    stock_name=str(info.get("stock_name", "")),
                    quantity=qty,
                    avg_price=avg_price,
                    total_cost=total_cost,
                    realized_pnl=realized_by_key.get(key, 0.0),
                    lots=active_lots,
                    account_id=info.get("account_id"),  # type: ignore[arg-type]
                    account_name=str(info.get("account_name", "")),
                )
            )
        return positions, sell_results, warnings


def compute_positions(trades: list[Trade]) -> tuple[list[Position], list[SellResult], list[str]]:
    return FifoEngine(trades).run()


def buy_lot_remainders(trades: list[Trade]) -> dict[int, dict[str, float]]:
    """매수 건별 FIFO 잔여수량·매수단가(원화/외화).

    Returns:
        {buy_trade_id: {"original_qty", "remaining_qty", "buy_price", "buy_price_fx"}}
        전량 소진된 매수도 remaining_qty=0 으로 포함.
    """
    lots: dict[tuple[int, int, int], list[dict[str, float | int]]] = defaultdict(list)
    remainders: dict[int, dict[str, float]] = {}

    for trade in sorted(trades, key=lambda t: (t.trade_date, t.id or 0)):
        if trade.side == "DIVIDEND":
            continue
        aid = _account_key(trade)
        key = (trade.business_id, trade.stock_id, aid)

        if trade.side == "BUY":
            tid = int(trade.id or 0)
            if tid <= 0:
                continue
            price_fx = float(getattr(trade, "price_fx", 0) or 0)
            entry = {
                "trade_id": tid,
                "original_qty": float(trade.quantity),
                "remaining_qty": float(trade.quantity),
                "buy_price": float(trade.price),
                "buy_price_fx": price_fx,
            }
            lots[key].append(entry)
            remainders[tid] = {
                "original_qty": float(trade.quantity),
                "remaining_qty": float(trade.quantity),
                "buy_price": float(trade.price),
                "buy_price_fx": price_fx,
            }
            continue

        # SELL
        remaining_to_sell = float(trade.quantity)
        while remaining_to_sell > 1e-12 and lots[key]:
            lot = lots[key][0]
            matched = min(float(lot["remaining_qty"]), remaining_to_sell)
            lot["remaining_qty"] = float(lot["remaining_qty"]) - matched
            remaining_to_sell -= matched
            tid = int(lot["trade_id"])
            remainders[tid] = {
                "original_qty": float(lot["original_qty"]),
                "remaining_qty": max(0.0, float(lot["remaining_qty"])),
                "buy_price": float(lot["buy_price"]),
                "buy_price_fx": float(lot.get("buy_price_fx") or 0),
            }
            if float(lot["remaining_qty"]) <= 1e-12:
                lot["remaining_qty"] = 0.0
                remainders[tid]["remaining_qty"] = 0.0
                lots[key].pop(0)

    return remainders
