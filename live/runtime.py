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
        self.broker = LiveBroker(
            client, product_ids=self.product_ids,
            experiment_id=getattr(self, "experiment_id", "") or "unbound",
            tick_size=tick_size or {},
            kill_switch_path=kill_switch_path)
        self._symbol_for = {v: k for k, v in self.product_ids.items()}
        self._poll_task: asyncio.Task | None = None

    # -- startup -------------------------------------------------------------

    async def start(self) -> bool:
        """Refuse mainnet outright if the circuit breakers are switched off.

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

        self._state_loaded = True
        await self._event("recovery", "STATE_RESTORED", payload={
            "open_positions": len(positions), "equity": self.state.equity})

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

        ours = [{"symbol": p.symbol,
                 "size": (1 if str(getattr(p, "side", "")).upper()
                          in {"LONG", "BUY", "1"} else -1) * int(p.contracts)}
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
