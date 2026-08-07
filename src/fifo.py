"""선입선출(FIFO) 실현손익 및 잔고 계산 엔진."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from .models import Lot, MatchDetail, Position, SellResult, Trade


class FifoEngine:
    """거래 리스트를 시간순으로 재생하여 FIFO 잔고/실현손익을 계산."""

    def __init__(self, trades: list[Trade]) -> None:
        self.trades = sorted(trades, key=lambda t: (t.trade_date, t.id or 0))

    def run(self) -> tuple[list[Position], list[SellResult], list[str]]:
        lots: dict[tuple[int, int], list[Lot]] = defaultdict(list)
        sell_results: list[SellResult] = []
        realized_by_key: dict[tuple[int, int], float] = defaultdict(float)
        warnings: list[str] = []

        meta: dict[tuple[int, int], dict[str, str]] = {}

        for trade in self.trades:
            key = (trade.business_id, trade.stock_id)
            meta[key] = {
                "business_name": trade.business_name,
                "stock_code": trade.stock_code,
                "stock_name": trade.stock_name,
            }

            if trade.side == "DIVIDEND":
                # 외화배당은 FIFO 수량에 영향 없음
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
                    )
                )
                continue

            # SELL
            remaining_to_sell = trade.quantity
            matches: list[MatchDetail] = []
            realized = 0.0
            sell_fee_remaining = trade.fee

            while remaining_to_sell > 1e-12 and lots[key]:
                lot = lots[key][0]
                matched = min(lot.remaining_qty, remaining_to_sell)
                ratio_buy = matched / lot.original_qty if lot.original_qty else 0.0
                buy_fee_alloc = lot.fee * ratio_buy

                ratio_sell = matched / trade.quantity if trade.quantity else 0.0
                sell_fee_alloc = trade.fee * ratio_sell
                sell_fee_remaining -= sell_fee_alloc

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
                warnings.append(
                    f"[{trade.trade_date}] {trade.business_name}/{trade.stock_name}"
                    f" 매도수량 부족 (부족 {shortfall:.4f}주)"
                )

            realized_by_key[key] += realized
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
                    stock_code=trade.stock_code,
                    stock_name=trade.stock_name,
                    business_name=trade.business_name,
                )
            )

        positions: list[Position] = []
        all_keys = set(lots.keys()) | set(realized_by_key.keys()) | set(meta.keys())
        for key in sorted(all_keys, key=lambda k: (meta.get(k, {}).get("business_name", ""), meta.get(k, {}).get("stock_name", ""))):
            active_lots = [deepcopy(lot) for lot in lots.get(key, []) if lot.remaining_qty > 1e-12]
            qty = sum(lot.remaining_qty for lot in active_lots)
            # 원가잔액: 잔여 로트의 매수대금 합 (수량×단가, 수수료 제외)
            total_cost = sum(lot.cost_basis for lot in active_lots)
            avg_price = (total_cost / qty) if qty > 0 else 0.0
            info = meta.get(key, {})
            positions.append(
                Position(
                    business_id=key[0],
                    business_name=info.get("business_name", ""),
                    stock_id=key[1],
                    stock_code=info.get("stock_code", ""),
                    stock_name=info.get("stock_name", ""),
                    quantity=qty,
                    avg_price=avg_price,
                    total_cost=total_cost,
                    realized_pnl=realized_by_key.get(key, 0.0),
                    lots=active_lots,
                )
            )
        return positions, sell_results, warnings


def compute_positions(trades: list[Trade]) -> tuple[list[Position], list[SellResult], list[str]]:
    return FifoEngine(trades).run()
