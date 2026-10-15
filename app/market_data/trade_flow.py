"""Recent order flow per coin, from Delta's public `all_trades` feed (2026-10-15).

The owner asked for order flow next to the order book at every signal. The bot
was already subscribed to `all_trades` (app/market_data/delta_ws.py) and ignored
it; this keeps the last WINDOW seconds of prints per symbol so the signal
recorder can snapshot who was aggressing at the moment of a decision.

A MEASUREMENT ONLY. Nothing on the trading path reads it, and every method is
safe to call with bad input: a malformed print is dropped and counted.

Message format, observed live 2026-10-15:
    {"type": "all_trades", "symbol": "BTCUSD", "price": "82300.0", "size": 17,
     "buyer_role": "maker", "seller_role": "taker", "timestamp": <microseconds>}
    {"type": "all_trades_snapshot", "symbol": ..., "trades": [<same, newest first>]}
`size` is in contracts; USD = size x contract_value x price. The TAKER is the
aggressor: buyer_role "taker" is market buying, seller_role "taker" selling.

No history exists for this (Delta's public trades endpoint returns the last 50
only), so these readings are LIVE-ONLY: recorded at every signal, but usable by
a model only once enough live signals carry them.
"""

from __future__ import annotations

from collections import deque
from typing import Mapping

WINDOW = 15 * 60
SPANS = (60, 300, 900)


class TradeFlow:
    def __init__(self, contract_values: Mapping[str, float] | None = None) -> None:
        self.contract_values = dict(contract_values or {})
        self._prints: dict[str, deque] = {}
        self._seen_ts: dict[str, int] = {}
        self.dropped = 0

    def on_message(self, msg: dict) -> None:
        """Feed one websocket message. Never raises."""
        try:
            t = msg.get("type")
            if t == "all_trades":
                self._add(msg.get("symbol"), msg)
            elif t == "all_trades_snapshot":
                sym = msg.get("symbol")
                for tr in reversed(list(msg.get("trades") or [])):   # oldest first
                    self._add(sym, tr)
        except Exception:                                   # noqa: BLE001
            self.dropped += 1

    def _add(self, symbol, tr: dict) -> None:
        try:
            ts = int(tr["timestamp"]) // 1_000_000
            price, size = float(tr["price"]), float(tr["size"])
            buyer_taker = tr.get("buyer_role") == "taker"
            seller_taker = tr.get("seller_role") == "taker"
        except (KeyError, TypeError, ValueError):
            self.dropped += 1
            return
        if not symbol or price <= 0 or size <= 0 or buyer_taker == seller_taker:
            self.dropped += 1
            return
        q = self._prints.setdefault(symbol, deque())
        # A reconnect re-sends a snapshot of prints we may already hold; keep
        # only prints newer than the newest one seen (seconds resolution, so
        # a same-second duplicate is still possible -- a small overcount).
        if ts < self._seen_ts.get(symbol, 0):
            return
        self._seen_ts[symbol] = ts
        usd = size * float(self.contract_values.get(symbol, 1.0)) * price
        q.append((ts, usd, 1 if buyer_taker else -1))
        while q and q[0][0] < ts - WINDOW:
            q.popleft()

    def snapshot(self, symbol: str, at: int, side: int) -> dict:
        """Order-flow readings over the last 1, 5 and 15 minutes before `at`.

        `imbalance_*` is (taker buys - taker sells) / total, in [-1, 1], and
        `flow_with_trade_*` the same multiplied by the trade's side, so "+"
        always means the crowd was pushing in the trade's direction.
        """
        q = list(self._prints.get(symbol, ()))
        out: dict = {"prints_seen": len(q)}
        for span in SPANS:
            lab = f"{span // 60}m"
            buys = sells = n = 0.0
            for ts, usd, s in q:
                if at - span <= ts <= at:
                    n += 1
                    if s > 0:
                        buys += usd
                    else:
                        sells += usd
            tot = buys + sells
            imb = (buys - sells) / tot if tot > 0 else None
            out.update({f"buy_usd_{lab}": round(buys, 2), f"sell_usd_{lab}": round(sells, 2),
                        f"trades_{lab}": int(n), f"imbalance_{lab}": imb,
                        f"flow_with_trade_{lab}": imb * side if imb is not None else None})
        big = [usd for ts, usd, _ in q if at - WINDOW <= ts <= at]
        out["largest_trade_usd_15m"] = round(max(big), 2) if big else None
        return out
