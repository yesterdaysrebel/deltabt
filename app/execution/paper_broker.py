"""Deterministic paper execution.

THERE IS NO ORDER-PLACEMENT PATH TO THE EXCHANGE HERE OR ANYWHERE ELSE. This
module simulates fills against observed market data. It imports no HTTP client,
no signing code and no credentials, and the exchange adapter it sits behind
exposes market data only.

TWO FILL PATHS, DELIBERATELY DIFFERENT

**Live (``process_market_event``)** consumes ticks in arrival order, so the
sequence of events is *observed* rather than inferred. If a stop and a target
are both reachable during a bar, the tick stream says which happened first. The
tick's microsecond timestamp is recorded on the fill, which is what makes the
ordering auditable afterwards.

**Replay (``process_bar``)** consumes closed OHLC bars, where the ordering of
the high and the low within the bar is unknowable. It resolves pessimistically
-- stop first -- and carries the entry-bar guard described below.

THE SAME-BAR LOOK-AHEAD GUARD

A bug found and fixed during the research program: a passive entry that filled
because the bar's LOW touched the limit was also allowed to claim that same
bar's HIGH as a target hit. Measured at one configuration it produced 356
same-bar target exits against a single same-bar stop -- a 356:1 asymmetry that
cannot occur by chance. The reasoning is simple once stated: if price had to
come down to the resting limit to fill it, the bar's favourable extreme almost
certainly happened BEFORE the fill, so counting it as a target is reading the
past as the future.

``process_bar`` therefore refuses to book a target on the bar a passive order
filled on. ``tests/live/test_paper_execution.py`` contains the regression test.

TRIGGER PRICES
    Stops trigger on MARK price, matching Delta's default, and fill at LTP with
    slippage and taker fees. Targets are resting limit orders, so they fill on
    LTP and pay the maker fee with no slippage. Conflating mark and last-traded
    mistimes every exit, which is why the two are carried separately from the
    socket onward.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from app.execution.intents import ApprovedOrderIntent
from app.persistence.models import new_uid
from deltabt.costs import SymbolCosts

log = logging.getLogger(__name__)

LONG, SHORT = 1, -1


class OrderStatus(str, Enum):
    NEW = "NEW"
    WORKING = "WORKING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    TIME_EXIT = "TIME_EXIT"
    SYSTEM_SAFETY = "SYSTEM_SAFETY"
    DATA_FAILURE = "DATA_FAILURE"


@dataclass
class PaperOrder:
    order_uid: str
    idempotency_key: str
    signal_key: str
    symbol: str
    side: int
    order_type: str
    purpose: str                     # entry | stop | target
    quantity: int
    limit_price: float | None
    status: OrderStatus = OrderStatus.NEW
    created_at: int = 0
    #: True when the fill came from price reaching a resting order rather than
    #: from crossing the spread. Only passive fills carry the same-bar guard.
    passive: bool = False


@dataclass
class PaperFill:
    fill_uid: str
    order_uid: str
    symbol: str
    side: int
    quantity: int
    price: float
    notional: float
    fee: float
    slippage: float
    liquidity: str
    filled_at: int
    tick_ts_us: int | None = None


@dataclass
class PaperPosition:
    position_uid: str
    signal_key: str
    symbol: str
    side: int
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float
    risk_per_unit: float
    initial_risk: float
    notional: float
    equity_before: float
    opened_at: int
    strategy_version: str
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    funding: float = 0.0
    status: str = "OPEN"
    exit_price: float | None = None
    exit_reason: str | None = None
    closed_at: int | None = None
    realized_pnl: float | None = None
    r_multiple: float | None = None
    last_price: float | None = None
    #: Bar on which the entry filled. Used by the same-bar look-ahead guard.
    entry_bar: int | None = None
    entry_was_passive: bool = False
    #: Exchange microsecond timestamp of the entry fill. Stop and target are
    #: only live for ticks strictly after it.
    armed_after_us: int | None = None

    @property
    def is_open(self) -> bool:
        return self.status in ("OPENING", "OPEN", "SUSPENDED", "CLOSING")

    def unrealized(self, price: float, contract_value: float) -> float:
        return self.side * (price - self.entry_price) * self.quantity * contract_value

    def r_at(self, price: float, contract_value: float) -> float:
        if self.initial_risk <= 0:
            return 0.0
        return self.unrealized(price, contract_value) / self.initial_risk


@dataclass
class BrokerEvent:
    kind: str                        # ORDER_CREATED | FILL | POSITION_OPENED | POSITION_CLOSED
    symbol: str
    payload: dict = field(default_factory=dict)


class PaperBroker:
    """Simulated execution. No exchange order API is reachable from here."""

    def __init__(self, costs: dict[str, SymbolCosts], *, starting_equity: float,
                 slippage_bps: float = 2.0) -> None:
        self.costs = costs
        self.equity = starting_equity
        self.slippage_bps = slippage_bps
        self.orders: dict[str, PaperOrder] = {}
        self.positions: dict[str, PaperPosition] = {}
        #: intent_id -> order_uid, so a replayed intent cannot double-fill.
        self._by_intent: dict[str, str] = {}
        self._pending: dict[str, ApprovedOrderIntent] = {}
        self._fills: list[PaperFill] = []
        self.events: list[BrokerEvent] = []

    # -- introspection (brief section 9) -----------------------------------

    def get_open_orders(self, symbol: str | None = None) -> list[PaperOrder]:
        return [o for o in self.orders.values()
                if o.status in (OrderStatus.NEW, OrderStatus.WORKING)
                and (symbol is None or o.symbol == symbol)]

    def get_positions(self, symbol: str | None = None) -> list[PaperPosition]:
        return [p for p in self.positions.values()
                if p.is_open and (symbol is None or p.symbol == symbol)]

    def get_balance(self) -> dict:
        open_pos = self.get_positions()
        unreal = 0.0
        for p in open_pos:
            if p.last_price is not None:
                cv = self.costs[p.symbol].contract_value
                unreal += p.unrealized(p.last_price, cv)
        return {"equity": self.equity, "unrealized": unreal,
                "open_positions": len(open_pos)}

    def cancel_order(self, order_uid: str, reason: str = "cancelled") -> bool:
        o = self.orders.get(order_uid)
        if o is None or o.status not in (OrderStatus.NEW, OrderStatus.WORKING):
            return False
        o.status = OrderStatus.CANCELLED
        log.info("paper order cancelled", extra={"order": order_uid,
                                                 "reason": reason})
        return True

    def modify_order(self, order_uid: str, *, limit_price: float | None = None,
                     quantity: int | None = None) -> bool:
        o = self.orders.get(order_uid)
        if o is None or o.status not in (OrderStatus.NEW, OrderStatus.WORKING):
            return False
        if limit_price is not None:
            o.limit_price = limit_price
        if quantity is not None:
            if quantity <= 0:
                return False
            o.quantity = quantity
        return True

    # -- submission --------------------------------------------------------

    def submit_order(self, intent: ApprovedOrderIntent) -> PaperOrder | None:
        """Accepts a risk-approved intent and nothing else.

        Returns None if this intent was already submitted, which is what makes
        replay after a crash safe.
        """
        if not isinstance(intent, ApprovedOrderIntent):
            raise TypeError(
                "PaperBroker.submit_order accepts only an ApprovedOrderIntent; "
                "a strategy may not create orders directly")
        if intent.intent_id in self._by_intent:
            log.info("intent already submitted; ignoring replay",
                     extra={"intent": intent.intent_id})
            return None
        if any(p.symbol == intent.symbol and p.is_open
               for p in self.positions.values()):
            log.warning("refusing entry: position already open",
                        extra={"symbol": intent.symbol})
            return None

        order = PaperOrder(
            order_uid=new_uid("ord"),
            idempotency_key=intent.intent_id,
            signal_key=intent.signal_key,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            purpose="entry",
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            status=OrderStatus.WORKING,
            created_at=intent.bar_open,
            passive=intent.order_type == "limit",
        )
        self.orders[order.order_uid] = order
        self._by_intent[intent.intent_id] = order.order_uid
        self._pending[order.order_uid] = intent
        self.events.append(BrokerEvent("ORDER_CREATED", intent.symbol, {
            "order_uid": order.order_uid, "intent_id": intent.intent_id,
            "quantity": order.quantity, "side": order.side}))
        return order

    # -- fills -------------------------------------------------------------

    def _slip(self, price: float, side: int) -> float:
        """Adverse slippage in basis points, applied against the taker."""
        return price * (1.0 + side * self.slippage_bps / 10_000.0)

    def _open_from_fill(self, order: PaperOrder, intent: ApprovedOrderIntent,
                        price: float, when: int, tick_us: int | None,
                        bar_open: int | None) -> PaperPosition:
        costs = self.costs[order.symbol]
        notional = costs.notional(order.quantity, price)
        fee = costs.entry_cost(order.quantity, price)
        slip = abs(price - intent.entry_reference) * order.quantity * costs.contract_value

        fill = PaperFill(new_uid("fill"), order.order_uid, order.symbol,
                         order.side, order.quantity, price, notional, fee,
                         slip, "maker" if order.passive else "taker", when,
                         tick_us)
        order.status = OrderStatus.FILLED
        self.equity -= fee

        rpu = (price - intent.stop_price) if order.side == LONG else (intent.stop_price - price)
        pos = PaperPosition(
            position_uid=new_uid("pos"),
            signal_key=intent.signal_key,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            entry_price=price,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            risk_per_unit=max(rpu, 1e-12),
            initial_risk=max(rpu, 1e-12) * order.quantity * costs.contract_value,
            notional=notional,
            equity_before=intent.equity_before,
            opened_at=when,
            strategy_version=intent.strategy_version,
            entry_fee=fee,
            last_price=price,
            entry_bar=bar_open,
            entry_was_passive=order.passive,
            armed_after_us=tick_us,
        )
        self.positions[pos.position_uid] = pos
        self.events.append(BrokerEvent("FILL", order.symbol, {
            "fill_uid": fill.fill_uid, "order_uid": order.order_uid,
            "price": price, "fee": fee, "tick_ts_us": tick_us}))
        self.events.append(BrokerEvent("POSITION_OPENED", order.symbol, {
            "position_uid": pos.position_uid, "entry": price,
            "stop": pos.stop_price, "target": pos.target_price,
            "quantity": pos.quantity}))
        self._fills.append(fill)
        return pos

    def _close(self, pos: PaperPosition, price: float, reason: ExitReason,
               when: int, tick_us: int | None, *, maker: bool) -> None:
        costs = self.costs[pos.symbol]
        fee = costs.exit_cost(pos.quantity, price, maker=maker)
        gross = pos.side * (price - pos.entry_price) * pos.quantity * costs.contract_value
        pnl = gross - pos.entry_fee - fee - pos.funding

        pos.status = "CLOSED"
        pos.exit_price = price
        pos.exit_reason = reason.value
        pos.closed_at = when
        pos.exit_fee = fee
        pos.realized_pnl = pnl
        pos.r_multiple = pnl / pos.initial_risk if pos.initial_risk > 0 else 0.0
        pos.last_price = price
        self.equity += gross - fee

        self._fills.append(PaperFill(
            new_uid("fill"), new_uid("exit"), pos.symbol, -pos.side,
            pos.quantity, price, costs.notional(pos.quantity, price), fee,
            0.0, "maker" if maker else "taker", when, tick_us))
        self.events.append(BrokerEvent("POSITION_CLOSED", pos.symbol, {
            "position_uid": pos.position_uid, "exit": price,
            "reason": reason.value, "pnl": pnl, "r": pos.r_multiple,
            "tick_ts_us": tick_us}))
        log.info("paper position closed", extra={
            "symbol": pos.symbol, "reason": reason.value, "pnl": pnl,
            "r": pos.r_multiple})

    # -- live tick path ----------------------------------------------------

    def process_market_event(self, tick) -> list[BrokerEvent]:
        """Advance execution on one tick. Ordering is observed, not inferred."""
        before = len(self.events)
        sym = tick.symbol
        if sym not in self.costs:
            return []

        # Entries first: a resting entry that fills on this tick becomes a
        # position whose stop/target are only armed from the NEXT tick.
        for order in list(self.get_open_orders(sym)):
            if order.purpose != "entry":
                continue
            intent = self._pending.get(order.order_uid)
            if intent is None:
                continue
            if order.order_type == "market":
                px = self._slip(tick.ltp, order.side)
                self._open_from_fill(order, intent, px, tick.ts, tick.ts_us, None)
            elif order.limit_price is not None:
                touched = (tick.ltp <= order.limit_price if order.side == LONG
                           else tick.ltp >= order.limit_price)
                if touched:
                    self._open_from_fill(order, intent, order.limit_price,
                                         tick.ts, tick.ts_us, None)

        for pos in list(self.get_positions(sym)):
            if pos.status != "OPEN":
                # SUSPENDED: inside a halt. Delta does not trigger stops during
                # maintenance, so neither do we. State is preserved untouched.
                continue
            pos.last_price = tick.ltp
            if pos.armed_after_us is not None and tick.ts_us <= pos.armed_after_us:
                # The stop and target arm strictly AFTER the tick that filled
                # the entry. Otherwise one price observation could open and
                # close a position, which no real order sequence can do.
                continue
            # Stops trigger on MARK, per Delta's default.
            if pos.side == LONG:
                hit_stop = tick.mark <= pos.stop_price
                hit_target = tick.ltp >= pos.target_price
            else:
                hit_stop = tick.mark >= pos.stop_price
                hit_target = tick.ltp <= pos.target_price

            if hit_stop:
                # A single tick cannot be both, since stop and target sit on
                # opposite sides of entry. If both somehow evaluate true, the
                # stop wins.
                px = self._slip(tick.ltp, -pos.side)
                self._close(pos, px, ExitReason.STOP_LOSS, tick.ts,
                            tick.ts_us, maker=False)
            elif hit_target:
                self._close(pos, pos.target_price, ExitReason.TAKE_PROFIT,
                            tick.ts, tick.ts_us, maker=True)

        return self.events[before:]

    # -- replay / bar path -------------------------------------------------

    def process_bar(self, bar) -> list[BrokerEvent]:
        """Advance execution on one CLOSED bar.

        Used for replay and tests. Intra-bar ordering is unknowable here, so
        the stop wins any conflict, and the entry-bar guard applies.
        """
        before = len(self.events)
        sym = bar.symbol
        if sym not in self.costs:
            return []

        for order in list(self.get_open_orders(sym)):
            if order.purpose != "entry":
                continue
            intent = self._pending.get(order.order_uid)
            if intent is None:
                continue
            if order.order_type == "market":
                px = self._slip(bar.open, order.side)
                self._open_from_fill(order, intent, px, bar.start, None, bar.start)
            elif order.limit_price is not None:
                touched = (bar.low <= order.limit_price if order.side == LONG
                           else bar.high >= order.limit_price)
                if touched:
                    self._open_from_fill(order, intent, order.limit_price,
                                         bar.start, None, bar.start)

        for pos in list(self.get_positions(sym)):
            if pos.status != "OPEN":
                continue                      # SUSPENDED: inside a halt
            pos.last_price = bar.close
            if pos.side == LONG:
                hit_stop = bar.low <= pos.stop_price
                hit_target = bar.high >= pos.target_price
            else:
                hit_stop = bar.high >= pos.stop_price
                hit_target = bar.low <= pos.target_price

            # THE GUARD. A passive entry filled on this bar means price had to
            # travel to the resting limit; the bar's favourable extreme almost
            # certainly preceded that. Booking it as a target is look-ahead.
            if pos.entry_bar == bar.start and pos.entry_was_passive:
                hit_target = False

            if hit_stop:
                self._close(pos, pos.stop_price, ExitReason.STOP_LOSS,
                            bar.start, None, maker=False)
            elif hit_target:
                self._close(pos, pos.target_price, ExitReason.TAKE_PROFIT,
                            bar.start, None, maker=True)

        return self.events[before:]

    # -- administrative closes ---------------------------------------------

    def force_close(self, pos: PaperPosition, price: float, reason: ExitReason,
                    when: int) -> None:
        self._close(pos, price, reason, when, None, maker=False)

    def suspend(self, symbol: str) -> int:
        """Mark positions untriggerable during a halt. State is preserved."""
        n = 0
        for p in self.get_positions(symbol):
            if p.status == "OPEN":
                p.status = "SUSPENDED"
                n += 1
        return n

    def resume(self, symbol: str) -> int:
        n = 0
        for p in self.positions.values():
            if p.symbol == symbol and p.status == "SUSPENDED":
                p.status = "OPEN"
                n += 1
        return n

