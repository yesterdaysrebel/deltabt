"""The live bot: the paper bot's brain, with the venue wired in where the
simulation used to be.

WHY A SUBCLASS AND NOT A REWRITE

app/runtime/bot.py is 1,339 lines and almost none of it cares where orders go.
The feed, the candle assembly, the strategy evaluation, the risk gates, the
experiment binding, the persistence and the health endpoints are all identical
whether a fill was simulated or reported. Copying them would create a second
copy of every one of those decisions, and the two would drift -- this
repository has a scar for exactly that (see broker_params in paper_broker.py,
where one literal in two places cost a bot that refused its own experiment).

`live` may import `app`; `app` may never import `live`
(tests/live_exec/test_boundary_preserved.py). So the live bot inherits.

WHAT IT REPLACES, AND WHY EACH ONE HAD TO BE REPLACED

1.  THE BROKER. PaperBroker -> LiveBroker. Different physics, same surface;
    see live/broker.py.

2.  recover(). The inherited one rebuilds positions from the database and
    writes them into `broker.positions` keyed by position_uid, as PaperPosition
    objects. LiveBroker keys by SYMBOL, because the venue does not know our
    uids -- it is a cache of what the exchange says, not of what we recorded.
    More importantly the inherited version reconciles the database against
    ITSELF (duplicate rows, unknown symbols) and cannot reconcile it against
    the venue, which is the check that matters once orders are real.

3.  THE POLL LOOP. PaperBroker learns of a fill inside process_market_event.
    LiveBroker learns by asking. Nothing in the inherited runtime asks, so the
    loop is added here.

WHAT IS NOT DONE, AND MUST BE BEFORE THIS TRADES

The inherited persistence path (drain_broker_events, _persist_fill,
_persist_close) reads PaperFill and PaperPosition objects. LiveBroker emits
BrokerEvents carrying plain dicts from the venue. Those two have to be mapped
before a live fill reaches the database, and until they are this bot can open
and close positions WITHOUT RECORDING THEM -- which reconciliation would then
correctly refuse to start on. That mapping is the next piece of work and is
deliberately not faked here.
"""

from __future__ import annotations

import asyncio
import logging

from app.runtime.bot import STATE_KEY, TradingBot
from app.risk.engine import RiskState
from live.broker import LiveBroker
from live.client import LiveClient, VenueError
from live.reconcile import reconcile

log = logging.getLogger(__name__)

#: How often to ask the venue what it did. Fills arrive whenever the exchange
#: says so, and the only cost of a missed poll is latency -- poll() is a diff,
#: so the next one sees the same difference.
POLL_SECONDS = 5.0

#: How often to re-check the venue against our own records while running.
#: Startup reconciliation catches a crash; this catches a divergence that
#: opens WHILE running, which is the one nobody is watching for.
RECONCILE_SECONDS = 300.0

#: How long after a position opens its stop-loss must exist at the venue.
#: Delta creates bracket legs when the entry fills, and poll() learns of the
#: position a moment later, so checking on the very first sighting could race
#: the venue and flatten a correctly protected trade. Three polls.
BRACKET_GRACE_SECONDS = 15.0

#: If the venue cannot be read for this long after the grace, say so loudly.
#: It does not flatten on an unreadable venue: the close would need that same
#: venue, and closing a protected position during an outage is its own harm.
BRACKET_ALERT_SECONDS = 120.0

#: How long a venue balance read is reused for sizing. Several symbols close on
#: the same 5m bar; one read serves them, a stale one does not survive a trade.
BALANCE_TTL_SECONDS = 20.0


class LiveTradingBot(TradingBot):
    """A TradingBot whose orders reach a real exchange."""

    def __init__(self, *args, client: LiveClient, product_ids: dict[str, int],
                 tick_size=None, venue: str = "testnet",
                 kill_switch_path: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.client = client
        self.venue = venue
        self.product_ids = dict(product_ids)
        # REPLACE the broker the parent built. Constructing the parent first
        # and overwriting is deliberate: every other attribute it sets up is
        # wanted, and re-deriving them here would be the copy this class exists
        # to avoid.
        # THE BAND-EXIT SETTINGS COME FROM WHERE THE PAPER BROKER'S DO.
        #
        # The parent has just built a PaperBroker from settings.risk and we are
        # about to discard it. Reading the same three values keeps the two
        # brokers configured identically, which is the only thing that makes
        # "same surface, different physics" mean anything -- a live bot quietly
        # running a different exit rule from the paper bot is not a rehearsal
        # of the paper bot.
        #
        # settings.RISK, not settings.strategy: these are risk configuration so
        # that risk_hash covers them (see the note at the PaperBroker
        # construction in app/runtime/bot.py). Defaulting them here instead
        # would be a third copy of a value that already exists twice.
        self.broker = LiveBroker(
            client, product_ids=self.product_ids,
            experiment_id=getattr(self, "experiment_id", "") or "unbound",
            tick_size=tick_size or {},
            exit_on_wpr_band_exit=self.settings.risk.exit_on_wpr_band_exit,
            wpr_exit_long_level=self.settings.risk.wpr_exit_long_level,
            wpr_exit_short_level=self.settings.risk.wpr_exit_short_level,
            kill_switch_path=kill_switch_path)
        self._symbol_for = {v: k for k, v in self.product_ids.items()}
        self._poll_task: asyncio.Task | None = None

    # -- startup -------------------------------------------------------------

    async def start(self) -> bool:
        """Refuse prod outright if the circuit breakers are switched off.

        BEFORE the lock, the database and the experiment binding, because none
        of those matter if the process must not trade at all. The check is a
        no-op on testnet -- see live/guards.py for why that exemption is
        deliberate and why this check exists at all.
        """
        from live.guards import GuardError, require_circuit_breakers
        try:
            require_circuit_breakers(self.settings.risk, self.venue)
        except GuardError as exc:
            log.critical("%s", exc)
            self.recovery_error = str(exc)
            await self.notifier.send("REFUSING TO START", str(exc))
            return False
        return await super().start()

    async def recover(self) -> None:
        """Rebuild from the database, then check the VENUE agrees.

        Order matters. The database check runs first because a database that
        disagrees with itself cannot be compared to anything; the venue check
        runs second because it is the one that can stop a real position being
        traded around.
        """
        stored = await self.repo.get_state(STATE_KEY)
        if stored:
            self.state = RiskState.from_dict(stored)
            log.info("restored risk state", extra={"equity": self.state.equity})

        positions = await self.repo.load_open_positions()

        by_symbol: dict[str, int] = {}
        for p in positions:
            by_symbol[p.symbol] = by_symbol.get(p.symbol, 0) + 1
        dupes = [s for s, n in by_symbol.items() if n > 1]
        if dupes:
            self.recovery_error = f"duplicate open positions for {dupes}"
            await self._event("recovery", "RECONCILIATION_FAILED",
                              severity="CRITICAL", payload={"symbols": dupes})
            return

        for p in positions:
            if p.symbol not in self.costs:
                self.recovery_error = (
                    f"open position in {p.symbol}, which is not in the "
                    f"configured universe")
                await self._event("recovery", "RECONCILIATION_FAILED",
                                  severity="CRITICAL", payload={"symbol": p.symbol})
                return

        if not await self.reconcile_with_venue(positions):
            return

        await self._release_orphaned_entries()

        # A POSITION OPENED BY A PREVIOUS PROCESS IS CHECKED TOO. Reconciliation
        # has just confirmed it exists at the venue; it has not confirmed it is
        # protected there. This is what catches a position left unprotected
        # before this check existed -- tnet's SOLUSD short of 2026-09-16 would
        # be flattened by the first process to run this.
        for p in positions:
            self._schedule_bracket_check(p.symbol)

        self._state_loaded = True
        await self._event("recovery", "STATE_RESTORED", payload={
            "open_positions": len(positions), "equity": self.state.equity})

    async def _release_orphaned_entries(self) -> None:
        """Free exposure slots held by entry orders a previous process owned.

        WHY THIS EXISTS. An entry order row is the exposure reservation, and
        the process that made it is the only one that knew how to close it
        out: LiveBroker's `_pending` map lives in memory. When that process
        dies before the order resolves, the row stays WORKING forever and holds
        a slot nobody can release. On 2026-09-16 six such rows filled tnet's
        six slots and it refused every entry for hours, reporting healthy.
        (Those six had a second cause -- a TypeError that stopped any order
        reaching the venue -- but any restart can orphan a row.)

        WHEN A ROW IS RELEASED, AND WHEN IT IS NOT. This runs after
        reconcile_with_venue() has confirmed the ledger's positions match the
        venue. A row is released only if the venue shows NO open order and NO
        position on its symbol, because then no position can come of it.
        Anything on the symbol at the venue leaves the row alone and says so:
        this process cannot tell which order it was, and freeing the slot
        would let a second entry open beside something real.

        A venue read failure skips the sweep entirely. The bot then stays as
        blocked as it was, which is visible; guessing is not.
        """
        pending = await self.repo.load_reserving_entry_orders()
        if not pending:
            return
        try:
            open_orders = self.client.get_open_orders()
            venue_positions = self.client.get_positions()
        except VenueError as exc:
            log.error("could not read the venue to check %d orphaned entry "
                      "order(s); leaving them held: %s", len(pending), exc)
            return

        def _sym(row) -> str:
            pid = row.get("product_id")
            return (row.get("product_symbol")
                    or self._symbol_for.get(int(pid) if pid else -1, ""))

        busy = {_sym(r) for r in open_orders}
        busy |= {_sym(r) for r in venue_positions
                 if int(round(float(r.get("size") or 0))) != 0}

        for order in pending:
            if order.symbol in busy:
                log.warning("entry order %s on %s is unresolved but the venue "
                            "has activity on that symbol; leaving its slot "
                            "held", order.order_uid, order.symbol)
                await self._event("recovery", "ORPHANED_ENTRY_HELD",
                                  symbol=order.symbol, severity="WARNING",
                                  payload={"order_uid": order.order_uid,
                                           "instance_uid": order.instance_uid})
                continue
            await self.repo.update_order_status(order.order_uid, "CANCELLED")
            log.warning("released orphaned entry order %s on %s from instance "
                        "%s: nothing at the venue on that symbol",
                        order.order_uid, order.symbol, order.instance_uid)
            await self._event("recovery", "ORPHANED_ENTRY_RELEASED",
                              symbol=order.symbol, severity="WARNING",
                              payload={"order_uid": order.order_uid,
                                       "instance_uid": order.instance_uid,
                                       "status_was": order.status})

    async def reconcile_with_venue(self, ledger_positions) -> bool:
        """True when the venue and our records agree. Sets recovery_error if not.

        FAILS CLOSED IN BOTH DIRECTIONS. A disagreement halts, and so does an
        inability to ask -- a venue we cannot read is not a venue we agree
        with, and "assume flat" is how an unknown position gets traded around.
        """
        try:
            venue = self.client.get_positions()
        except VenueError as exc:
            self.recovery_error = f"could not read positions from the venue: {exc}"
            await self._event("recovery", "RECONCILIATION_FAILED",
                              severity="CRITICAL", payload={"error": str(exc)})
            await self.notifier.send("RECONCILIATION FAILED", self.recovery_error)
            return False

        # `quantity`, NOT `contracts`. PositionRecord has never had a
        # `contracts` field -- that is LivePosition's name -- so this raised
        # AttributeError on the first restart with an open position in the
        # ledger, and recover() died every start after: a crash loop while
        # holding positions. It went unseen because tests/live_exec/
        # test_runtime.py fed it a fake with a `contracts` attribute, and the
        # only live process that ever ran started with an empty ledger. Found
        # 2026-09-16 by a test that recovers from the real InMemoryRepository,
        # with tnet holding three positions it could not have restarted with.
        ours = [{"symbol": p.symbol,
                 "size": (1 if str(getattr(p, "side", "")).upper()
                          in {"LONG", "BUY", "1"} else -1) * int(p.quantity)}
                for p in ledger_positions]
        result = reconcile(venue, ours, symbol_for=self._symbol_for)
        if not result.ok:
            self.recovery_error = result.render()
            await self._event("recovery", "RECONCILIATION_FAILED",
                              severity="CRITICAL",
                              payload={"discrepancies": [
                                  {"kind": d.kind, "symbol": d.symbol,
                                   "venue": d.venue_size, "ledger": d.ledger_size}
                                  for d in result.discrepancies]})
            await self.notifier.send("RECONCILIATION FAILED", result.render())
            return False

        # Prime the broker's cache from the same read, so the first poll() does
        # not report every existing position as newly opened.
        self.broker.poll()
        log.info("venue reconciled: %s", result.render())
        return True

    # -- recording what the venue did ----------------------------------------

    async def _persist_open(self, ev) -> None:
        """Record a position the venue opened.

        The event says only WHICH SYMBOL, because that is all poll() can know
        -- the venue does not carry our uids. Everything else is fetched: the
        intent from what we submitted, the fill from the venue's own order row.

        AN UNATTRIBUTED POSITION IS RECORDED AS ONE, NOT GUESSED AT. After a
        restart `_pending` is empty, so a position opened by a previous process
        cannot be linked to an intent. Inventing one would hide exactly what
        reconciliation exists to surface, so this logs CRITICAL and leaves the
        row unwritten -- the next reconcile then refuses to trade, which is the
        correct outcome and a visible one.
        """
        from live.ledger import entry_facts, find_by_client_order_id, open_record

        symbol = ev.symbol
        cid, intent = self.broker.intent_for(symbol)
        if not cid or not intent:
            await self._event("execution", "UNATTRIBUTED_POSITION",
                              symbol=symbol, severity="CRITICAL",
                              payload=dict(ev.payload))
            log.critical("the venue opened %s and this process cannot say why "
                         "(no intent in memory -- a restart?). NOT recording a "
                         "guess; reconciliation will refuse to trade.", symbol)
            return

        order = self.client.get_order_by_client_id(cid)
        if order is None:
            log.error("no venue order for %s (cid=%s); cannot record the entry",
                      symbol, cid)
            return

        record = open_record(
            intent=intent, venue_entry=entry_facts(order),
            position_uid=cid, instance_uid=self.instance_uid,
            strategy_version=self.strategy.version,
            equity_before=self.state.equity,
            experiment_id=self.experiment_id,
            config_hash=self.identity.config_hash if self.identity else None)

        if not await self.repo.open_position(record):
            await self._event("execution", "DUPLICATE_POSITION_REFUSED",
                              symbol=symbol, severity="CRITICAL",
                              payload={"position_uid": cid})
            return

        # THE ENTRY ORDER ROW IS THE EXPOSURE RESERVATION, and it has to learn
        # it filled. Recording the POSITION alone left the row WORKING with no
        # position_uid, so effective_exposure() counted this trade TWICE while
        # it was open and ONCE for ever after it closed -- a permanent slot
        # leak per trade, and a bot that blocks itself after max_open_positions
        # round trips. PaperBroker closes the row through drain_broker_events;
        # the live path records fills here instead, so it has to here too.
        order_uid = intent.get("order_uid")
        if order_uid:
            await self.repo.record_order_fill(
                order_uid, filled_price=record.entry_price,
                filled_exchange_ts=record.opened_at, position_uid=cid)
            await self.repo.update_order_status(order_uid, "FILLED")
        else:
            log.error("position %s opened with no order row to close out; its "
                      "exposure slot stays held", symbol)

        if await self._flatten_if_entry_invalid(symbol, record, intent):
            return
        self._schedule_bracket_check(symbol)
        self.state.trades_today += 1
        await self._save_state()
        await self.notifier.send(
            f"{self.venue} {'LONG' if record.side > 0 else 'SHORT'} {symbol}",
            f"entry {record.entry_price} stop {record.stop_price} "
            f"target {record.target_price} qty {record.quantity}")

    # -- protecting what is open ----------------------------------------------
    #
    # WHY THIS EXISTS. The first live trades on tnet, 2026-09-16: SOLUSD was
    # approved at 97.299 with its stop at 97.8215 and target 95.7314 -- and a
    # market sell on a thin testnet book filled at 98.463, 2.2R from the
    # reference and BEYOND ITS OWN STOP. Delta silently dropped both bracket
    # legs, because a buy-stop below a short's entry is on the wrong side. So a
    # 95-contract short sat open with no stop-loss and no take-profit, while
    # the ledger recorded a stop of 97.8215 as if it were protected. Nothing
    # alerted. BTCUSD and ETHUSD, filled within a tick of their references, had
    # both legs.
    #
    # Two checks, and one action for both. A position that is unprotected, or
    # whose fill no longer matches the geometry risk approved, is CLOSED. It is
    # the one remedy that does not require this process to invent a new stop
    # price the risk engine never saw.

    def _sizing_equity(self) -> float | None:
        """min(internal equity, the venue's USD balance), or 0 if unreadable.

        WHY MIN. The internal ledger starts every experiment at 10,000; the
        tnet account held $738.78. Sizing from the ledger made a 0.5% risk
        budget 6.8% of the real account. Sizing from the venue alone would let
        a large account oversize an experiment planned at 10,000. The smaller
        of the two is right in both directions.

        FAILS CLOSED. An unreadable balance sizes at zero, so the entry rounds
        to no contracts and is refused -- never the internal 10,000 by default.
        """
        cache = self.__dict__.get("_balance_cache")
        t = asyncio.get_event_loop().time()
        if cache and t - cache[0] < BALANCE_TTL_SECONDS:
            venue = cache[1]
        else:
            try:
                venue = float(self.client.get_wallet_balance("USD")
                              .get("balance") or 0)
            except VenueError as exc:
                log.critical("could not read the venue balance; sizing at zero: %s", exc)
                return 0.0
            self.__dict__["_balance_cache"] = (t, venue)
        return min(float(self.state.equity), venue)

    def _bracket_checks(self) -> dict[str, float]:
        """symbol -> loop time at which its stop-loss must exist.

        Created on first use: several harnesses build this class with __new__.
        """
        return self.__dict__.setdefault("_bracket_due", {})

    def _flattening(self) -> set[str]:
        return self.__dict__.setdefault("_flattening_set", set())

    def _schedule_bracket_check(self, symbol: str, *, now: float | None = None) -> None:
        t = asyncio.get_event_loop().time() if now is None else now
        self._bracket_checks()[symbol] = t + BRACKET_GRACE_SECONDS

    async def _flatten(self, symbol: str, reason: str, detail: dict) -> None:
        """Close `symbol` at market, reduce-only, once, and say why loudly."""
        if symbol in self._flattening():
            return
        self._flattening().add(symbol)
        self._bracket_checks().pop(symbol, None)
        await self._event("execution", "POSITION_FLATTENED_UNSAFE", symbol=symbol,
                          severity="CRITICAL",
                          payload={"reason": reason, **detail})
        await self.notifier.send(f"{self.venue} FLATTENING {symbol}: {reason}",
                                 str(detail))
        try:
            self.broker.close_position(symbol, reason)
        except VenueError as exc:
            # Leave it marked so the next poll does not hammer the venue; the
            # event above already says the position needed closing.
            log.critical("could not flatten %s (%s): %s", symbol, reason, exc)
            await self._event("execution", "FLATTEN_FAILED", symbol=symbol,
                              severity="CRITICAL",
                              payload={"reason": reason, "error": str(exc)})

    async def _flatten_if_entry_invalid(self, symbol: str, record, intent) -> bool:
        """True if the fill broke the geometry risk approved, and it was closed.

        The paper broker refuses a fill more than max_entry_deviation R from
        the reference (app/execution/paper_broker._entry_blocked). The venue
        cannot be refused after it has filled, so the live equivalent is to
        close. A fill BEYOND the stop is closed regardless of the reference:
        its stop is on the wrong side, so the venue will not hold it.
        """
        entry = float(record.entry_price)
        side = int(intent["side"])
        stop = float(intent["stop_price"])
        rpu = float(intent.get("risk_per_unit") or 0)
        ref = intent.get("entry_reference")

        beyond = (side > 0 and entry <= stop) or (side < 0 and entry >= stop)
        dev = (abs(entry - float(ref)) / rpu) if (ref is not None and rpu > 0) else None
        limit = float(getattr(self.broker, "max_entry_deviation", 0) or 0)
        too_far = dev is not None and limit > 0 and dev > limit
        if not (beyond or too_far):
            return False

        await self._flatten(symbol, "entry_deviation", {
            "entry_price": entry, "entry_reference": ref, "stop_price": stop,
            "deviation_r": round(dev, 3) if dev is not None else None,
            "limit_r": limit, "beyond_stop": beyond})
        return True

    async def _verify_brackets(self, now: float | None = None) -> None:
        """Flatten any position whose stop-loss is not held at the venue."""
        due = self._bracket_checks()
        if not due or not self.__dict__.get("_polled_once"):
            return
        t = asyncio.get_event_loop().time() if now is None else now
        for symbol, deadline in list(due.items()):
            if t < deadline:
                continue
            if symbol not in self.broker.positions:
                due.pop(symbol, None)          # already closed; nothing to protect
                continue
            try:
                legs = self.broker.protective_legs(symbol)
            except VenueError as exc:
                if t >= deadline + BRACKET_ALERT_SECONDS:
                    await self._event("execution", "PROTECTION_UNVERIFIABLE",
                                      symbol=symbol, severity="CRITICAL",
                                      payload={"error": str(exc)})
                    # Push the deadline on so this alerts periodically rather
                    # than every poll.
                    due[symbol] = t
                continue
            if not legs["stop_loss"]:
                await self._flatten(symbol, "unprotected", {
                    "stop_loss_legs": 0,
                    "take_profit_legs": len(legs["take_profit"])})
                continue

            # A STOP THE VENUE WILL LIQUIDATE BEFORE IS NOT PROTECTION. tnet's
            # ETH short had a stop at 2416.2 and was liquidated at 2415.25; its
            # stop leg existed the whole time. The leg that fires first is the
            # one nearest the entry, so that is the one compared.
            pos = self.broker.positions[symbol]
            liq = float(getattr(pos, "liquidation_price", 0) or 0)
            stops = [float(o.get("stop_price") or 0) for o in legs["stop_loss"]]
            stops = [x for x in stops if x > 0]
            if liq <= 0 or not stops:
                await self._event("execution", "LIQUIDATION_UNVERIFIABLE",
                                  symbol=symbol, severity="WARNING",
                                  payload={"liquidation_price": liq,
                                           "stop_prices": stops})
            else:
                first = max(stops) if pos.side > 0 else min(stops)
                beyond = (pos.side > 0 and first <= liq) or (pos.side < 0 and first >= liq)
                if beyond:
                    await self._flatten(symbol, "stop_beyond_liquidation", {
                        "stop_price": first, "liquidation_price": liq,
                        "side": pos.side})
                    continue
            due.pop(symbol, None)
            if not legs["take_profit"]:
                await self._event("execution", "TAKE_PROFIT_MISSING",
                                  symbol=symbol, severity="WARNING",
                                  payload={"stop_loss_legs": len(legs["stop_loss"])})

    async def _persist_close(self, ev) -> None:
        """Record a close the venue performed, and WHY it performed it.

        The reason cannot come from memory: with exchange-held brackets the
        position closes without this process being involved. It is read off the
        closing order -- see live/ledger.exit_reason.
        """
        from live.ledger import apply_close, close_facts

        symbol = ev.symbol
        open_rows = [p for p in await self.repo.load_open_positions()
                     if p.symbol == symbol]
        if not open_rows:
            log.error("the venue closed %s but no open row exists to close; "
                      "reconciliation will surface this", symbol)
            return
        record = open_rows[0]

        pid = self.product_ids.get(symbol)
        history = self.client.order_history(pid)
        # The most recent order that actually moved size is the one that
        # closed it. Bracket legs are created by the venue and carry no
        # client_order_id of ours, so they cannot be found by id.
        closing = next((o for o in history
                        if str(o.get("state")) == "closed"
                        and float(o.get("average_fill_price") or 0) > 0), None)
        if closing is None:
            log.error("no closing order found for %s; the exit is unrecorded",
                      symbol)
            return

        requested = (ev.payload or {}).get("requested_reason")
        closed = apply_close(record, close_facts(closing, requested=requested))
        await self.repo.update_position(closed)
        self.state.apply_close(closed.realized_pnl or 0.0,
                               closed.closed_at or self.clock.now())
        await self._save_state()
        await self.notifier.send(
            f"{self.venue} closed {symbol} {closed.exit_reason}",
            f"pnl {closed.realized_pnl:+.4f} "
            f"({closed.r_multiple:+.2f}R) equity {self.state.equity:.2f}")

    # -- the loop the paper bot does not need --------------------------------

    async def _poll_loop(self, interval: float = POLL_SECONDS) -> None:
        """Ask the venue what it did, and re-reconcile periodically."""
        since_reconcile = 0.0
        # `_stopping` is an asyncio.Event, not a bool. `while not self._stopping`
        # is permanently False -- an Event has no __bool__, so it is truthy --
        # and the loop would never run one iteration while looking correct.
        while not self._stopping.is_set():
            try:
                for ev in self.broker.poll():
                    log.info("venue event: %s %s %s",
                             ev.kind, ev.symbol, ev.payload)
                    await self._event("broker", ev.kind, symbol=ev.symbol,
                                      payload=ev.payload)
                    # RECORD IT HERE, not in the inherited drain_broker_events:
                    # that one reads PaperFill objects out of a queue the
                    # simulator fills, and nothing fills it here.
                    if ev.kind == "POSITION_OPENED":
                        await self._persist_open(ev)
                    elif ev.kind == "POSITION_CLOSED":
                        await self._persist_close(ev)
                        self._flattening().discard(ev.symbol)
                        self._bracket_checks().pop(ev.symbol, None)
                # Only after a poll has succeeded does broker.positions describe
                # the venue. Before that, "not in positions" means "not looked
                # yet", and a startup-scheduled check would drop the very
                # position it exists to protect.
                self.__dict__["_polled_once"] = True
                await self._verify_brackets()
                since_reconcile += interval
                if since_reconcile >= RECONCILE_SECONDS:
                    since_reconcile = 0.0
                    ledger = await self.repo.load_open_positions()
                    if not await self.reconcile_with_venue(ledger):
                        log.critical("venue diverged while running; halting")
                        self.ready = False
            except VenueError as exc:
                # A read failure is not a reason to stop: the brackets are at
                # the exchange and still protect the position. It IS a reason
                # to say so every time, because a poll loop that has silently
                # stopped seeing fills looks identical to a quiet market.
                log.error("poll failed: %s", exc)
            except Exception:
                log.exception("poll loop error")
            await asyncio.sleep(interval)

    async def run(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_loop())
        try:
            await super().run()
        finally:
            if self._poll_task is not None:
                self._poll_task.cancel()
