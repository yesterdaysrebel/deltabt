"""Venue-backed execution: the same surface as PaperBroker, different physics.

THE DIFFERENCE THAT IS NOT AN IMPLEMENTATION DETAIL

PaperBroker COMPUTES fills. `process_market_event(tick)` looks at a price,
decides the stop was hit, and books the exit -- synchronously, deterministically,
inside the call. Every position it holds is one it created itself, so its memory
cannot be wrong.

A venue TELLS you about fills, whenever it gets round to it, possibly while the
process was dead. Nothing here may decide that a stop was hit: the exchange
decides, and this class finds out afterwards by asking.

So the two classes share a surface and not a shape:

    PaperBroker.process_market_event(tick) -> [FILL, POSITION_CLOSED, ...]
    LiveBroker.process_market_event(tick)  -> []        # always. see below.
    LiveBroker.poll(now)                   -> [FILL, POSITION_CLOSED, ...]

`process_market_event` is deliberately inert rather than absent, so the runtime
can drive either without branching. Making it guess at fills from a tick would
manufacture a position the venue does not hold, which is the one failure
reconciliation exists to catch and the one it cannot fix.

WHAT THE VENUE MANAGES, AND WHY THAT IS THE POINT

Stop and target go to the exchange as bracket legs on the entry. They then work
whether or not this process is alive -- which is the whole argument for them:
on 2026-09-13 the paper bot was blind for 90 seconds during an RDS maintenance
reboot, and a bot-managed stop is not a stop during those 90 seconds.

The stop leg carries a marketable-limit cap (live/orders.py STOP_LIMIT_CAP_R)
because Delta offers no slippage protection on market orders.

STATE IS A CACHE, NOT A RECORD

`self.positions` mirrors the venue as of the last poll. It is never the
authority. `live/reconcile.py` is what compares it to the database, and it
halts rather than repairing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from live.client import AmbiguousWrite, LiveClient, VenueError
from live.guards import KILL_SWITCH_PATH, kill_switch_engaged
from live.orders import (STOP_LIMIT_CAP_R, OrderRequest, OrderType, Side,
                         StopTriggerMethod, TimeInForce, client_order_id)

log = logging.getLogger(__name__)


@dataclass
class BrokerEvent:
    """Mirrors app.execution.paper_broker.BrokerEvent.

    Duplicated rather than imported to keep the import direction one-way:
    `live` may read `app`, but nothing here should make `app` grow a reason to
    import `live`. The kinds are identical so a runtime can consume either.
    """

    kind: str          # ORDER_CREATED | FILL | POSITION_OPENED | POSITION_CLOSED
    symbol: str
    payload: dict = field(default_factory=dict)


@dataclass
class LivePosition:
    """What the venue says we hold, as of the last poll."""

    symbol: str
    side: int
    contracts: int
    entry_price: float
    product_id: int
    stop_price: float = 0.0
    target_price: float = 0.0
    position_uid: str = ""
    intent_id: str = ""

    def is_open(self) -> bool:
        return self.contracts > 0


class LiveBroker:
    """Places real orders. Learns their outcome by asking the venue."""

    def __init__(self, client: LiveClient, *, product_ids: dict[str, int],
                 experiment_id: str, tick_size: dict[str, Decimal] | None = None,
                 entry_ttl_seconds: int = 90,
                 kill_switch_path: str | None = None) -> None:
        self.client = client
        self.product_ids = dict(product_ids)
        self.experiment_id = experiment_id
        self.tick_size = dict(tick_size or {})
        self.entry_ttl_seconds = entry_ttl_seconds
        self.kill_switch_path = kill_switch_path
        #: symbol -> LivePosition, refreshed by poll(). A CACHE.
        self.positions: dict[str, LivePosition] = {}
        #: client_order_id -> the intent that produced it, for attribution.
        self._pending: dict[str, dict] = {}
        self._suspended: set[str] = set()
        self._entry_cid: dict[str, str] = {}
        self._symbol_for = {v: k for k, v in self.product_ids.items()}

    # -- the inert half ------------------------------------------------------

    def process_market_event(self, tick) -> list[BrokerEvent]:
        """Always empty. The venue decides fills; see the module docstring."""
        return []

    def process_bar(self, bar) -> list[BrokerEvent]:
        return []

    def suspend(self, symbol: str) -> int:
        """Stop opening on a symbol whose feed is untrustworthy.

        Does NOT touch open positions: their brackets are at the exchange and
        remain the best protection available precisely when our data is bad.
        """
        self._suspended.add(symbol)
        return len(self._suspended)

    def resume(self, symbol: str) -> bool:
        """True when this call is what un-suspended it."""
        was = symbol in self._suspended
        self._suspended.discard(symbol)
        return was

    # -- placing -------------------------------------------------------------

    def _round(self, symbol: str, price: float) -> Decimal:
        tick = self.tick_size.get(symbol)
        d = Decimal(str(price))
        if not tick or tick <= 0:
            return d
        return (d / tick).to_integral_value() * tick

    def submit_order(self, intent, *, now: int | None = None) -> dict:
        """Send one risk-approved intent to the venue, with its brackets.

        Returns the venue's order row. Raises rather than returning a partial
        truth: an intent whose outcome is unknown must reach the caller as an
        exception, because the correct response is to stop and reconcile.
        """
        # THE LAST GATE BEFORE AN ORDER LEAVES. Checked per order rather than
        # at start-up, because the point of a kill switch is the trade that
        # has not happened yet. It stops OPENING only: positions already open
        # keep their exchange-held brackets, which is the protection that
        # matters once something has gone wrong enough to reach for this.
        if kill_switch_engaged(self.kill_switch_path):
            raise VenueError(
                f"kill switch engaged ({self.kill_switch_path or KILL_SWITCH_PATH}); "
                f"not opening {intent.symbol}")
        if intent.symbol in self._suspended:
            raise VenueError(f"{intent.symbol} is suspended; not opening")
        pid = self.product_ids.get(intent.symbol)
        if pid is None:
            raise VenueError(f"no product_id known for {intent.symbol}")

        side = Side.for_position(intent.side)
        stop = self._round(intent.symbol, intent.stop_price)
        target = self._round(intent.symbol, intent.target_price)
        # The cap sits BEYOND the stop, in the direction the loss runs: a long
        # exits by selling, so its cap is below the stop.
        cap = self._round(
            intent.symbol,
            float(Decimal(str(intent.stop_price))
                  - Decimal(str(intent.side)) * STOP_LIMIT_CAP_R
                  * Decimal(str(intent.risk_per_unit))))

        cid = client_order_id(self.experiment_id, intent.intent_id, "entry")
        order = OrderRequest(
            product_id=pid,
            size=int(intent.quantity),
            side=side,
            order_type=(OrderType.MARKET if intent.order_type == "market"
                        else OrderType.LIMIT),
            client_order_id=cid,
            limit_price=(self._round(intent.symbol, intent.limit_price)
                         if intent.order_type == "limit" else None),
            # IOC on a market entry: a market order that cannot fill now should
            # not linger as a resting one at an unknown price.
            time_in_force=(TimeInForce.IOC if intent.order_type == "market"
                           else TimeInForce.GTC),
            bracket_stop_loss_price=stop,
            bracket_stop_loss_limit_price=cap,
            bracket_take_profit_price=target,
            stop_trigger_method=StopTriggerMethod.MARK,
            note=intent.signal_key)

        # SYMBOL -> the id of the order that opened it. poll() reports a
        # position by SYMBOL (the venue does not know our uids), so this is the
        # link back to what we intended when the fill is finally seen.
        self._entry_cid[intent.symbol] = cid
        self._pending[cid] = {
            "intent_id": intent.intent_id,
            "signal_key": intent.signal_key,
            "symbol": intent.symbol,
            "side": intent.side,
            "stop_price": float(stop),
            "target_price": float(target),
            "stop_cap_price": float(cap),
            "risk_per_unit": intent.risk_per_unit,
        }
        log.info("submitting %s %s x%d %s (stop %s cap %s target %s)",
                 intent.symbol, side.value, intent.quantity, intent.order_type,
                 stop, cap, target)
        return self.client.place_order(order)

    def close_position(self, symbol: str, reason: str) -> dict:
        """Flatten one position at market, reduce-only.

        reduce_only is not decoration: without it, a size that disagrees with
        the venue's by one contract OPENS an opposite position instead of
        closing. That is the failure mode of every hand-rolled flatten.
        """
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open():
            raise VenueError(f"no open position in {symbol} to close")
        cid = client_order_id(self.experiment_id, pos.position_uid or symbol,
                              f"exit:{reason}")
        order = OrderRequest(
            product_id=pos.product_id,
            size=pos.contracts,
            side=Side.for_position(pos.side).opposite(),
            order_type=OrderType.MARKET,
            client_order_id=cid,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
            note=reason)
        log.warning("closing %s at market: %s", symbol, reason)
        return self.client.place_order(order)

    def expire_stale_entries(self, now: int) -> list[BrokerEvent]:
        """Cancel resting entry orders older than the TTL, at the venue."""
        events: list[BrokerEvent] = []
        for row in self.client.get_open_orders():
            created = row.get("created_at") or 0
            try:
                age = now - int(str(created)[:10])
            except (TypeError, ValueError):
                continue
            if age < self.entry_ttl_seconds:
                continue
            if row.get("reduce_only"):
                continue     # never cancel an exit for being old
            sym = self._symbol_for.get(row.get("product_id"), "?")
            try:
                self.client.cancel_order(int(row["id"]), int(row["product_id"]))
            except VenueError as exc:
                log.error("could not cancel stale entry %s: %s", row.get("id"), exc)
                continue
            events.append(BrokerEvent("ORDER_CANCELLED", sym,
                                      {"order_id": row.get("id"), "age": age}))
        return events

    # -- learning what happened ---------------------------------------------

    def poll(self, now: int | None = None) -> list[BrokerEvent]:
        """Ask the venue what it did, and turn the difference into events.

        This is the live equivalent of PaperBroker's fill simulation, and the
        ONLY place positions change. It is a diff against the cache, so a
        missed poll costs latency and not correctness -- the next one sees the
        same difference.
        """
        rows = self.client.get_positions()
        seen: dict[str, LivePosition] = {}
        for row in rows:
            size = int(round(float(row.get("size") or 0)))
            if size == 0:
                continue
            pid = int(row.get("product_id") or 0)
            sym = row.get("product_symbol") or self._symbol_for.get(pid, f"pid={pid}")
            side = -1 if (size < 0 or str(row.get("side", "")).lower() == "sell") else 1
            seen[sym] = LivePosition(
                symbol=sym, side=side, contracts=abs(size),
                entry_price=float(row.get("entry_price") or 0.0),
                product_id=pid,
                position_uid=str(row.get("position_uid") or row.get("id") or ""))

        events: list[BrokerEvent] = []
        for sym, pos in seen.items():
            was = self.positions.get(sym)
            if was is None:
                events.append(BrokerEvent("POSITION_OPENED", sym, {
                    "side": pos.side, "contracts": pos.contracts,
                    "entry_price": pos.entry_price}))
            elif was.contracts != pos.contracts:
                events.append(BrokerEvent("FILL", sym, {
                    "delta": pos.contracts - was.contracts,
                    "contracts": pos.contracts, "partial": True}))
        for sym, was in self.positions.items():
            if sym not in seen:
                # The venue closed it -- a bracket leg fired, or a liquidation.
                # We learn the fact here and the PRICE from the fill history,
                # which the runtime records; this event is the trigger.
                events.append(BrokerEvent("POSITION_CLOSED", sym, {
                    "side": was.side, "contracts": was.contracts,
                    "closed_by": "venue"}))

        self.positions = seen
        return events

    def intent_for(self, symbol: str) -> tuple[str | None, dict | None]:
        """The (client_order_id, intent facts) that opened `symbol`, if known.

        Returns (None, None) after a restart: `_pending` is in memory, so a
        position opened by a previous process cannot be attributed from here.
        The caller must treat that as a fact to record, not a reason to guess
        -- an unattributed position is exactly what reconciliation refuses to
        start on, and inventing an intent would hide it.
        """
        cid = self._entry_cid.get(symbol)
        return cid, (self._pending.get(cid) if cid else None)

    # -- reads ---------------------------------------------------------------

    def get_positions(self, symbol: str | None = None) -> list[LivePosition]:
        if symbol is not None:
            pos = self.positions.get(symbol)
            return [pos] if pos and pos.is_open() else []
        return [p for p in self.positions.values() if p.is_open()]

    def get_balance(self) -> dict:
        for row in self.client.get_balance():
            if str(row.get("asset_symbol", "")).upper() in {"USD", "USDT"}:
                return row
        return {}

    @property
    def equity(self) -> float:
        bal = self.get_balance()
        return float(bal.get("balance") or 0.0)
