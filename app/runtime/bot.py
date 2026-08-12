"""The bot: startup, recovery, the evaluation loop, and shutdown.

STARTUP ORDER IS LOAD-BEARING (brief section 7)

    1. advisory lock -- before anything else, and exit if another instance
       holds it
    2. database
    3. durable state (equity, daily counters, consecutive losses)
    4. open positions, rehydrated into the broker
    5. historical backfill
    6. halt state primed from that history
    7. indicators warmed
    8. only then READY

Steps 3-4 precede 5-7 deliberately. A bot that starts consuming live data
before it knows what it already holds can open a second position in a symbol it
is already in. Steps 6-7 precede READY because a bot that evaluates before its
indicators have warmed produces signals from NaN, and one that has not primed
its halt state will read a maintenance reopen as a breakout.

RECOVERY IS REPLAY, NOT TRUST

State is rebuilt from the persisted record. Nothing is taken from a cached
snapshot that has not been checked against it. Duplicate work after a restart
is prevented by database constraints -- an in-memory idempotency set is empty
exactly when it is most needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
from dataclasses import asdict

from app.clock import EventTime, MarketClock, wall_now
from app.config.settings import (
    CANDLE_ROLL_GRACE,
    GAP_LOOKBACK,
    MAX_CLOSED_1M_AGE,
    MAX_WS_SILENCE,
    Settings,
)
from app.config.strategy import FROZEN, StrategyConfig
from app.execution.paper_broker import ExitReason, PaperBroker, PaperPosition
from app.market_data.backfill import Backfiller
from app.market_data.candle_builder import CandleBuilder
from app.market_data.delta_ws import DeltaMarketFeed
from app.market_data.market_state import HaltDetector, MarketState
from app.market_data.normalize import (
    NormalizeError,
    normalize_candle,
    normalize_ticker,
)
from app.monitoring.metrics import Metrics
from app.notifications.base import Notifier, NullNotifier
from app.persistence.models import (
    FillRecord,
    InstanceRecord,
    OrderRecord,
    PositionRecord,
    RiskEventRecord,
    SignalRecord,
    SystemEventRecord,
    new_uid,
)
from app.persistence.repository import Repository
from app.risk.engine import RiskEngine, RiskState
from app.strategy.explanation import Outcome
from app.strategy.rules import evaluate, warmup_bars
from deltabt.costs import SymbolCosts

log = logging.getLogger(__name__)

STATE_KEY = "risk_state"


def idempotency_key(symbol: str, bar_open: int, direction, config_hash: str) -> str:
    """Deterministic identity for one evaluation of one closed bar.

    Includes the CONFIG HASH rather than a version label. A hand-maintained
    version string that someone forgets to bump would let two different rule
    sets share a key, and the second evaluation would be silently discarded as
    a duplicate.
    """
    d = {1: "long", -1: "short"}.get(direction, "none")
    blob = f"{symbol}|{bar_open}|{d}|{config_hash}"
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class TradingBot:
    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        costs: dict[str, SymbolCosts],
        *,
        strategy: StrategyConfig = FROZEN,
        notifier: Notifier | None = None,
        backfiller: Backfiller | None = None,
        feed: DeltaMarketFeed | None = None,
        lock=None,
    ) -> None:
        settings.validate()
        strategy.validate()
        self.settings = settings
        self.repo = repo
        self.costs = costs
        self.strategy = strategy
        self.notifier = notifier or NullNotifier()
        self.backfiller = backfiller
        self.lock = lock
        self.metrics = Metrics()

        self.instance_uid = new_uid("bot")
        #: EXCHANGE time. Every market decision -- cooldowns, order expiry,
        #: daily rollover, signal timing -- reads this, never the wall clock.
        #: Audit finding F8: mixing the two made a rejection depend on when the
        #: PROCESS saw a signal rather than when the MARKET produced it, which
        #: meant the run could not be verified by replaying its own record.
        self.clock = MarketClock()
        self.symbols = tuple(settings.symbols)
        self.builder = CandleBuilder(self.symbols)
        self.halts = {s: HaltDetector(s) for s in self.symbols}
        self.broker = PaperBroker(costs, starting_equity=settings.risk.starting_equity,
                                  slippage_bps=settings.risk.slippage_bps)
        self.risk = RiskEngine(settings.risk, costs, allowed_symbols=self.symbols)
        self.state = RiskState.fresh(settings.risk.starting_equity)

        self.feed = feed or DeltaMarketFeed(self.symbols, self.on_message,
                                            url=settings.ws_url)
        #: Instance attributes, not class attributes: a mutable class-level
        #: list would be shared by every bot in the process, which in tests
        #: means one bot's bars leaking into another's evaluation loop.
        self._pending_bars: list = []
        self._pending_events: list = []
        #: Gaps already sent for REST repair, so a hole is not refetched on
        #: every subsequent bar.
        self._repaired_gaps: set[tuple[str, int, int]] = set()
        self.ready = False
        self.recovery_error: str | None = None
        self.started_at = wall_now()
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    # =================================================================
    # STARTUP AND RECOVERY
    # =================================================================

    async def start(self) -> bool:
        if self.lock is not None and not await self.lock.acquire():
            log.error("another instance holds the advisory lock; exiting")
            return False

        await self.repo.connect()
        await self.repo.register_instance(InstanceRecord(
            instance_uid=self.instance_uid, hostname=socket.gethostname(),
            pid=os.getpid(), strategy_version=self.strategy.version,
            strategy_config=self.strategy.to_dict(),
            risk_config=asdict(self.settings.risk), symbols=list(self.symbols)))
        await self._event("bot", "STARTUP", payload={
            "strategy": self.strategy.version, "symbols": list(self.symbols)})

        await self.recover()
        if not self.recovery_error:
            # No point spending a multi-page backfill on a bot that has
            # already decided it must not trade -- and the backfill's own
            # failure message would mask the reconciliation failure that
            # actually matters.
            await self.warm_up()

        if self.recovery_error:
            log.error("refusing to become ready: %s", self.recovery_error)
            return False
        self.ready = True
        await self._event("bot", "READY")
        await self.notifier.send("bot ready",
                                 f"{self.strategy.version} on {', '.join(self.symbols)}")
        return True

    async def recover(self) -> None:
        """Rebuild state from the database. Refuses to proceed on a mismatch."""
        stored = await self.repo.get_state(STATE_KEY)
        if stored:
            self.state = RiskState.from_dict(stored)
            log.info("restored risk state", extra={"equity": self.state.equity,
                                                   "streak": self.state.consecutive_losses})
        self.broker.equity = self.state.equity

        positions = await self.repo.load_open_positions()

        # Reconciliation: the database enforces one open position per symbol,
        # but a duplicate would mean the constraint was bypassed, so it is
        # checked rather than assumed.
        by_symbol: dict[str, int] = {}
        for p in positions:
            by_symbol[p.symbol] = by_symbol.get(p.symbol, 0) + 1
        dupes = [s for s, n in by_symbol.items() if n > 1]
        if dupes:
            self.recovery_error = f"duplicate open positions for {dupes}"
            await self._event("recovery", "RECONCILIATION_FAILED",
                              severity="CRITICAL", payload={"symbols": dupes})
            await self.notifier.send("RECONCILIATION FAILED", self.recovery_error)
            return

        for p in positions:
            if p.symbol not in self.costs:
                self.recovery_error = (
                    f"open position in {p.symbol}, which is not in the "
                    f"configured universe")
                await self._event("recovery", "RECONCILIATION_FAILED",
                                  severity="CRITICAL",
                                  payload={"symbol": p.symbol})
                return
            self.broker.positions[p.position_uid] = _to_broker_position(p)

        log.info("recovered %d open position(s)", len(positions))
        await self._event("recovery", "STATE_RESTORED", payload={
            "open_positions": len(positions), "equity": self.state.equity,
            "trades_today": self.state.trades_today})

    async def warm_up(self) -> None:
        """Backfill, prime halt state, and confirm indicators can warm."""
        if self.backfiller is None:
            self.backfiller = Backfiller()
        need = warmup_bars(self.strategy)
        for sym in self.symbols:
            bars = await self.backfiller.warm_up(sym, self.settings.backfill_days)
            self.builder[sym].ingest_backfill(bars)
            df = self.builder[sym].frame()
            self.halts[sym].prime_from_history(df)
            await self.repo.save_candles(sym, "1m", bars, source="rest")
            five = self.builder[sym].frame_5m()
            log.info("warmed", extra={"symbol": sym, "bars_1m": len(df),
                                      "bars_5m": len(five),
                                      "state": self.halts[sym].state.value})
            if len(five) < need:
                self.recovery_error = (
                    f"{sym}: only {len(five)} closed 5m bars after backfill, "
                    f"need {need} for indicator warm-up")
                return

    # =================================================================
    # MESSAGE HANDLING
    # =================================================================

    def on_message(self, msg: dict) -> None:
        t = msg.get("type")
        try:
            if t == "v2/ticker":
                self._on_tick(normalize_ticker(msg))
            elif t == "candlestick_1m":
                self._on_candle(normalize_candle(msg))
        except NormalizeError as exc:
            self.metrics.bad_messages += 1
            log.warning("unusable message dropped: %s", exc)

    def _on_tick(self, tick) -> None:
        self.metrics.ticks += 1
        if tick.symbol not in self.builder:
            return
        self.clock.observe(tick.ts)
        # Execution advances on every tick, so stops and targets do not wait
        # for a bar close.
        for ev in self.broker.process_market_event(tick):
            self._pending_events.append(ev)

    def _on_candle(self, upd) -> None:
        if upd.symbol not in self.builder:
            return
        for bar in self.builder.ingest(upd):
            self._pending_bars.append(bar)

    # =================================================================
    # THE EVALUATION LOOP
    # =================================================================

    async def on_closed_1m(self, bar) -> None:
        """Handle one closed 1m bar: halt state, persistence, 5m trigger."""
        self.metrics.candles_1m += 1
        # A bar stamped at its OPEN is knowable only once the minute has
        # elapsed, so the exchange instant this bar represents is its close.
        self.clock.observe(bar.start + 60)
        state = self.halts[bar.symbol].observe(bar)
        await self.repo.save_candles(bar.symbol, "1m", [bar], source="ws")

        if state is MarketState.HALTED:
            n = self.broker.suspend(bar.symbol)
            if n:
                await self._event("halt", "POSITIONS_SUSPENDED",
                                  symbol=bar.symbol, severity="WARNING",
                                  payload={"count": n})
                await self.notifier.send(
                    "market halted",
                    f"{bar.symbol}: {n} position(s) suspended, stops inactive")
        elif state is MarketState.LIVE and self.broker.resume(bar.symbol):
            await self._event("halt", "POSITIONS_RESUMED", symbol=bar.symbol)

        await self._repair_gaps(bar.symbol)

        b = self.builder[bar.symbol]
        five, missing = b.closed_5m_for(bar.start)
        if five is None:
            return
        if missing:
            self.metrics.incomplete_5m += 1
            await self._event("candles", "INCOMPLETE_5M", symbol=bar.symbol,
                              severity="WARNING",
                              payload={"bar": five.start, "missing": missing})
            return
        await self.repo.save_candles(bar.symbol, "5m", [five], source="derived")
        await self.on_closed_5m(bar.symbol, five)

    async def _repair_gaps(self, symbol: str) -> None:
        """Refetch missing minutes over REST.

        Every restart leaves one of these by construction: the backfill ends at
        the last bar the REST endpoint served, and the first live-assembled bar
        is a minute or two later. Leaving it unrepaired means /healthz reports a
        gap for the first five minutes of every deploy, and -- worse -- the 5m
        bars spanning the seam are permanently incomplete, so the strategy
        silently declines to evaluate them.
        """
        b = self.builder[symbol]
        if self.backfiller is None:
            return
        for gap in list(b.gaps):
            key = (symbol, gap.expected_start, gap.actual_start)
            if key in self._repaired_gaps:
                continue
            self._repaired_gaps.add(key)
            try:
                bars = await self.backfiller.fill_gap(
                    symbol, gap.expected_start, gap.actual_start)
            except Exception as exc:                       # noqa: BLE001
                log.error("gap repair failed: %s", exc)
                await self._event("candles", "GAP_REPAIR_FAILED", symbol=symbol,
                                  severity="ERROR",
                                  payload={"from": gap.expected_start,
                                           "to": gap.actual_start,
                                           "error": str(exc)})
                continue
            n = b.ingest_backfill(bars)
            if n:
                await self.repo.save_candles(symbol, "1m", bars, source="rest")
            await self._event(
                "candles", "GAP_REPAIRED" if n >= gap.missing else "GAP_PARTIAL",
                symbol=symbol, severity="INFO" if n >= gap.missing else "WARNING",
                payload={"from": gap.expected_start, "to": gap.actual_start,
                         "missing": gap.missing, "recovered": n})
            if n >= gap.missing:
                # Fully repaired: it no longer counts against /healthz.
                b.gaps.remove(gap)

    async def on_closed_5m(self, symbol: str, five) -> None:
        """Evaluate the strategy on one closed primary bar."""
        self.metrics.candles_5m += 1
        b = self.builder[symbol]
        primary = b.frame_5m(limit=self.strategy.window_bars)
        confirmation = b.frame(limit=self.strategy.window_bars)

        exp = evaluate(primary, confirmation, self.strategy, symbol=symbol)
        can_trade = self.halts[symbol].can_trade
        if not can_trade and exp.outcome is Outcome.DETECTED:
            exp.outcome = Outcome.SUPPRESSED
            exp.rejection_reason = (
                f"market state is {self.halts[symbol].state.value}")

        key = idempotency_key(symbol, exp.bar_open, exp.direction,
                              self.strategy.config_hash)
        exp.detail["idempotency_key"] = key

        decision = None
        # The exchange instant this decision belongs to: the close of the bar it
        # was made from. Not the wall clock -- see app/clock.py.
        market_now = exp.bar_open + 300 if exp.bar_open else self.clock.now()
        self.clock.observe(market_now)
        if exp.outcome is Outcome.DETECTED:
            self.metrics.signals_detected += 1
            decision = self.risk.evaluate(
                exp, self.state, open_positions=self.broker.get_positions(),
                now=market_now, market_can_trade=can_trade)
            if not decision.approved:
                self.metrics.signals_rejected += 1
                await self.repo.record_risk_event(RiskEventRecord(
                    event_id=new_uid("risk"), instance_uid=self.instance_uid,
                    symbol=symbol, event_type="REJECTION",
                    reason=decision.reason or "", limit_name=decision.limit_name,
                    limit_value=decision.limit_value,
                    observed_value=decision.observed_value,
                    exchange_ts=market_now, received_ts=wall_now()))

        inserted = await self._record_signal(exp, key, market_now)
        if not inserted:
            self.metrics.duplicate_signals += 1
            log.info("evaluation already recorded; not acting again",
                     extra={"symbol": symbol, "bar": exp.bar_open})
            return

        if exp.outcome is not Outcome.NO_SETUP:
            await self.notifier.send(f"{symbol} {exp.outcome.value}", exp.summary())

        if decision is not None and decision.approved:
            await self._place(exp, decision, market_now)

    async def _place(self, exp, decision, market_now: int) -> None:
        intent = decision.intent
        order = self.broker.submit_order(intent, now=market_now)
        if order is None:
            log.warning("broker declined the intent", extra={"symbol": exp.symbol})
            return
        ok = await self.repo.create_order(OrderRecord(
            order_uid=order.order_uid, idempotency_key=intent.intent_id,
            signal_key=intent.signal_key, instance_uid=self.instance_uid,
            symbol=order.symbol, side=order.side, order_type=order.order_type,
            purpose="entry", quantity=order.quantity,
            limit_price=order.limit_price, status=order.status.value,
            equity_before=intent.equity_before, risk_amount=intent.risk_amount,
            created_exchange_ts=order.created_at,
            expires_exchange_ts=order.created_at + self.broker.entry_ttl_seconds
            if self.broker.entry_ttl_seconds else None,
            received_ts=wall_now()))
        if not ok:
            self.broker.cancel_order(order.order_uid, "duplicate in database")
            log.warning("order already durable; cancelling the in-memory twin")
            return
        self.metrics.orders += 1
        await self._event("execution", "PAPER_ORDER_CREATED", symbol=order.symbol,
                          payload={"order_uid": order.order_uid,
                                   "quantity": order.quantity,
                                   "side": order.side})

    async def _record_signal(self, exp, key: str,
                             market_now: int | None = None) -> bool:
        et = EventTime.at(market_now if market_now is not None
                          else (exp.bar_open + 300 if exp.bar_open else 0))
        return await self.repo.record_signal(SignalRecord(
            idempotency_key=key, instance_uid=self.instance_uid,
            symbol=exp.symbol, bar_open=exp.bar_open,
            primary_timeframe=exp.primary_timeframe,
            confirmation_timeframe=exp.confirmation_timeframe,
            direction=exp.direction, outcome=exp.outcome.value,
            strategy_version=exp.strategy_version,
            strategy_config_hash=exp.strategy_config_hash,
            conditions_passed=exp.conditions_passed,
            conditions_failed=exp.conditions_failed,
            indicators=exp.indicators, entry_price=exp.entry_price,
            stop_price=exp.stop_price, target_price=exp.target_price,
            stop_distance_pct=exp.stop_distance_pct,
            reward_risk=exp.reward_risk, rejection_reason=exp.rejection_reason,
            detail={"risk_amount": exp.risk_amount, "quantity": exp.quantity,
                    "notional": exp.notional, "equity": exp.equity,
                    "estimated_fee": exp.estimated_fee,
                    "estimated_slippage": exp.estimated_slippage,
                    **exp.detail},
            exchange_ts=et.exchange_ts, received_ts=et.received_ts))

    # =================================================================
    # BROKER EVENTS -> PERSISTENCE
    # =================================================================

    async def drain_broker_events(self) -> None:
        events, self._pending_events = self._pending_events, []
        for ev in events:
            if ev.kind == "FILL":
                await self._persist_fill(ev)
            elif ev.kind == "POSITION_OPENED":
                await self._persist_open(ev)
            elif ev.kind == "POSITION_CLOSED":
                await self._persist_close(ev)
            elif ev.kind in ("ORDER_EXPIRED", "ORDER_CANCELLED"):
                await self.repo.update_order_status(
                    ev.payload["order_uid"],
                    "EXPIRED" if ev.kind == "ORDER_EXPIRED" else "CANCELLED")
                self.metrics.orders_expired += 1
                await self._event("execution", ev.kind, symbol=ev.symbol,
                                  severity="WARNING", payload=ev.payload)

    async def _persist_fill(self, ev) -> None:
        p = ev.payload
        costs = self.costs[ev.symbol]
        pos = next((x for x in self.broker.positions.values()
                    if x.symbol == ev.symbol), None)
        qty = pos.quantity if pos else 0
        exch = int(p.get("tick_ts_us") or 0) // 1_000_000 or self.clock.now()
        ok = await self.repo.record_fill(FillRecord(
            fill_uid=p["fill_uid"], order_uid=p["order_uid"],
            instance_uid=self.instance_uid, symbol=ev.symbol,
            side=pos.side if pos else 0, quantity=qty, price=p["price"],
            notional=costs.notional(qty, p["price"]), fee=p["fee"],
            slippage=0.0, liquidity="taker", filled_at=exch,
            tick_ts_us=p.get("tick_ts_us"),
            exchange_ts=exch, received_ts=wall_now()))
        if ok:
            self.metrics.fills += 1
        else:
            log.info("fill already durable; not double-booking")

    async def _persist_open(self, ev) -> None:
        pos = next((x for x in self.broker.positions.values()
                    if x.position_uid == ev.payload["position_uid"]), None)
        if pos is None:
            return
        ok = await self.repo.open_position(_to_record(pos, self.instance_uid))
        if not ok:
            # The database refused it, so a position already exists. The
            # in-memory twin is the wrong one and must go.
            self.broker.positions.pop(pos.position_uid, None)
            await self._event("execution", "DUPLICATE_POSITION_REFUSED",
                              symbol=ev.symbol, severity="CRITICAL",
                              payload=ev.payload)
            return
        self.state.trades_today += 1
        await self._save_state()
        await self.notifier.send(
            f"paper {['', 'LONG', 'SHORT'][pos.side if pos.side > 0 else 2]} "
            f"{pos.symbol}",
            f"entry {pos.entry_price} stop {pos.stop_price} "
            f"target {pos.target_price} qty {pos.quantity}")

    async def _persist_close(self, ev) -> None:
        pos = next((x for x in self.broker.positions.values()
                    if x.position_uid == ev.payload["position_uid"]), None)
        if pos is None:
            return
        rec = _to_record(pos, self.instance_uid)
        await self.repo.update_position(rec)
        self.state.apply_close(pos.realized_pnl or 0.0,
                               pos.closed_at or self.clock.now())
        self.broker.equity = self.state.equity
        await self._save_state()
        self.metrics.closed_positions += 1
        await self.notifier.send(
            f"paper closed {pos.symbol} {pos.exit_reason}",
            f"pnl {pos.realized_pnl:+.2f} ({pos.r_multiple:+.2f}R) "
            f"equity {self.state.equity:.2f}")

    async def _save_state(self) -> None:
        await self.repo.set_state(STATE_KEY, self.state.to_dict())

    async def _event(self, component: str, event_type: str, *, symbol=None,
                     severity="INFO", payload=None) -> None:
        await self.repo.record_system_event(SystemEventRecord(
            event_id=new_uid("evt"), instance_uid=self.instance_uid,
            component=component, event_type=event_type, severity=severity,
            symbol=symbol, payload=payload or {},
            strategy_version=self.strategy.version,
            exchange_ts=self.clock.now() or None, received_ts=wall_now()))

    # =================================================================
    # HEALTH INPUTS
    # =================================================================

    def health_snapshot(self) -> dict:
        # Wall clock, deliberately. "Have WE stopped hearing from the exchange"
        # is a question about our own liveness; answering it from exchange
        # timestamps would be circular, because a dead feed stops advancing
        # exchange time and would look fresh forever.
        now = int(wall_now())
        return {
            "ws_connected": self.feed.stats.connected,
            "seconds_since_ws_message": self.feed.stats.seconds_since_last_message,
            "last_closed_1m": self.builder.last_closed_1m_start,
            "recent_gaps": self.builder.recent_gap_count(
                within_seconds=GAP_LOOKBACK, now=now),
            "strategy_running": self.ready and not self._stopping.is_set(),
            "ready": self.ready,
            "recovery_error": self.recovery_error,
            "open_positions": len(self.broker.get_positions()),
            "equity": self.state.equity,
            "uptime_seconds": wall_now() - self.started_at,
            "market_time": self.clock.now(),
        }

    # =================================================================
    # RUN / STOP
    # =================================================================

    async def run(self) -> None:
        self._tasks = [
            asyncio.create_task(self.feed.run(), name="feed"),
            asyncio.create_task(self._bar_loop(), name="bars"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
        ]
        await self._stopping.wait()

    async def _bar_loop(self, interval: float = 1.0) -> None:
        """Drain closed bars and broker events on the main loop.

        Deliberately not done inside the socket callback: a database round trip
        in the receive path would block the feed and turn a slow write into a
        stale-feed alert.
        """
        while not self._stopping.is_set():
            try:
                # roll_on_clock is an OPERATIONAL fallback for a symbol that
                # printed nothing for a minute, so it reads the wall clock by
                # design. Order expiry below reads MARKET time.
                now = int(wall_now())
                for bar in self.builder.roll_on_clock(now, grace=CANDLE_ROLL_GRACE):
                    self._pending_bars.append(bar)
                bars, self._pending_bars = self._pending_bars, []
                for bar in sorted(bars, key=lambda b: (b.start, b.symbol)):
                    await self.on_closed_1m(bar)
                # Sweep stale entry orders even when no tick has arrived --
                # a silent feed is exactly when they would otherwise pile up.
                self._pending_events.extend(
                    self.broker.expire_stale_entries(self.clock.now()))
                await self.drain_broker_events()
            except Exception:                              # noqa: BLE001
                log.exception("bar loop error")
                await self._event("bot", "LOOP_ERROR", severity="ERROR")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _heartbeat_loop(self, interval: float = 15.0) -> None:
        while not self._stopping.is_set():
            try:
                h = self.health_snapshot()
                await self.repo.heartbeat(
                    self.instance_uid, ws_connected=h["ws_connected"],
                    last_ws_message_at=self.feed.stats.last_message_at or None,
                    last_closed_1m=h["last_closed_1m"],
                    last_closed_5m=None,
                    open_positions=h["open_positions"], equity=h["equity"],
                    detail=self.metrics.as_dict())
            except Exception:                              # noqa: BLE001
                log.exception("heartbeat failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        """Graceful shutdown. Open paper positions are LEFT OPEN.

        Closing them on shutdown would fabricate exits that the strategy never
        produced, and would make every deploy look like a losing trade. They
        are recovered on the next start.
        """
        self._stopping.set()
        self.ready = False
        self.feed.stop()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):    # noqa: BLE001
                pass
        await self._save_state()
        await self._event("bot", "SHUTDOWN", payload={
            "open_positions": len(self.broker.get_positions())})
        await self.repo.stop_instance(self.instance_uid)
        await self.repo.close()
        if self.lock is not None:
            await self.lock.release()


# --- record <-> broker translation -----------------------------------------


def _to_record(p: PaperPosition, instance_uid: str) -> PositionRecord:
    return PositionRecord(
        position_uid=p.position_uid, signal_key=p.signal_key,
        instance_uid=instance_uid, symbol=p.symbol, side=p.side,
        status=p.status, quantity=p.quantity, entry_price=p.entry_price,
        stop_price=p.stop_price, target_price=p.target_price,
        initial_risk=p.initial_risk, risk_per_unit=p.risk_per_unit,
        notional=p.notional, equity_before=p.equity_before,
        opened_at=p.opened_at, strategy_version=p.strategy_version,
        entry_fee=p.entry_fee, exit_fee=p.exit_fee, funding=p.funding,
        exit_price=p.exit_price, realized_pnl=p.realized_pnl,
        r_multiple=p.r_multiple, exit_reason=p.exit_reason,
        closed_at=p.closed_at)


def _to_broker_position(r: PositionRecord) -> PaperPosition:
    return PaperPosition(
        position_uid=r.position_uid, signal_key=r.signal_key, symbol=r.symbol,
        side=r.side, quantity=r.quantity, entry_price=r.entry_price,
        stop_price=r.stop_price, target_price=r.target_price,
        risk_per_unit=r.risk_per_unit, initial_risk=r.initial_risk,
        notional=r.notional, equity_before=r.equity_before,
        opened_at=r.opened_at, strategy_version=r.strategy_version,
        entry_fee=r.entry_fee, exit_fee=r.exit_fee, funding=r.funding,
        status=r.status, exit_price=r.exit_price, exit_reason=r.exit_reason,
        closed_at=r.closed_at, realized_pnl=r.realized_pnl,
        r_multiple=r.r_multiple,
        # A recovered position must be immediately triggerable: the ticks that
        # would have hit its stop while the bot was down are gone, so waiting
        # for a tick "after the entry" would leave it unprotected.
        armed_after_us=None)
