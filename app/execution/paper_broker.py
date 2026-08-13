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

from app.execution.allocation import aggregate, fill_uid
from app.execution.intents import ApprovedOrderIntent
from app.execution.order_state import (
    IllegalTransition,
    OrderStatus,
    is_terminal,
    transition,
)
from app.persistence.models import new_uid
from app.portfolio.funding import settlements_for_position
from deltabt.costs import SymbolCosts

log = logging.getLogger(__name__)

LONG, SHORT = 1, -1


def realised_rr(entry: float, stop: float, target: float, side: int) -> float:
    """Reward/risk implied by an ACTUAL fill price.

    Distinct from the planned figure the risk engine approved: the stop and
    target are fixed when the signal fires, so moving the entry changes both
    legs at once -- adversely, it widens the risk and narrows the reward.
    """
    risk = (entry - stop) if side == LONG else (stop - entry)
    reward = (target - entry) if side == LONG else (entry - target)
    if risk <= 0:
        return 0.0
    return reward / risk


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
    #: The price the decision was made against, kept next to what actually
    #: filled so slippage is a recorded fact rather than a later inference.
    requested_price: float | None = None
    filled_price: float | None = None
    filled_at: int | None = None
    reject_reason: str | None = None
    #: Set once the order fills and its position exists.
    position_uid: str | None = None
    #: True when the fill came from price reaching a resting order rather than
    #: from crossing the spread. Only passive fills carry the same-bar guard.
    passive: bool = False


@dataclass
class PaperFill:
    fill_uid: str
    order_uid: str
    #: Which position this fill belongs to. Carried explicitly so the
    #: persistence layer never has to guess (audit F1).
    position_uid: str
    seq: int
    purpose: str                     # entry | exit
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
    #: Reward/risk implied by the price actually filled, which is always at or
    #: below the planned figure. Recorded so the forward test measures the
    #: degradation instead of assuming it away.
    fill_rr: float | None = None
    #: Exchange time through which funding settlements have been charged.
    #: Advanced only once the events are durable, so a restart mid-settlement
    #: redoes the work and the deterministic event id makes the redo a no-op.
    funding_checked_through: int = 0

    @property
    def is_open(self) -> bool:
        return self.status in ("OPENING", "OPEN", "SUSPENDED", "CLOSING")

    def unrealized(self, price: float, contract_value: float) -> float:
        return self.side * (price - self.entry_price) * self.quantity * contract_value

    def r_at(self, price: float, contract_value: float) -> float:
        if self.initial_risk <= 0:
            return 0.0
        return self.unrealized(price, contract_value) / self.initial_risk


def _order_payload(o: "PaperOrder") -> dict:
    """Everything an order row needs, so the bot reconstructs nothing."""
    return {
        "order_uid": o.order_uid, "idempotency_key": o.idempotency_key,
        "signal_key": o.signal_key, "position_uid": o.position_uid,
        "symbol": o.symbol, "side": o.side, "order_type": o.order_type,
        "purpose": o.purpose, "quantity": o.quantity,
        "limit_price": o.limit_price, "requested_price": o.requested_price,
        "filled_price": o.filled_price, "status": o.status.value,
        "created_exchange_ts": o.created_at, "filled_exchange_ts": o.filled_at,
        "reject_reason": o.reject_reason,
    }


def _fill_payload(f: "PaperFill") -> dict:
    """Everything the persistence layer needs, so it reconstructs nothing."""
    return {
        "fill_uid": f.fill_uid, "order_uid": f.order_uid,
        "position_uid": f.position_uid, "seq": f.seq, "purpose": f.purpose,
        "symbol": f.symbol, "side": f.side, "quantity": f.quantity,
        "price": f.price, "notional": f.notional, "fee": f.fee,
        "slippage": f.slippage, "liquidity": f.liquidity,
        "filled_at": f.filled_at, "tick_ts_us": f.tick_ts_us,
    }


@dataclass
class BrokerEvent:
    kind: str                        # ORDER_CREATED | FILL | POSITION_OPENED | POSITION_CLOSED
    symbol: str
    payload: dict = field(default_factory=dict)


class PaperBroker:
    """Simulated execution. No exchange order API is reachable from here."""

    def __init__(self, costs: dict[str, SymbolCosts], *, starting_equity: float,
                 slippage_bps: float = 2.0, entry_ttl_seconds: int = 90,
                 max_entry_deviation: float = 0.25,
                 min_fill_rr: float = 1.7) -> None:
        self.costs = costs
        self.equity = starting_equity
        self.slippage_bps = slippage_bps
        #: An unfilled entry order dies after this long. A setup is a statement
        #: about the bar that produced it; an order still resting minutes later
        #: would fill at a price the risk engine never sized against. Without
        #: this, a feed that stops delivering ticks silently accumulates
        #: working orders that all fill at once when it resumes.
        self.entry_ttl_seconds = entry_ttl_seconds
        #: Refuse a market entry that has run away from the reference the stop
        #: and size were computed against, expressed as a fraction of the STOP
        #: DISTANCE rather than of price.
        #:
        #: Calibrating on price was wrong and measured so: on a real BTCUSD
        #: short the entry slipped 12.4 points -- 0.019% of price, comfortably
        #: inside a 0.15% price band -- but the stop was only 143 points away,
        #: so that slip widened realised risk by 8.7% and put a $50 budget at
        #: $54.35. What matters is the move relative to R, not to price.
        self.max_entry_deviation = max_entry_deviation
        #: Reward/risk floor applied to the ACTUAL fill.
        #:
        #: `minimum_rr` in the risk engine is a signal-time gate on planned
        #: geometry. Once the entry slips, the stop widens and the target
        #: narrows, so realised RR is always below the planned figure -- on a
        #: 2R plan, ANY adverse slip breaks a 2.0 floor exactly, so enforcing
        #: the planned number at fill time would reject essentially every
        #: trade. Measured on real data the degradation was 2.0 -> 1.75.
        #:
        #: So the degradation is bounded and made explicit rather than
        #: pretended away: a fill whose real reward/risk is below this is not
        #: taken, and the realised figure is recorded on the position.
        #:
        #: 1.7 against a 2.0 plan allows at most 15% degradation, which is
        #: about 0.06R of adverse entry slippage. Note this is the gate that
        #: actually enforces reward/risk discipline: the risk engine's
        #: `minimum_rr` compares the PLANNED geometry against itself, since the
        #: strategy sets target = entry + 2R, so it passes at exactly the
        #: boundary every time and only binds if the two configs disagree.
        self.min_fill_rr = min_fill_rr
        self.orders: dict[str, PaperOrder] = {}
        self.positions: dict[str, PaperPosition] = {}
        #: intent_id -> order_uid, so a replayed intent cannot double-fill.
        self._by_intent: dict[str, str] = {}
        self._pending: dict[str, ApprovedOrderIntent] = {}
        self._fills: list[PaperFill] = []
        #: order_uid -> fills booked so far, so the next sequence number is
        #: deterministic and a replay reuses it rather than inventing a new id.
        self._fill_seq: dict[str, int] = {}
        #: Funding settlements already applied in memory. Makes the in-memory
        #: effect idempotent at source, so persistence never has to "undo" a
        #: charge -- an undo is wrong whenever the event being replayed is a
        #: stale one the broker did not just apply.
        self._funding_charged: set[str] = set()
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

    def set_status(self, order: PaperOrder, requested: OrderStatus,
                   *, reason: str | None = None) -> bool:
        """Move an order, refusing anything the state machine forbids.

        A terminal order drifting back to active is how a cancelled order
        acquires a fill; the dataset would then contain a trade that never
        happened. Re-applying the current state is a no-op, so a duplicate
        event is idempotent rather than fatal.
        """
        try:
            order.status = transition(order.order_uid, order.status, requested)
        except IllegalTransition as exc:
            log.error("refused an illegal order transition: %s", exc)
            return False
        if reason:
            order.reject_reason = reason
        return True

    def cancel_order(self, order_uid: str, reason: str = "cancelled") -> bool:
        o = self.orders.get(order_uid)
        if o is None or is_terminal(o.status):
            return False
        if not self.set_status(o, OrderStatus.CANCELLED, reason=reason):
            return False
        self._pending.pop(order_uid, None)
        log.info("paper order cancelled", extra={"order": order_uid,
                                                 "reason": reason})
        return True

    def modify_order(self, order_uid: str, *, limit_price: float | None = None,
                     quantity: int | None = None) -> bool:
        o = self.orders.get(order_uid)
        if o is None or is_terminal(o.status):
            return False
        if limit_price is not None:
            o.limit_price = limit_price
        if quantity is not None:
            if quantity <= 0:
                return False
            o.quantity = quantity
        return True

    # -- submission --------------------------------------------------------

    def submit_order(self, intent: ApprovedOrderIntent,
                     *, now: int | None = None) -> PaperOrder | None:
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
            created_at=now if now is not None else intent.bar_open,
            requested_price=intent.entry_reference,
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

    def _entry_blocked(self, order: PaperOrder, intent: ApprovedOrderIntent,
                       price: float, now: int) -> str | None:
        """Reason this entry must not fill, or None."""
        if self.entry_ttl_seconds and (now - order.created_at) > self.entry_ttl_seconds:
            return (f"entry order expired after {now - order.created_at}s "
                    f"(ttl {self.entry_ttl_seconds}s)")
        if order.order_type == "market" and self.max_entry_deviation:
            r = intent.risk_per_unit
            dev = abs(price - intent.entry_reference) / r if r > 0 else float("inf")
            if dev > self.max_entry_deviation:
                return (f"price moved {dev:.2f}R from the "
                        f"{intent.entry_reference} reference "
                        f"(limit {self.max_entry_deviation:.2f}R) -- "
                        f"refusing to chase")
        if self.min_fill_rr:
            rr = realised_rr(price, intent.stop_price, intent.target_price,
                             order.side)
            if rr < self.min_fill_rr:
                return (f"reward/risk at the actual fill is {rr:.2f}, below the "
                        f"{self.min_fill_rr:.2f} floor (planned "
                        f"{abs(intent.target_price - intent.entry_reference) / intent.risk_per_unit:.2f})")
        return None

    def _kill_entry(self, order: PaperOrder, reason: str, expired: bool) -> None:
        self.set_status(order,
                        OrderStatus.EXPIRED if expired else OrderStatus.CANCELLED,
                        reason=reason)
        self._pending.pop(order.order_uid, None)
        self.events.append(BrokerEvent(
            "ORDER_EXPIRED" if expired else "ORDER_CANCELLED", order.symbol,
            {"order_uid": order.order_uid, "reason": reason}))
        log.warning("entry not taken", extra={"symbol": order.symbol,
                                              "reason": reason})

    def next_fill_seq(self, order_uid: str) -> int:
        return self._fill_seq.get(order_uid, 0) + 1

    def _book_fill(self, order: PaperOrder, position_uid: str, purpose: str,
                   *, price: float, quantity: int, notional: float, fee: float,
                   slippage: float, liquidity: str, when: int,
                   tick_us: int | None) -> PaperFill:
        """Record one fill with a deterministic identity."""
        seq = self.next_fill_seq(order.order_uid)
        self._fill_seq[order.order_uid] = seq
        f = PaperFill(
            fill_uid=fill_uid(order.order_uid, seq), order_uid=order.order_uid,
            position_uid=position_uid, seq=seq, purpose=purpose,
            symbol=order.symbol, side=order.side, quantity=quantity,
            price=price, notional=notional, fee=fee, slippage=slippage,
            liquidity=liquidity, filled_at=when, tick_ts_us=tick_us)
        self._fills.append(f)
        return f

    def fills_for_position(self, position_uid: str,
                           purpose: str | None = None) -> list[PaperFill]:
        return [f for f in self._fills
                if f.position_uid == position_uid
                and (purpose is None or f.purpose == purpose)]

    def allocation_for_position(self, position_uid: str, purpose: str = "entry"):
        """Aggregate of a position's fills -- weighted average, dedup by uid."""
        return aggregate(self.fills_for_position(position_uid, purpose))

    def settle_funding(self, symbol: str, now: int, *, rate_percent: float,
                       mark_price: float, interval: int) -> list[BrokerEvent]:
        """Charge every settlement crossed since each position was last checked.

        Snapshot semantics, exactly as the research model: whatever is open at
        the instant pays the full interval. Called from the tick path so a
        settlement is charged as soon as market time passes it, not at close.
        """
        before = len(self.events)
        for pos in self.get_positions(symbol):
            if pos.status not in ("OPEN", "SUSPENDED"):
                continue
            costs = self.costs[pos.symbol]
            for s in settlements_for_position(
                    position_uid=pos.position_uid, symbol=pos.symbol,
                    side=pos.side, quantity=pos.quantity,
                    contract_value=costs.contract_value,
                    opened_at=pos.opened_at,
                    checked_through=pos.funding_checked_through,
                    now=now, interval=interval, rate_percent=rate_percent,
                    mark_price=mark_price):
                if s.event_id in self._funding_charged:
                    continue
                self._funding_charged.add(s.event_id)
                pos.funding += s.funding_amount
                # Realised cash flow at the instant, so equity tracks the same
                # arithmetic the close later uses:
                #   pnl = gross - entry_fee - exit_fee - funding
                self.equity -= s.funding_amount
                self.events.append(BrokerEvent("FUNDING", pos.symbol, {
                    "event_id": s.event_id, "position_uid": s.position_uid,
                    "symbol": s.symbol, "side": s.side, "quantity": s.quantity,
                    "exchange_ts": s.exchange_ts,
                    "funding_rate": s.funding_rate,
                    "mark_price": s.mark_price, "notional": s.notional,
                    "funding_amount": s.funding_amount,
                    "interval_seconds": s.interval_seconds,
                    "rate_source": s.rate_source}))
            pos.funding_checked_through = max(pos.funding_checked_through, now)
        return self.events[before:]

    def mark_funding_charged(self, event_ids) -> None:
        """Seed the applied set during recovery, from the durable ledger."""
        self._funding_charged.update(event_ids)

    def _open_exit_order(self, pos: PaperPosition, reason: ExitReason,
                         price: float, when: int, maker: bool) -> PaperOrder:
        """The order that closes a position.

        Its uid is DETERMINISTIC -- "{position_uid}:exit" -- so replaying a
        close cannot manufacture a second exit order, exactly as the entry
        fill's deterministic id prevents a second entry fill.
        """
        uid = f"{pos.position_uid}:exit"
        existing = self.orders.get(uid)
        if existing is not None:
            return existing
        order = PaperOrder(
            order_uid=uid, idempotency_key=uid, signal_key=pos.signal_key,
            symbol=pos.symbol,
            side=-pos.side,                 # closing trades the other way
            order_type="limit" if maker else "market",
            purpose={"TAKE_PROFIT": "target", "STOP_LOSS": "stop"}.get(
                reason.value, "manual"),
            quantity=pos.quantity,
            limit_price=price if maker else None,
            status=OrderStatus.WORKING, created_at=when,
            requested_price=(pos.target_price if reason is ExitReason.TAKE_PROFIT
                             else pos.stop_price if reason is ExitReason.STOP_LOSS
                             else price),
            position_uid=pos.position_uid, passive=maker)
        self.orders[uid] = order
        return order

    def _slip(self, price: float, side: int) -> float:
        """Adverse slippage in basis points, applied against the taker."""
        return price * (1.0 + side * self.slippage_bps / 10_000.0)

    def _resize_for_actual_fill(self, order: PaperOrder,
                                intent: ApprovedOrderIntent,
                                price: float) -> int:
        """Quantity that keeps realised risk inside the approved budget.

        The risk engine sized against a reference price. The fill lands
        somewhere else, which moves the stop distance and therefore the risk --
        upward whenever the slip is adverse. Keeping the approved quantity
        would quietly breach the very limit the engine enforced, so the
        quantity comes down instead. Never up: a favourable fill does not
        licence a bigger position than was approved.
        """
        costs = self.costs[order.symbol]
        rpu = ((price - intent.stop_price) if order.side == LONG
               else (intent.stop_price - price))
        if rpu <= 0:
            return 0
        affordable = int(intent.risk_amount / (rpu * costs.contract_value))
        return max(0, min(order.quantity, affordable))

    def _open_from_fill(self, order: PaperOrder, intent: ApprovedOrderIntent,
                        price: float, when: int, tick_us: int | None,
                        bar_open: int | None) -> PaperPosition | None:
        costs = self.costs[order.symbol]

        qty = self._resize_for_actual_fill(order, intent, price)
        if qty <= 0:
            self._kill_entry(
                order, f"fill at {price} leaves no room inside the "
                       f"${intent.risk_amount:.2f} risk budget", False)
            return None
        if qty != order.quantity:
            log.info("reducing size to stay inside the risk budget",
                     extra={"symbol": order.symbol, "approved": order.quantity,
                            "filled": qty, "price": price})
            self.events.append(BrokerEvent("ORDER_RESIZED", order.symbol, {
                "order_uid": order.order_uid, "approved": order.quantity,
                "filled": qty, "price": price,
                "reference": intent.entry_reference}))
            order.quantity = qty

        notional = costs.notional(order.quantity, price)
        fee = costs.entry_cost(order.quantity, price)
        slip = abs(price - intent.entry_reference) * order.quantity * costs.contract_value

        self.set_status(order, OrderStatus.FILLED)
        order.filled_price = price
        order.filled_at = when
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
            fill_rr=realised_rr(price, intent.stop_price, intent.target_price,
                                order.side),
            funding_checked_through=when,
        )
        self.positions[pos.position_uid] = pos
        order.position_uid = pos.position_uid

        # Booked only now that the position exists, so the fill can NAME it.
        # The event carries the complete record: the persistence layer must
        # never have to reconstruct side or quantity, which is how a short
        # came to be recorded as a long.
        fill = self._book_fill(
            order, pos.position_uid, "entry", price=price,
            quantity=order.quantity, notional=notional, fee=fee,
            slippage=slip, liquidity="maker" if order.passive else "taker",
            when=when, tick_us=tick_us)
        self.events.append(BrokerEvent("FILL", order.symbol, _fill_payload(fill)))
        self.events.append(BrokerEvent("POSITION_OPENED", order.symbol, {
            "position_uid": pos.position_uid, "entry": price,
            "stop": pos.stop_price, "target": pos.target_price,
            "quantity": pos.quantity}))
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

        # AUDIT F3: the exit gets a REAL order, created before its fill, so the
        # fill has a parent to reference and the lifecycle is complete. The
        # previous code fabricated an order_uid that the foreign key rejects --
        # and the write was never even attempted, so exit fee, maker/taker
        # classification and the ordering-proof timestamp were all lost.
        exit_order = self._open_exit_order(pos, reason, price, when, maker)
        exit_fill = self._book_fill(
            exit_order, pos.position_uid, "exit", price=price,
            quantity=pos.quantity, notional=costs.notional(pos.quantity, price),
            fee=fee, slippage=0.0,
            liquidity="maker" if maker else "taker", when=when,
            tick_us=tick_us)
        self.set_status(exit_order, OrderStatus.FILLED)
        exit_order.filled_price = price
        exit_order.filled_at = when

        self.events.append(BrokerEvent("EXIT_ORDER_CREATED", pos.symbol,
                                       _order_payload(exit_order)))
        self.events.append(BrokerEvent("FILL", pos.symbol,
                                       _fill_payload(exit_fill)))
        self.events.append(BrokerEvent("POSITION_CLOSED", pos.symbol, {
            "position_uid": pos.position_uid, "exit": price,
            "reason": reason.value, "pnl": pnl, "r": pos.r_multiple,
            "exit_order_uid": exit_order.order_uid,
            "exit_fee": fee, "tick_ts_us": tick_us}))
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
                blocked = self._entry_blocked(order, intent, px, tick.ts)
                if blocked:
                    self._kill_entry(order, blocked, "expired" in blocked)
                    continue
                self._open_from_fill(order, intent, px, tick.ts, tick.ts_us, None)
            elif order.limit_price is not None:
                blocked = self._entry_blocked(order, intent, order.limit_price,
                                              tick.ts)
                if blocked:
                    self._kill_entry(order, blocked, True)
                    continue
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
                blocked = self._entry_blocked(order, intent, px, bar.start)
                if blocked:
                    self._kill_entry(order, blocked, "expired" in blocked)
                    continue
                self._open_from_fill(order, intent, px, bar.start, None, bar.start)
            elif order.limit_price is not None:
                blocked = self._entry_blocked(order, intent, order.limit_price,
                                              bar.start)
                if blocked:
                    self._kill_entry(order, blocked, True)
                    continue
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

    def expire_stale_entries(self, now: int) -> list[BrokerEvent]:
        """Kill unfilled entry orders that have outlived their setup.

        Called from the bar loop as well as the tick path, because the case
        that matters most is a feed delivering nothing: without a sweep, orders
        accumulate silently and all fill at once when ticks resume.
        """
        before = len(self.events)
        if not self.entry_ttl_seconds:
            return []
        for order in list(self.get_open_orders()):
            if order.purpose != "entry":
                continue
            age = now - order.created_at
            if age > self.entry_ttl_seconds:
                self._kill_entry(
                    order, f"entry order expired after {age}s "
                           f"(ttl {self.entry_ttl_seconds}s)", True)
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

