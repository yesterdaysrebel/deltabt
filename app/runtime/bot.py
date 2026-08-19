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
from app.execution.allocation import UnmatchedFill, resolve_position
from app.execution.order_state import OrderStatus
from app.execution.paper_broker import ExitReason, PaperBroker, PaperPosition
from app.forwardtest.identity import (
    EXECUTION_FIELDS,
    execution_params,
    ConfigurationDrift,
    build_identity,
)
from app.market_data.backfill import Backfiller
from app.market_data.candle_builder import CandleBuilder
from app.market_data.delta_ws import DeltaMarketFeed
from app.market_data.market_state import HaltDetector, MarketState, halt_min_run
from app.market_data.normalize import (
    NormalizeError,
    normalize_candle,
    normalize_ticker,
)
from app.monitoring.health import json_safe
from app.monitoring.metrics import Metrics
from app.notifications.base import Notifier, NullNotifier
from app.persistence.models import (
    FillRecord,
    FundingEventRecord,
    QuarantinedFillRecord,
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
from app.strategy.atr_arm import AtrArmConfig, evaluate_atr
from app.strategy.atr_arm import warmup_bars as atr_warmup_bars
from app.strategy.frozen_hwpr import FrozenHwprConfig, evaluate_frozen
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
        #: Which evaluator and which bar boundary this process uses. Decided
        #: once from the resolved config rather than re-tested per bar, so the
        #: two arms cannot interleave.
        self.frozen_arm = isinstance(strategy, FrozenHwprConfig)
        #: The ATR arm is 5m-primary like V3, so it shares the 5m boundary and
        #: differs only in which evaluator runs on it.
        self.atr_arm = isinstance(strategy, AtrArmConfig)
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
        self.halts = {s: HaltDetector(s, min_run=halt_min_run(s))
                      for s in self.symbols}
        self.broker = PaperBroker(costs, starting_equity=settings.risk.starting_equity,
                                  slippage_bps=settings.risk.slippage_bps,
                                  max_hold_seconds=settings.risk.max_hold_seconds)
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
        #: Latest funding rate (PERCENT per interval) and mark price seen on
        #: v2/ticker, per symbol. Delta publishes no "funding applied" event,
        #: so a settlement is charged at the last rate observed before it --
        #: an approximation, recorded as such on the ledger row.
        self._funding_rate: dict[str, float] = {}
        self._last_mark: dict[str, float] = {}
        self.ready = False
        self.recovery_error: str | None = None
        #: True once recover() has loaded the persisted risk state into
        #: self.state. Until then self.state is a FRESH RiskState, and writing
        #: that back on shutdown would destroy the real one -- see stop().
        self._state_loaded = False
        #: Set once an experiment is bound. Every decision row is stamped with
        #: it so a run can never be silently mixed with another.
        self.experiment_id: str | None = None
        self.identity = None
        self.started_at = wall_now()
        #: Wall time of the last bar-loop pass. Health reads it to tell a
        #: working loop from a dead one; a flag cannot do that.
        self.last_bar_loop_at = wall_now()
        self.loop_errors_consecutive = 0
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

        # FAIL CLOSED before anything else touches the market. If the running
        # configuration does not match the experiment already in the database,
        # the bot must not trade: adopting the new configuration would silently
        # make the second half of a 30-day run a different experiment from the
        # first (audit F5).
        if not await self.bind_experiment():
            return False

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

    def current_identity(self, experiment_id: str):
        """Identity of the configuration this process is actually running."""
        return build_identity(
            experiment_id, self.strategy, self.settings.risk,
            execution_params(
                {f: getattr(self.broker, f, None) if f != "slippage_bps"
                 else self.settings.risk.slippage_bps for f in EXECUTION_FIELDS},
                self.symbols),
            self.symbols)

    async def bind_experiment(self) -> bool:
        """Attach to the active experiment, or refuse to run.

        Three outcomes, and only the first two allow trading:
          * no active experiment -> run unbound (development), and say so;
          * active experiment whose configuration matches -> bind;
          * active experiment whose configuration has MOVED -> refuse.
        """
        active = await self.repo.active_experiment()
        if active is None:
            log.warning("no active forward-test experiment; running unbound. "
                        "Decisions will not carry an experiment id.")
            return True

        exp_id = active["experiment_id"]
        mine = self.current_identity(exp_id)
        recorded = _identity_from_row(active)
        diffs = mine.differences(recorded)
        if diffs:
            drift = ConfigurationDrift(exp_id, diffs)
            self.recovery_error = str(drift)
            log.error("%s", drift)
            await self._event("forwardtest", "CONFIG_DRIFT_REFUSED",
                              severity="CRITICAL",
                              payload={"experiment_id": exp_id,
                                       "differences": diffs})
            await self.notifier.send("CONFIGURATION DRIFT -- refusing to start",
                                     str(drift), severity="CRITICAL")
            return False

        self.experiment_id = exp_id
        self.identity = mine
        log.info("bound to experiment", extra={
            "experiment_id": exp_id, "config_hash": mine.config_hash,
            "git_sha": mine.git_sha})
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
            pos = _to_broker_position(p)
            # Rebuild the funding watermark from the LEDGER, not from the
            # position row. Without this a restart re-walks every settlement
            # since the position opened; each would be refused as a duplicate,
            # but the work and the log noise are avoidable and the intent
            # should be explicit.
            charged = await self.repo.funding_for_position(p.position_uid)
            if charged:
                stamps = [c["exchange_ts"] if isinstance(c, dict) else c.exchange_ts
                          for c in charged]
                stamps = [int(t.timestamp()) if hasattr(t, "timestamp") else int(t)
                          for t in stamps]
                pos.funding_checked_through = max(stamps)
                self.broker.mark_funding_charged(
                    c["event_id"] if isinstance(c, dict) else c.event_id
                    for c in charged)
                pos.funding = sum(
                    float(c["funding_amount"] if isinstance(c, dict)
                          else c.funding_amount) for c in charged)
            self.broker.positions[p.position_uid] = pos

        # Only now is self.state the real one. Both early returns above leave
        # it fresh on purpose, and stop() must not write those back.
        self._state_loaded = True
        log.info("recovered %d open position(s)", len(positions))
        await self._event("recovery", "STATE_RESTORED", payload={
            "open_positions": len(positions), "equity": self.state.equity,
            "trades_today": self.state.trades_today})

    async def warm_up(self) -> None:
        """Backfill, prime halt state, and confirm indicators can warm."""
        if self.backfiller is None:
            self.backfiller = Backfiller()
        # The frozen arm decides on 1m and needs its whole window before the
        # 5m regime inside build_conditions has converged; V3 needs 5m bars.
        need = (self.strategy.window_bars if self.frozen_arm
                else atr_warmup_bars(self.strategy) if self.atr_arm
                else warmup_bars(self.strategy))
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
            have = len(df) if self.frozen_arm else len(five)
            unit = "1m" if self.frozen_arm else "5m"
            if have < need:
                self.recovery_error = (
                    f"{sym}: only {have} closed {unit} bars after backfill, "
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
        if tick.funding_rate is not None:
            self._funding_rate[tick.symbol] = tick.funding_rate
        self._last_mark[tick.symbol] = tick.mark
        self._settle_funding(tick.symbol, tick.ts)
        # Execution advances on every tick, so stops and targets do not wait
        # for a bar close.
        for ev in self.broker.process_market_event(tick):
            self._pending_events.append(ev)

    def _settle_funding(self, symbol: str, now: int) -> None:
        """Charge any settlement market time has just passed.

        Driven from the tick path so a settlement is charged at the instant it
        is crossed, not deferred to the close -- which is what "snapshot, not
        pro-rata" means.
        """
        rate = self._funding_rate.get(symbol)
        mark = self._last_mark.get(symbol)
        if rate is None or mark is None:
            return
        spec = self.costs.get(symbol)
        if spec is None:
            return
        evs = self.broker.settle_funding(
            symbol, now, rate_percent=rate, mark_price=mark,
            interval=spec.funding_interval_seconds)
        self._pending_events.extend(evs)

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

        # THE FROZEN 1m ARM DECIDES HERE AND RETURNS. V3's block below is not
        # reached, not modified, and not made conditional on anything.
        if self.frozen_arm:
            await self.on_closed_1m_frozen(bar.symbol)
            return

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

    async def on_closed_1m_frozen(self, symbol: str) -> None:
        """Evaluate the frozen H-WPR-1 arm on one closed 1m bar.

        The 1m bar IS the decision bar here -- 5m enters only inside
        `evaluate_frozen`, as a confirmed regime filter derived by the frozen
        research module itself. Nothing about the rule set is recomputed in this
        layer; it consumes the evaluator's Explanation and hands it to the same
        risk and execution pipeline V3 uses.
        """
        self.metrics.candles_5m += 0     # 5m is not a decision boundary here
        one_minute = self.builder[symbol].frame(limit=self.strategy.window_bars)
        exp = evaluate_frozen(one_minute, self.strategy, symbol=symbol,
                              max_stop_pct=self.strategy.max_stop_pct)
        await self._process_explanation(symbol, exp, 60)

    async def on_closed_5m(self, symbol: str, five) -> None:
        """Evaluate the strategy on one closed primary bar."""
        self.metrics.candles_5m += 1
        b = self.builder[symbol]
        primary = b.frame_5m(limit=self.strategy.window_bars)
        confirmation = b.frame(limit=self.strategy.window_bars)

        if self.atr_arm:
            exp = evaluate_atr(primary, confirmation, self.strategy, symbol=symbol)
        else:
            exp = evaluate(primary, confirmation, self.strategy, symbol=symbol)
        await self._process_explanation(symbol, exp, 300)

    async def _process_explanation(self, symbol: str, exp, bar_seconds: int) -> None:
        """Everything after an evaluation, for any evaluator.

        EXTRACTED, NOT REWRITTEN. This is `on_closed_5m`'s own tail moved
        verbatim so the 1m frozen arm cannot drift from it. The only
        generalisation is `bar_seconds`, which was the literal 300: it is the
        length of the bar the decision was made from, so a 1m evaluation stamps
        its decision 60 seconds after the bar opened rather than 300.

        V3 reaches this by the identical path it always did.
        """
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
        market_now = (exp.bar_open + bar_seconds if exp.bar_open
                      else self.clock.now())
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
        """Reserve exposure in the DATABASE, then create the paper order.

        BUG 1. The risk engine's max_open_positions gate counted POSITIONS.
        Between approving BTCUSD and evaluating ETHUSD -- 1.1 seconds, same 5m
        bar -- the first had an approved ORDER and no position yet, so the gate
        saw zero and approved a second entry against a limit of one.

        The order row is now the reservation: counted and inserted inside one
        transaction, serialised by a transaction-scoped advisory lock. There is
        no window between deciding and reserving because they are the same
        operation.
        """
        intent = decision.intent
        order_uid = new_uid("ord")
        ttl = self.broker.entry_ttl_seconds
        record = OrderRecord(
            order_uid=order_uid, idempotency_key=intent.intent_id,
            signal_key=intent.signal_key, instance_uid=self.instance_uid,
            symbol=intent.symbol, side=intent.side,
            order_type=intent.order_type, purpose="entry",
            quantity=intent.quantity, limit_price=intent.limit_price,
            status=OrderStatus.WORKING.value,
            equity_before=intent.equity_before, risk_amount=intent.risk_amount,
            created_exchange_ts=market_now,
            expires_exchange_ts=(market_now + ttl) if ttl else None,
            requested_price=intent.entry_reference, received_ts=wall_now())

        reserved = await self.repo.reserve_entry_slot(
            record, self.settings.risk.max_open_positions)
        if not reserved:
            exposure = await self.repo.effective_exposure()
            reason = (f"max_open_positions {self.settings.risk.max_open_positions} "
                      f"already reserved (effective exposure {exposure}: open "
                      f"positions plus pending entry orders)")
            exp.outcome = Outcome.REJECTED
            exp.rejection_reason = reason
            self.metrics.reservations_refused += 1
            await self.repo.record_risk_event(RiskEventRecord(
                event_id=new_uid("risk"), instance_uid=self.instance_uid,
                symbol=exp.symbol, event_type="LIMIT_BREACH",
                reason=reason, limit_name="max_open_positions",
                limit_value=self.settings.risk.max_open_positions,
                observed_value=exposure, exchange_ts=market_now,
                received_ts=wall_now()))
            log.info("entry refused at the reservation gate",
                     extra={"symbol": exp.symbol, "exposure": exposure})
            return

        order = self.broker.submit_order(intent, now=market_now,
                                         order_uid=order_uid)
        if order is None:
            # The broker declined after the slot was reserved, so the
            # reservation must be released or it would block every future
            # entry for the rest of the run.
            await self.repo.update_order_status(
                order_uid, OrderStatus.CANCELLED.value)
            log.warning("broker declined after reservation; slot released",
                        extra={"symbol": exp.symbol})
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
            experiment_id=self.experiment_id,
            config_hash=self.identity.config_hash if self.identity else None,
            git_sha=self.identity.git_sha if self.identity else None,
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
            if ev.kind == "EXIT_ORDER_CREATED":
                # MUST precede its fill: paper_fills.order_uid is a foreign key
                # into paper_orders, and the whole of audit F3 was that exits
                # had no parent row to point at.
                await self._persist_exit_order(ev)
            elif ev.kind == "FILL":
                await self._persist_fill(ev)
            elif ev.kind == "POSITION_OPENED":
                await self._persist_open(ev)
            elif ev.kind == "POSITION_CLOSED":
                await self._persist_close(ev)
            elif ev.kind == "ORDER_RESIZED":
                # The broker cut the size to hold the risk budget. Persist the
                # amendment, or the order row keeps claiming the approved
                # quantity and its full fill is then mis-derived as PARTIAL.
                await self.repo.resize_order(ev.payload["order_uid"],
                                             int(ev.payload["filled"]))
                await self._event("execution", "ORDER_RESIZED",
                                  symbol=ev.symbol, payload=ev.payload)
            elif ev.kind == "FUNDING":
                await self._persist_funding(ev)
            elif ev.kind in ("ORDER_EXPIRED", "ORDER_CANCELLED"):
                await self.repo.update_order_status(
                    ev.payload["order_uid"],
                    "EXPIRED" if ev.kind == "ORDER_EXPIRED" else "CANCELLED")
                self.metrics.orders_expired += 1
                await self._event("execution", ev.kind, symbol=ev.symbol,
                                  severity="WARNING", payload=ev.payload)

    async def _persist_exit_order(self, ev) -> None:
        """Persist the order that closes a position, before its fill.

        Its uid is deterministic ("{position_uid}:exit"), so a replayed close
        is refused by the unique constraint rather than creating a second exit.
        """
        p = ev.payload
        created = await self.repo.create_order(OrderRecord(
            order_uid=p["order_uid"], idempotency_key=p["idempotency_key"],
            signal_key=p["signal_key"], instance_uid=self.instance_uid,
            symbol=p["symbol"], side=p["side"], order_type=p["order_type"],
            purpose=p["purpose"], quantity=p["quantity"],
            limit_price=p.get("limit_price"), status=p["status"],
            equity_before=self.state.equity, risk_amount=0.0,
            created_exchange_ts=p.get("created_exchange_ts"),
            filled_exchange_ts=p.get("filled_exchange_ts"),
            requested_price=p.get("requested_price"),
            filled_price=p.get("filled_price"),
            position_uid=p.get("position_uid"),
            received_ts=wall_now(), event_type="EXIT_ORDER_CREATED"))
        if created:
            self.metrics.exit_orders += 1
        await self._event("execution", "EXIT_ORDER_CREATED",
                          symbol=p["symbol"],
                          payload={"order_uid": p["order_uid"],
                                   "purpose": p["purpose"],
                                   "duplicate": not created})

    async def _persist_fill(self, ev) -> None:
        """Persist a fill exactly as the broker reported it.

        AUDIT F1. This used to reconstruct side and quantity by scanning for
        the first position matching the symbol. Closed positions are never
        removed from the broker's map, so from the second trade in a symbol
        onward it copied a CLOSED position's side -- a short was recorded as a
        long, and the dataset is the deliverable.

        Nothing is reconstructed now. The broker states the association; an
        association that cannot be verified is QUARANTINED rather than guessed.
        """
        p = ev.payload
        exch = int(p.get("tick_ts_us") or 0) // 1_000_000 or self.clock.now()
        try:
            position_uid = resolve_position(p, self.broker.positions)
        except UnmatchedFill as exc:
            await self._quarantine_fill(exc, exch)
            return

        ok = await self.repo.record_fill(FillRecord(
            fill_uid=p["fill_uid"], order_uid=p["order_uid"],
            position_uid=position_uid, seq=int(p.get("seq", 1)),
            purpose=p.get("purpose", "entry"),
            instance_uid=self.instance_uid, symbol=p["symbol"],
            side=int(p["side"]), quantity=int(p["quantity"]),
            price=float(p["price"]),
            notional=float(p["notional"]), fee=float(p["fee"]),
            slippage=float(p.get("slippage", 0.0)),
            liquidity=p.get("liquidity", "taker"),
            filled_at=int(p.get("filled_at") or exch),
            tick_ts_us=p.get("tick_ts_us"),
            exchange_ts=exch, received_ts=wall_now()))
        if ok:
            self.metrics.fills += 1
        else:
            # Not an error: the deterministic fill id did its job on a replay.
            log.info("fill already durable; not double-booking",
                     extra={"fill_uid": p["fill_uid"]})

    async def _quarantine_fill(self, exc: UnmatchedFill, exchange_ts: int) -> None:
        """A fill we cannot place. Recorded loudly, never attached to a guess."""
        self.metrics.fills_quarantined += 1
        payload = exc.payload
        await self.repo.quarantine_fill(QuarantinedFillRecord(
            quarantine_uid=new_uid("qfill"), instance_uid=self.instance_uid,
            reason=exc.reason, payload=dict(payload),
            symbol=payload.get("symbol"), order_uid=payload.get("order_uid"),
            position_uid=payload.get("position_uid"),
            exchange_ts=exchange_ts, received_ts=wall_now()))
        await self._event("execution", "FILL_QUARANTINED",
                          symbol=payload.get("symbol"), severity="CRITICAL",
                          payload={"reason": exc.reason, **payload})
        await self.notifier.send("FILL QUARANTINED", exc.reason,
                                 severity="CRITICAL")
        log.error("quarantined an unmatched fill", extra={"reason": exc.reason})

    async def _persist_funding(self, ev) -> None:
        """Write one settlement to the ledger.

        The event id is deterministic, so a restart across a settlement redoes
        the work and this becomes a no-op rather than a double charge.
        """
        p = ev.payload
        first = await self.repo.record_funding(FundingEventRecord(
            event_id=p["event_id"], instance_uid=self.instance_uid,
            position_uid=p["position_uid"], symbol=p["symbol"],
            side=p["side"], quantity=p["quantity"],
            exchange_ts=p["exchange_ts"], funding_rate=p["funding_rate"],
            mark_price=p["mark_price"], notional=p["notional"],
            funding_amount=p["funding_amount"],
            interval_seconds=p["interval_seconds"],
            rate_source=p["rate_source"], received_ts=wall_now()))
        if not first:
            # Already in the ledger. Nothing to undo: the broker refuses to
            # apply a settlement twice, so equity is already correct. Undoing
            # here would be wrong whenever the replayed event is a stale one
            # the broker did not just apply.
            log.info("funding already charged; not double-booking",
                     extra={"event_id": p["event_id"]})
            return
        self.metrics.funding_events += 1
        await self._event("funding", "FUNDING_SETTLED", symbol=p["symbol"],
                          payload=p)

    async def _persist_open(self, ev) -> None:
        pos = next((x for x in self.broker.positions.values()
                    if x.position_uid == ev.payload["position_uid"]), None)
        if pos is None:
            return
        ok = await self.repo.open_position(_to_record(
            pos, self.instance_uid, self.experiment_id,
            self.identity.config_hash if self.identity else None))
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
        rec = _to_record(
            pos, self.instance_uid, self.experiment_id,
            self.identity.config_hash if self.identity else None)
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
            "last_closed_1m_by_symbol": self.builder.last_closed_1m_by_symbol(),
            "recent_gaps": self.builder.recent_gap_count(
                within_seconds=GAP_LOOKBACK, now=now),
            "strategy_running": self.ready and not self._stopping.is_set(),
            "seconds_since_bar_loop": max(0.0, now - self.last_bar_loop_at),
            "loop_errors_consecutive": self.loop_errors_consecutive,
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

        THIS LOOP MUST NOT BE ABLE TO DIE. Found on a VPS deployment rehearsal:
        a transient DNS failure made a database write fail, the `except` handler
        logged and then called _event() -- which writes to the same unreachable
        database -- so the handler itself raised, the exception escaped the
        `while`, and the task ended. The process stayed up, the feed kept
        running, the candle builder kept ingesting, and NOTHING was evaluated,
        persisted or executed again. /healthz reported the only problem as
        "gaps", because every other signal it reads is updated by the socket
        callback rather than by this loop.

        So: the handler does no unguarded I/O, unprocessed bars are put BACK on
        the queue instead of being dropped, and every pass stamps a heartbeat
        that health checks against.
        """
        while not self._stopping.is_set():
            bars: list = []
            try:
                now = int(wall_now())
                # roll_on_clock is an OPERATIONAL fallback for a symbol that
                # printed nothing for a minute, so it reads the wall clock by
                # design. Order expiry below reads MARKET time.
                for bar in self.builder.roll_on_clock(now, grace=CANDLE_ROLL_GRACE):
                    self._pending_bars.append(bar)
                bars, self._pending_bars = self._pending_bars, []
                bars.sort(key=lambda b: (b.start, b.symbol))
                while bars:
                    await self.on_closed_1m(bars[0])
                    bars.pop(0)          # only once it is safely handled
                # Sweep stale entry orders even when no tick has arrived --
                # a silent feed is exactly when they would otherwise pile up.
                self._pending_events.extend(
                    self.broker.expire_stale_entries(self.clock.now()))
                await self.drain_broker_events()
                self.loop_errors_consecutive = 0
            except Exception:                              # noqa: BLE001
                # Whatever was not processed goes back on the queue rather than
                # vanishing. A bar dropped here is a bar the audit trail never
                # sees.
                self._pending_bars = bars + self._pending_bars
                self.loop_errors_consecutive += 1
                self.metrics.loop_errors += 1
                log.exception("bar loop error (consecutive=%d)",
                              self.loop_errors_consecutive)
                # NO unguarded I/O in the handler. Recording the incident is
                # best-effort; failing to record it must never kill the loop.
                try:
                    await self._event("bot", "LOOP_ERROR", severity="ERROR",
                                      payload={"consecutive":
                                               self.loop_errors_consecutive})
                except Exception:                          # noqa: BLE001
                    log.warning("could not persist the loop error either")
            finally:
                # Stamped even on failure: the loop is alive and trying, which
                # is what the health check needs to distinguish from dead.
                self.last_bar_loop_at = wall_now()
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    #: How often the heartbeat also says something in the LOG, not just the
    #: database. See the note in _heartbeat_loop.
    LOG_HEARTBEAT_EVERY = 20        # 20 x 15s = every 5 minutes

    async def _heartbeat_loop(self, interval: float = 15.0) -> None:
        beat = 0
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

                # A LIVENESS LINE IN THE LOG, periodically.
                #
                # The `bot-silent` CloudWatch alarm is the only thing that
                # catches a dead evaluation loop -- a dead loop logs no errors,
                # so error-count alarms stay green through exactly that
                # failure. Its premise is "the bot evaluates every symbol every
                # bar, so silence means it is not running".
                #
                # That premise was false. A quiet market produces NO_SETUP
                # evaluations, which are persisted but not logged, so a
                # perfectly healthy bot went 41 minutes without writing a
                # single line and the alarm fired. An alarm that cries wolf
                # during normal operation is one an operator learns to ignore,
                # which is worse than not having it.
                #
                # The heartbeat already runs every 15s and already knows
                # everything worth saying. Saying it out loud every 5 minutes
                # makes the alarm's premise true, and gives the log a pulse to
                # read during an incident.
                beat += 1
                if beat % self.LOG_HEARTBEAT_EVERY == 1:
                    # json_safe, not `or -1`: "no websocket message yet" is
                    # naturally +inf, inf is TRUTHY so the fallback never
                    # fires, and round(inf, 1) is still inf. json.dumps then
                    # writes a bare `Infinity`, which Python round-trips but
                    # which is not JSON any other reader accepts -- including
                    # the CloudWatch metric filters that watch this log.
                    silence = json_safe(h.get("seconds_since_ws_message"))
                    log.info("heartbeat", extra={
                        "ws_connected": h["ws_connected"],
                        "seconds_since_ws_message": (
                            round(silence, 1) if silence is not None else None),
                        "last_closed_1m": h["last_closed_1m"],
                        "recent_gaps": h["recent_gaps"],
                        "open_positions": h["open_positions"],
                        "equity": h["equity"],
                        "signals": self.metrics.signals_detected,
                        "orders": self.metrics.orders,
                        "fills": self.metrics.fills,
                        "loop_errors": self.metrics.loop_errors,
                        "uptime_seconds": int(h.get("uptime_seconds") or 0),
                    })
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
        # NEVER persist a state that was never loaded. A startup that refuses
        # -- configuration drift, a lost advisory lock, a failed reconciliation
        # -- returns before recover() runs, so self.state is still
        # RiskState.fresh(starting_equity). Saving it here overwrote the real
        # row with a blank one, and that is not hypothetical: the deploy at
        # 2026-08-14 10:22:55 refused on config drift, rolled itself back, and
        # on the way out reset equity, peak_equity, trades_today, the
        # consecutive-loss counter and the drawdown baseline to zero.
        #
        # The fail-closed path existed to protect the experiment and was
        # quietly damaging it instead. A refusing bot must leave the database
        # exactly as it found it.
        if self._state_loaded:
            await self._save_state()
        else:
            log.warning("not persisting risk state: it was never loaded, so "
                        "what is in memory is a fresh state, not the run's")
        await self._event("bot", "SHUTDOWN", payload={
            "open_positions": len(self.broker.get_positions())})
        await self.repo.stop_instance(self.instance_uid)
        await self.repo.close()
        if self.lock is not None:
            await self.lock.release()


# --- record <-> broker translation -----------------------------------------


def _identity_from_row(row: dict):
    """Rebuild the recorded identity so it can be compared field by field."""
    from app.forwardtest.identity import ExperimentIdentity
    return ExperimentIdentity(
        experiment_id=row["experiment_id"], strategy_hash=row["strategy_hash"],
        risk_hash=row["risk_hash"], execution_hash=row["execution_hash"],
        config_hash=row["config_hash"], git_sha=row["git_sha"],
        git_dirty=bool(row.get("git_dirty")), app_version=row["app_version"],
        strategy_version=row["strategy_version"],
        symbols=tuple(row["symbols"]), snapshot=row.get("snapshot") or {})


def _to_record(p: PaperPosition, instance_uid: str,
               experiment_id: str | None = None,
               config_hash: str | None = None) -> PositionRecord:
    return PositionRecord(
        position_uid=p.position_uid, signal_key=p.signal_key,
        instance_uid=instance_uid,
        # Both callers already passed these; the record simply never took them,
        # so positions.experiment_id and positions.config_hash were NULL for
        # every trade ever recorded. The column's own comment says "stamped so
        # a position can never be silently attributed to the wrong run", which
        # is exactly what was happening.
        experiment_id=experiment_id, config_hash=config_hash,
        symbol=p.symbol, side=p.side,
        status=p.status, quantity=p.quantity, entry_price=p.entry_price,
        stop_price=p.stop_price, target_price=p.target_price,
        initial_risk=p.initial_risk, risk_per_unit=p.risk_per_unit,
        notional=p.notional, equity_before=p.equity_before,
        opened_at=p.opened_at, strategy_version=p.strategy_version,
        entry_fee=p.entry_fee, exit_fee=p.exit_fee, funding=p.funding,
        exit_price=p.exit_price, realized_pnl=p.realized_pnl,
        r_multiple=p.r_multiple, exit_reason=p.exit_reason,
        closed_at=p.closed_at,
        # DROPPED HERE, EXACTLY LIKE experiment_id AND config_hash WERE.
        #
        # The broker computes every one of these -- planned_r and fill_rr at
        # fill time, the slippages on each side, gross_pnl on close -- and this
        # converter simply did not carry them, so the INSERT and the UPDATE
        # both wrote NULL. Every closed position on every run has had them
        # empty, which is why the report's cost table could only ever show a
        # dash for the two that matter most.
        #
        # schema.sql on planned_r and fill_rr: "They differ by entry slippage,
        # and reporting only one hides the degradation the forward test exists
        # to measure." Neither was ever recorded, so the degradation was not
        # measured at all.
        requested_entry=p.requested_entry,
        planned_r=p.planned_r, fill_rr=p.fill_rr,
        entry_slippage=p.entry_slippage, exit_slippage=p.exit_slippage,
        gross_pnl=p.gross_pnl,
        # Not a broker field: derived, and only once the position is closed.
        hold_seconds=(int(p.closed_at - p.opened_at)
                      if p.closed_at and p.opened_at else None))


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
        r_multiple=r.r_multiple, requested_entry=r.requested_entry,
        planned_r=r.planned_r, fill_rr=r.fill_rr,
        entry_slippage=r.entry_slippage, exit_slippage=r.exit_slippage,
        gross_pnl=r.gross_pnl,
        # A recovered position must be immediately triggerable: the ticks that
        # would have hit its stop while the bot was down are gone, so waiting
        # for a tick "after the entry" would leave it unprotected.
        armed_after_us=None,
        # Funding already charged is in the ledger; the watermark is rebuilt
        # from it during recovery so a restart cannot re-charge the past.
        funding_checked_through=r.opened_at)
