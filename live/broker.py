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
from math import isfinite
from typing import Any

from live.client import AmbiguousWrite, LiveClient, VenueError, VenueRejected
from live.guards import KILL_SWITCH_PATH, kill_switch_engaged
from app.execution.paper_broker import ENTRY_TTL_SECONDS, MAX_ENTRY_DEVIATION
from app.forwardtest.identity import register_execution_profile
from live.orders import (STOP_LIMIT_CAP_R, OrderRequest, OrderType, Side,
                         StopTriggerMethod, TimeInForce, client_order_id)

log = logging.getLogger(__name__)


class OpeningRefused(VenueError):
    """This broker declined to send an order. Nothing left the process.

    Raised for the checks that run BEFORE the venue is contacted -- the kill
    switch, a suspended symbol, an unknown product. It stays a VenueError so
    every existing `pytest.raises(VenueError)` and every caller that catches
    the base still sees it; it only adds that the outcome is KNOWN, so the
    runtime may release the exposure slot it reserved for this order.
    """

    no_position_can_result = True


class EntryDidNotFill(VenueError):
    """A market IOC entry reached the venue and died with nothing filled.

    Unlike OpeningRefused an order WAS sent -- but an IOC cannot rest, so once
    the venue reports it final and unfilled no position can come of it, and
    the exposure slot reserved for it must be released or it is held forever.
    poll() will never see a fill to close it out, and expire_stale_entries only
    looks at orders still open.
    """

    no_position_can_result = True


#: Order states the venue will not move out of. Anything else -- "open",
#: "pending", or a string not seen before -- is treated as still able to fill,
#: so the slot stays held. A wrongly held slot costs one trade; a wrongly
#: released one can open a second position beside a live one.
_FINAL_STATES = frozenset({"closed", "cancelled"})


def _died_unfilled(row) -> bool:
    """True only when the venue says the order is final AND filled nothing."""
    if not isinstance(row, dict):
        return False
    if str(row.get("state") or "").lower() not in _FINAL_STATES:
        return False
    try:
        size = int(round(float(row.get("size") or 0)))
        unfilled = int(round(float(row.get("unfilled_size") or 0)))
    except (TypeError, ValueError):
        return False              # cannot tell, so do not claim it
    return size > 0 and unfilled >= size


#: THE LIVE EXECUTION SURFACE, declared where it is implemented.
#:
#: It is NOT the paper one. min_fill_rr is a PaperBroker concept about a
#: simulated fill, and this broker does not implement it, so it is not claimed.
#:
#: max_entry_deviation IS claimed, since 2026-09-16, because the live runtime
#: now enforces it: a fill further than that from the reference is flattened
#: the moment the venue reports it. Paper refuses the fill; live cannot refuse
#: what the venue already did, so it closes it. The first live trade on tnet
#: sold SOLUSD 2.2R away from its reference on a thin book, beyond its own
#: stop, and Delta silently dropped both brackets. Recording a gate that fires
#: is true; recording one that did not was the reason it was left out before.
#:
#: These three are what actually governs a live fill: how long an unfilled
#: entry rests, how far beyond the stop the marketable-limit cap sits, and
#: which price the VENUE watches to trigger.
LIVE_EXECUTION_FIELDS = ("entry_ttl_seconds", "stop_limit_cap_r",
                         "stop_trigger_method", "max_entry_deviation",
                         "liquidation_buffer", "sizing_equity_source")

#: HOW FAR BEYOND THE STOP LIQUIDATION MUST SIT, as a multiple of the stop
#: distance. Leverage is chosen per trade so that
#:
#:     1/leverage - maintenance_margin  >=  LIQUIDATION_BUFFER * stop_distance
#:
#: tnet's first live trades used the account's leverage -- ETH 100x, with
#: liquidation 0.50% from entry -- against stops 0.53-0.66% away. Two positions
#: were liquidated before their stops could fire. 3x leaves room for the entry
#: slippage, fees and funding that move the real liquidation price inward.
LIQUIDATION_BUFFER = 3.0

#: Refuse an entry whose margin, at the leverage chosen above, would use more
#: than this share of the available balance.
MARGIN_HEADROOM = 0.9

#: Live sizes from min(internal equity, the venue's balance). See
#: live.runtime.LiveTradingBot._sizing_equity.
SIZING_EQUITY_SOURCE = "min(internal_equity, venue_usd_balance)"


def _live_execution_values(risk) -> dict:
    """What a LiveBroker will report, reconstructed for the CLI.

    tests/live/test_execution_identity_profiles.py asserts this equals what an
    actual LiveBroker yields, because a mismatch here is unbindable and shows
    up four steps later as a drift refusal.
    """
    return {"entry_ttl_seconds": ENTRY_TTL_SECONDS,
            "stop_limit_cap_r": float(STOP_LIMIT_CAP_R),
            "stop_trigger_method": StopTriggerMethod.MARK.value,
            "max_entry_deviation": MAX_ENTRY_DEVIATION,
            "liquidation_buffer": LIQUIDATION_BUFFER,
            "sizing_equity_source": SIZING_EQUITY_SOURCE}


register_execution_profile("live", LIVE_EXECUTION_FIELDS,
                           _live_execution_values)


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
    #: The venue's own liquidation price, as of the last poll. 0 when unknown.
    liquidation_price: float = 0.0

    def is_open(self) -> bool:
        return self.contracts > 0


class LiveBroker:
    """Places real orders. Learns their outcome by asking the venue."""
    #: Which attributes describe this broker in an experiment identity.
    #: See app/forwardtest/identity.LIVE_EXECUTION_FIELDS.
    EXECUTION_IDENTITY_FIELDS = LIVE_EXECUTION_FIELDS


    def __init__(self, client: LiveClient, *, product_ids: dict[str, int],
                 experiment_id: str, tick_size: dict[str, Decimal] | None = None,
                 entry_ttl_seconds: int = 90,
                 exit_on_wpr_band_exit: bool = False,
                 wpr_exit_long_level: float = -80.0,
                 wpr_exit_short_level: float = -20.0,
                 kill_switch_path: str | None = None) -> None:
        self.client = client
        self.product_ids = dict(product_ids)
        self.experiment_id = experiment_id
        self.tick_size = dict(tick_size or {})
        self.entry_ttl_seconds = entry_ttl_seconds
        #: See LIVE_EXECUTION_FIELDS. Enforced by live.runtime after the
        #: fill, in units of the trade's own R.
        self.max_entry_deviation = MAX_ENTRY_DEVIATION
        self.liquidation_buffer = LIQUIDATION_BUFFER
        self.sizing_equity_source = SIZING_EQUITY_SOURCE
        #: symbol -> (initial_margin %, maintenance_margin %, contract_value)
        self._margin_specs: dict[str, tuple[float, float, float]] = {}
        #: THE EXECUTION SURFACE THIS BROKER ACTUALLY HAS, recorded on the
        #: instance so the experiment identity reads what the broker received
        #: rather than a constant someone hoped matched. They were equal only
        #: by coincidence before: LiveBroker was constructed without
        #: entry_ttl_seconds at all and fell back to a default that happened to
        #: be the same 90 the CLI wrote.
        self.stop_limit_cap_r = float(STOP_LIMIT_CAP_R)
        self.stop_trigger_method = StopTriggerMethod.MARK.value
        self.exit_on_wpr_band_exit = exit_on_wpr_band_exit
        self.wpr_exit_long_level = wpr_exit_long_level
        self.wpr_exit_short_level = wpr_exit_short_level
        self.kill_switch_path = kill_switch_path
        #: symbol -> LivePosition, refreshed by poll(). A CACHE.
        self.positions: dict[str, LivePosition] = {}
        #: client_order_id -> the intent that produced it, for attribution.
        self._pending: dict[str, dict] = {}
        self._suspended: set[str] = set()
        self._entry_cid: dict[str, str] = {}
        #: symbol -> the reason this process asked the venue to close it.
        #: poll() hands it to POSITION_CLOSED, which live.ledger needs:
        #: without it a deliberate flatten is recorded as MANUAL_CLOSE.
        self._closing_reason: dict[str, str] = {}
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

    def _margin_spec(self, symbol: str) -> tuple[float, float, float]:
        """(initial_margin %, maintenance_margin %, contract_value), cached."""
        if symbol not in self._margin_specs:
            row = self.client.get_product(symbol)
            im = float(row.get("initial_margin") or 0)
            mm = float(row.get("maintenance_margin") or 0)
            cv = float(row.get("contract_value") or 0)
            if im <= 0 or mm <= 0 or cv <= 0:
                raise VenueError(f"{symbol} has no usable margin spec: "
                                 f"initial={im} maintenance={mm} contract={cv}")
            self._margin_specs[symbol] = (im, mm, cv)
        return self._margin_specs[symbol]

    def _refuse_if_book_dislocated(self, intent) -> None:
        """Refuse a market entry the book cannot fill near its reference.

        ADDED 2026-09-16. tnet's SOLUSD book sat ~1.4% above the price the
        signals are computed from. Every SOL entry filled 1.2-1.5R from its
        reference, and _flatten_if_entry_invalid closed it seconds later: five
        round trips in forty minutes, each paying the spread and fees to learn
        what the book already showed before the order was sent.

        This is the same limit, applied to the touch the order will meet --
        the best ask for a buy, the best bid for a sell -- and the same
        beyond-the-stop rule. It cannot replace the fill check: a market order
        walks the book past the touch. It only stops sending orders that are
        certain to be closed.

        Fails closed: a ticker that cannot be read, or that has no quote on
        the side the order needs, refuses the entry. Nothing has been sent, so
        the runtime releases the exposure slot. Limit entries are not checked:
        their price already bounds the fill.
        """
        if intent.order_type != "market":
            return
        symbol = intent.symbol
        ref = getattr(intent, "entry_reference", None)
        rpu = float(getattr(intent, "risk_per_unit", 0) or 0)
        if not ref or rpu <= 0:
            raise OpeningRefused(f"{symbol}: no entry reference or risk per unit "
                                 f"to check the book against")
        ref = float(ref)
        side = "best_ask" if intent.side > 0 else "best_bid"
        try:
            quotes = (self.client.get_ticker(symbol) or {}).get("quotes") or {}
            touch = float(quotes.get(side) or 0)
        except (VenueError, TypeError, ValueError) as exc:
            raise OpeningRefused(f"{symbol}: cannot read the book ({exc})") from exc
        if touch <= 0:
            raise OpeningRefused(f"{symbol}: the book has no {side}; not opening")

        stop = float(intent.stop_price)
        beyond = (intent.side > 0 and touch <= stop) or (intent.side < 0 and touch >= stop)
        dev = abs(touch - ref) / rpu
        limit = float(self.max_entry_deviation or 0)
        if beyond or (limit > 0 and dev > limit):
            raise OpeningRefused(
                f"{symbol}: book {side} {touch:g} is {dev:.2f}R from reference "
                f"{ref:g} (limit {limit:g}R{', beyond the stop' if beyond else ''})")

    def _set_safe_leverage(self, intent, pid: int) -> int:
        """Choose, set and confirm the leverage for this entry, or refuse it.

        The lowest leverage is not the goal; liquidation beyond the stop is.
        So this takes the HIGHEST leverage whose liquidation still sits
        LIQUIDATION_BUFFER times further out than the stop, capped at the
        product's maximum -- the least margin that is still safe.

        Every failure raises OpeningRefused: nothing has been sent, so the
        runtime releases the exposure slot. Reading the leverage back after
        setting it is deliberate -- the setting is what liquidation depends
        on, and "the POST returned 200" is not the same claim.
        """
        symbol = intent.symbol
        entry = getattr(intent, "entry_reference", None)
        if not entry:
            raise OpeningRefused(f"{symbol}: no entry reference to size leverage from")
        entry = float(entry)
        stop_distance = abs(entry - float(intent.stop_price)) / entry
        if stop_distance <= 0:
            raise OpeningRefused(f"{symbol}: stop equals entry; cannot size leverage")
        try:
            im_pct, mm_pct, contract_value = self._margin_spec(symbol)
        except VenueError as exc:
            raise OpeningRefused(f"{symbol}: {exc}") from exc

        max_leverage = int(100.0 / im_pct)
        leverage = min(int(1.0 / (self.liquidation_buffer * stop_distance
                                  + mm_pct / 100.0)), max_leverage)
        if leverage < 1:
            raise OpeningRefused(
                f"{symbol}: a {stop_distance:.2%} stop cannot sit inside "
                f"liquidation at any leverage with {mm_pct}% maintenance margin")

        notional = int(intent.quantity) * contract_value * entry
        margin = notional / leverage
        try:
            available = float(self.client.get_wallet_balance("USD")
                              .get("available_balance") or 0)
        except VenueError as exc:
            raise OpeningRefused(f"{symbol}: could not read the balance: {exc}") from exc
        if margin > available * MARGIN_HEADROOM:
            raise OpeningRefused(
                f"{symbol}: margin ${margin:,.2f} at {leverage}x exceeds "
                f"{MARGIN_HEADROOM:.0%} of the available ${available:,.2f}")

        try:
            self.client.set_order_leverage(pid, leverage)
            confirmed = self.client.get_order_leverage(pid)
        except VenueError as exc:
            raise OpeningRefused(f"{symbol}: could not set leverage {leverage}x: {exc}") from exc
        if int(round(confirmed)) != leverage:
            raise OpeningRefused(
                f"{symbol}: asked for {leverage}x, the venue reports {confirmed}x")

        liq_distance = 1.0 / leverage - mm_pct / 100.0
        log.info("%s leverage %dx: liquidation ~%.2f%% from entry, stop %.2f%% "
                 "(%.1fx), margin $%.2f of $%.2f", symbol, leverage,
                 liq_distance * 100, stop_distance * 100,
                 liq_distance / stop_distance, margin, available)
        return leverage

    def submit_order(self, intent, *, now: int | None = None,
                     order_uid: str | None = None) -> dict:
        """Send one risk-approved intent to the venue, with its brackets.

        Returns the venue's order row. Raises rather than returning a partial
        truth: an intent whose outcome is unknown must reach the caller as an
        exception, because the correct response is to stop and reconcile.

        `order_uid` IS THE RUNTIME'S DATABASE ROW for this order, and the
        runtime has always passed it -- PaperBroker takes it. This broker did
        not, so every live entry raised TypeError after its exposure slot was
        reserved and before anything reached the venue. tnet leaked all six
        slots that way on 2026-09-16 and then refused every entry for hours
        while reporting healthy. It is recorded beside the venue's
        client_order_id, which stays derived from the intent so a lookup after
        an ambiguous write still finds it; the two ids are otherwise unrelated,
        and this is the only place that links them.

        Raised exceptions say whether anything could exist at the venue, via
        `no_position_can_result` -- see live.client.VenueError.
        """
        # THE LAST GATE BEFORE AN ORDER LEAVES. Checked per order rather than
        # at start-up, because the point of a kill switch is the trade that
        # has not happened yet. It stops OPENING only: positions already open
        # keep their exchange-held brackets, which is the protection that
        # matters once something has gone wrong enough to reach for this.
        if kill_switch_engaged(self.kill_switch_path):
            raise OpeningRefused(
                f"kill switch engaged ({self.kill_switch_path or KILL_SWITCH_PATH}); "
                f"not opening {intent.symbol}")
        if intent.symbol in self._suspended:
            raise OpeningRefused(f"{intent.symbol} is suspended; not opening")
        pid = self.product_ids.get(intent.symbol)
        if pid is None:
            raise OpeningRefused(f"no product_id known for {intent.symbol}")
        self._refuse_if_book_dislocated(intent)
        leverage = self._set_safe_leverage(intent, pid)

        side = Side.for_position(intent.side)
        stop = self._round(intent.symbol, intent.stop_price)
        target = self._round(intent.symbol, intent.target_price)
        # The cap sits BEYOND the stop, in the direction the loss runs: a long
        # exits by selling, so its cap is below the stop.
        cap = self._round(
            intent.symbol,
            float(Decimal(str(intent.stop_price))
                  - Decimal(str(intent.side)) * Decimal(str(self.stop_limit_cap_r))
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
            stop_trigger_method=StopTriggerMethod(self.stop_trigger_method),
            note=intent.signal_key)

        # SYMBOL -> the id of the order that opened it. poll() reports a
        # position by SYMBOL (the venue does not know our uids), so this is the
        # link back to what we intended when the fill is finally seen.
        self._entry_cid[intent.symbol] = cid
        self._pending[cid] = {
            "order_uid": order_uid,
            "leverage": leverage,
            # live.ledger.open_record falls back to these. `quantity` is read
            # whenever the venue's order row reports no filled size, and was
            # absent, so that path raised KeyError inside the poll loop -- which
            # swallows it -- leaving a real position unrecorded.
            "quantity": int(intent.quantity),
            # OPTIONAL, as open_record already treats it (`.get`): it only
            # refines the recorded slippage. getattr because not every caller
            # builds a full ApprovedOrderIntent -- quantity above is required
            # and already used for the order size, this is not.
            "entry_reference": getattr(intent, "entry_reference", None),
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
        try:
            row = self.client.place_order(order)
        except VenueRejected:
            # The venue declined, so this order will never produce a fill for
            # poll() to attribute. The bookkeeping above was written BEFORE
            # sending on purpose -- an ambiguous write may still land and must
            # be attributable -- so a definite refusal is the one case that has
            # to take it back out, or the next fill on this symbol is linked to
            # an order that does not exist.
            if self._entry_cid.get(intent.symbol) == cid:
                del self._entry_cid[intent.symbol]
            self._pending.pop(cid, None)
            raise
        if intent.order_type == "market" and _died_unfilled(row):
            if self._entry_cid.get(intent.symbol) == cid:
                del self._entry_cid[intent.symbol]
            self._pending.pop(cid, None)
            log.warning("%s IOC entry %s was not filled (state %s); nothing "
                        "opened", intent.symbol, cid, row.get("state"))
            raise EntryDidNotFill(
                f"{intent.symbol} market entry {cid} ended {row.get('state')} "
                f"with nothing filled")
        return row

    def protective_legs(self, symbol: str) -> dict[str, list[dict]]:
        """The reduce-only stop-loss and take-profit orders protecting `symbol`.

        Read from PENDING orders: that is where the venue keeps untriggered
        bracket legs, and get_open_orders() never sees them. Raises VenueError
        when the venue cannot be read -- a caller must not treat "could not
        look" as "nothing there".
        """
        pid = self.product_ids.get(symbol)
        rows = self.client.get_pending_orders(pid)
        out: dict[str, list[dict]] = {"stop_loss": [], "take_profit": []}
        for row in rows:
            if pid is not None and row.get("product_id") not in (None, pid):
                continue
            if not row.get("reduce_only"):
                continue
            kind = row.get("stop_order_type")
            if kind == "stop_loss_order":
                out["stop_loss"].append(row)
            elif kind == "take_profit_order":
                out["take_profit"].append(row)
        return out

    def _position_key(self, pos: LivePosition) -> str:
        """What makes THIS position's exit id different from every other's.

        ADDED 2026-09-16. The exit id was keyed on `pos.position_uid or
        symbol`, and Delta's margined positions carry no uid, so it was keyed
        on the symbol: tnet's five SOLUSD entry_deviation closes that evening
        all went out as cid 727639a06bd897e7. The venue accepted each one, but
        client.place_order resolves a timed-out or 5xx write by looking its cid
        up -- and would have found the PREVIOUS, filled close, and reported a
        close that never landed as done, on a position being flattened because
        it was unsafe.

        In order of preference: the entry order that opened it (known for any
        position this process opened), the venue's own uid, and otherwise the
        venue's facts about it -- enough to tell one recovered position from
        the last one closed on that symbol, and stable across retries.
        """
        entry_cid = self._entry_cid.get(pos.symbol)
        if entry_cid:
            return entry_cid
        if pos.position_uid:
            return pos.position_uid
        return (f"{pos.symbol}:{pos.side}:{pos.contracts}:"
                f"{pos.entry_price!r}")

    def close_position(self, symbol: str, reason: str) -> dict:
        """Flatten one position at market, reduce-only.

        reduce_only is not decoration: without it, a size that disagrees with
        the venue's by one contract OPENS an opposite position instead of
        closing. That is the failure mode of every hand-rolled flatten.
        """
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open():
            raise VenueError(f"no open position in {symbol} to close")
        cid = client_order_id(self.experiment_id, self._position_key(pos),
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
        self._closing_reason[symbol] = reason
        return self.client.place_order(order)

    # -- THE BROKER SURFACE TradingBot REQUIRES ------------------------------
    #
    # These three exist because the bot calls them, and their absence is how
    # the first live deploy failed: `AttributeError: 'LiveBroker' object has no
    # attribute 'close_if_setup_invalidated'`, 1388 times, once per second, on
    # a host whose /readyz had passed and whose deploy was recorded a success.
    #
    # Every unit test drove LiveBroker directly against a FakeClient, so none
    # of them ever went through TradingBot -- the only caller that reaches
    # these. A broker is not "the methods it has", it is the surface its driver
    # calls, and nothing asserted the two matched.

    def close_if_setup_invalidated(self, symbol: str, wpr: float, price: float,
                                   now: int) -> list[BrokerEvent]:
        """Close a position whose entry band no longer holds.

        Same rule as PaperBroker: called once per CLOSED primary bar, because
        %R only updates on a closed bar. ONLY THE ADVERSE SIDE COUNTS -- a long
        exits below the floor; a long that climbs past the ceiling is winning
        and is left alone. Backwards, this would close exactly the trades that
        reach target.

        THE EVENT IS NOT SYNTHESISED HERE. The paper broker closes the position
        in memory and returns the event; a live close is a market order that
        may fill at a different price, later, or not at all. poll() sees the
        position vanish and emits POSITION_CLOSED from what the venue actually
        did. Returning a fill here would be inventing one.
        """
        if not self.exit_on_wpr_band_exit or wpr is None or not isfinite(wpr):
            return []
        pos = self.positions.get(symbol)
        if pos is None or not pos.is_open():
            return []
        failed = (wpr < self.wpr_exit_long_level if pos.side > 0
                  else wpr > self.wpr_exit_short_level)
        if not failed:
            return []
        try:
            self.close_position(symbol, "setup_invalidated")
        except VenueError as exc:
            # Loud, and not fatal: the next bar re-evaluates. Raising here
            # would take down the bar loop over one rejected order.
            log.error("could not close %s on setup invalidation: %s", symbol, exc)
        return []

    def settle_funding(self, symbol: str, now: int, *, rate_percent: float,
                       mark_price: float, interval: int) -> list[BrokerEvent]:
        """Nothing. THE VENUE CHARGES FUNDING ON A REAL ACCOUNT.

        PaperBroker computes and books funding because no one else will. Here
        Delta debits it directly and it arrives in the position's own P&L --
        live/ledger.py reads the venue's figure. Simulating it as well would
        count every settlement twice, once in a number we invented and once in
        the number the exchange actually charged.

        Returning [] rather than raising: the bot calls this on a schedule and
        a live account is simply not where the answer comes from.
        """
        return []

    def mark_funding_charged(self, event_ids) -> None:
        """Nothing, for the same reason as settle_funding.

        The argument is consumed so a generator is not left unevaluated by a
        silent no-op -- a caller passing a generator expression would otherwise
        never run it, which is a different bug wearing this one's clothes.
        """
        for _ in event_ids:
            pass

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
            # THE RUNTIME PERSISTS THIS BY order_uid, and indexes the key
            # unconditionally. It used to be absent, which was harmless only
            # because no live order had ever reached the venue; the first stale
            # limit entry would have raised KeyError in the bar loop after
            # already cancelling at the venue, losing the status update and
            # leaking the slot. Unknown after a restart -- `_pending` is memory
            # -- in which case the event is not emitted rather than emitted
            # half-formed; the startup orphan sweep owns that case.
            cid = row.get("client_order_id")
            order_uid = (self._pending.get(cid) or {}).get("order_uid")
            if not order_uid:
                log.warning("cancelled stale entry %s (cid %s) with no known "
                            "order row; not reporting it", row.get("id"), cid)
                continue
            self._pending.pop(cid, None)
            if self._entry_cid.get(sym) == cid:
                del self._entry_cid[sym]
            events.append(BrokerEvent("ORDER_CANCELLED", sym,
                                      {"order_uid": order_uid,
                                       "order_id": row.get("id"), "age": age}))
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
                position_uid=str(row.get("position_uid") or row.get("id") or ""),
                liquidation_price=float(row.get("liquidation_price") or 0.0))

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
                    "closed_by": "venue",
                    # Read by _persist_close; live.ledger uses it only when the
                    # closing order was not a bracket leg.
                    "requested_reason": self._closing_reason.pop(sym, None)}))

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
