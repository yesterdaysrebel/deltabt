"""Risk engine: sizing, every limit, and named rejections.

This is the component the project exists for -- the stated problem was
inconsistent sizing, ignored stops, overtrading and revenge trading, not signal
quality. So every limit gets a test that trips it, and every rejection is
checked for naming the limit rather than just returning False.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config.settings import RiskConfig
from app.config.strategy import StrategyConfig
from app.risk.engine import RiskDecision, RiskEngine, RiskState, _in_session
from app.strategy.explanation import Explanation, Outcome
from deltabt.costs import SymbolCosts

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)
SOL = SymbolCosts(symbol="SOLUSD", tick_size=0.01, contract_value=1.0,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=100.0,
                  position_size_limit=100_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)
COSTS = {"BTCUSD": BTC, "SOLUSD": SOL}
CFG = StrategyConfig()
NOW = 1786560000


def setup(symbol="BTCUSD", direction=1, entry=63_000.0, stop=62_500.0,
          target=64_000.0, outcome=Outcome.DETECTED):
    e = Explanation(symbol=symbol, bar_open=NOW, primary_timeframe="5m",
                    confirmation_timeframe="1m",
                    strategy_version=CFG.version,
                    strategy_config_hash=CFG.config_hash, outcome=outcome,
                    direction=direction)
    e.entry_price, e.stop_price, e.target_price = entry, stop, target
    e.detail["risk_per_unit"] = abs(entry - stop)
    e.detail["idempotency_key"] = "sig1"
    return e


def engine(**over):
    return RiskEngine(replace(RiskConfig(), **over), COSTS,
                      allowed_symbols=("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"))


class FakePos:
    def __init__(self, symbol, notional=1000.0, is_open=True):
        self.symbol, self.notional, self.is_open = symbol, notional, is_open


def approve(eng=None, exp=None, state=None, positions=None, now=NOW,
            live=True) -> RiskDecision:
    return (eng or engine()).evaluate(
        exp or setup(), state or RiskState.fresh(10_000.0),
        open_positions=positions or [], now=now, market_can_trade=live)


# =====================================================================
# SIZING -- section 11
# =====================================================================


class TestSizing:
    def test_risk_amount_is_equity_times_risk_per_trade(self):
        d = approve()
        assert d.approved
        assert d.intent.risk_amount <= 10_000.0 * 0.005 * 1.000001

    def test_quantity_derives_from_risk_over_stop_distance(self):
        # $50 risk / $500 stop = 0.1 BTC = 100 contracts at 0.001 each
        d = approve()
        assert d.intent.quantity == 100

    def test_wider_stop_gives_a_smaller_position(self):
        narrow = approve(exp=setup(stop=62_900.0, target=63_200.0))
        wide = approve(exp=setup(stop=62_000.0, target=65_000.0))
        assert wide.intent.quantity < narrow.intent.quantity

    def test_realised_risk_never_exceeds_budget(self):
        for stop in (62_999.0, 62_950.0, 62_500.0, 61_000.0, 59_000.0):
            e = setup(stop=stop, target=63_000.0 + 2 * (63_000.0 - stop))
            d = approve(exp=e)
            if d.approved:
                assert d.intent.risk_amount <= 10_000.0 * 0.005 * 1.000001, (
                    "integer rounding must round DOWN")

    def test_contracts_are_whole_numbers(self):
        d = approve()
        assert isinstance(d.intent.quantity, int)

    def test_position_that_rounds_to_zero_is_rejected_with_a_reason(self):
        """SOLUSD contracts are 1 SOL; a tiny account cannot buy a fraction."""
        eng = RiskEngine(replace(RiskConfig(), starting_equity=100.0), COSTS)
        e = setup(symbol="SOLUSD", entry=150.0, stop=100.0, target=250.0)
        d = eng.evaluate(e, RiskState.fresh(100.0), open_positions=[], now=NOW)
        assert not d.approved
        assert "zero contracts" in d.reason
        assert e.outcome is Outcome.REJECTED

    def test_every_sizing_input_is_recorded_on_the_explanation(self):
        e = setup()
        d = approve(exp=e)
        assert d.approved
        for field in ("equity", "risk_amount", "quantity", "notional",
                      "estimated_fee", "estimated_slippage"):
            assert getattr(e, field) is not None, field
        assert e.stop_price and e.entry_price and e.target_price


# =====================================================================
# EVERY LIMIT
# =====================================================================


class TestLimits:
    def test_minimum_rr_blocks_a_thin_target(self):
        e = setup(entry=63_000.0, stop=62_500.0, target=63_500.0)   # 1R
        d = approve(exp=e)
        assert not d.approved
        assert d.limit_name == "minimum_rr"
        assert "1.00" in d.reason and "2.00" in d.reason

    def test_minimum_rr_passes_at_exactly_the_threshold(self):
        d = approve(exp=setup(entry=63_000.0, stop=62_500.0, target=64_000.0))
        assert d.approved

    # A STRATEGY THAT TARGETS EXACTLY minimum_rr PUTS EVERY SETUP ON THE
    # BOUNDARY, AND FLOATING POINT DECIDES.
    #
    # The ATR arm sets target = entry +/- 2.0 * risk_per_unit against a
    # minimum_rr of 2.0. The engine recomputes rr = |target - entry| / rpu,
    # and that subtraction cancels most of the significant digits, so the
    # result lands either side of 2.0 by ~1e-14 depending on the price.
    #
    # Observed live on 2026-08-19: 7 of 64 refusals were this, e.g. BTCUSD at
    # rr=1.9999999999850013. Identical setups being coin-flipped, which puts
    # unattributable variance into the forward test -- so these are regression
    # tests for a measurement property, not for a crash.
    @pytest.mark.parametrize("entry,rpu", [
        (65_367.0, 300.878),      # the BTCUSD case from the live log
        (1_940.1, 8.24898),       # ETHUSD
        (78.67, 0.371625),        # SOLUSD
        (0.201, 0.00509656),      # BEATUSD -- small prices, largest relative
        (118_234.5, 447.31),
        (3.14159, 0.0271828),
    ])
    @pytest.mark.parametrize("direction", [1, -1])
    def test_a_target_built_at_exactly_target_r_is_never_refused(
            self, entry, rpu, direction):
        stop = entry - direction * rpu
        target = entry + direction * 2.0 * rpu      # exactly 2R, as built
        d = approve(exp=setup(symbol="BTCUSD", direction=direction,
                              entry=entry, stop=stop, target=target),
                    eng=engine(max_position_notional=10_000_000.0))
        assert d.limit_name != "minimum_rr", (
            f"exactly-2R setup refused as sub-2R: {d.reason}")

    def test_the_tolerance_is_far_below_anything_economically_real(self):
        # 1e-9 must not let a genuinely thin target through. A target short by
        # 0.001R -- five orders of magnitude larger than the float noise it is
        # there to absorb -- must still be refused.
        entry, rpu = 63_000.0, 500.0
        d = approve(exp=setup(entry=entry, stop=entry - rpu,
                              target=entry + (2.0 - 0.001) * rpu))
        assert not d.approved
        assert d.limit_name == "minimum_rr"

    def test_max_open_positions(self):
        d = approve(positions=[FakePos("ETHUSD")])
        assert not d.approved
        assert d.limit_name == "max_open_positions"

    def test_existing_position_in_the_same_symbol_blocks_entry(self):
        d = approve(eng=engine(max_open_positions=5),
                    positions=[FakePos("BTCUSD")])
        assert not d.approved
        assert "already holding" in d.reason

    def test_max_daily_loss(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)          # otherwise the first evaluation resets the day
        s.day_start_equity, s.daily_pnl, s.equity = 10_000.0, -250.0, 9_750.0
        d = approve(state=s)
        assert not d.approved
        assert d.limit_name == "max_daily_loss_pct"
        assert d.observed_value == pytest.approx(0.025)

    def test_max_drawdown(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.peak_equity, s.equity = 12_000.0, 10_000.0     # 16.7% drawdown
        s.day_start_equity = 10_000.0
        d = approve(state=s)
        assert not d.approved
        assert d.limit_name == "max_drawdown_pct"

    def test_max_trades_per_day(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        # Read the limit rather than restate it. It was raised from 6 to 20 on
        # 2026-08-19 and a hardcoded 6 here would have gone green while
        # asserting nothing.
        s.trades_today = RiskConfig().max_trades_per_day
        d = approve(state=s)
        assert not d.approved
        assert d.limit_name == "max_trades_per_day"

    def test_max_consecutive_losses_is_the_revenge_trading_brake(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.consecutive_losses = 3
        d = approve(state=s)
        assert not d.approved
        assert d.limit_name == "max_consecutive_losses"

    def test_a_zero_streak_limit_disables_the_gate(self):
        """0 means "no limit", and the guard is what makes that true.

        `consecutive_losses >= 0` holds for a FRESH state, so comparing against
        a limit of 0 without guarding would reject every signal the bot ever
        evaluated -- a permanent silent halt reached from the opposite side of
        the same bug fixed on 2026-08-14.
        """
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        eng = engine(max_consecutive_losses=0)
        assert approve(eng=eng, state=s).approved, (
            "a fresh state with the gate disabled must still be allowed to trade")
        s.consecutive_losses = 25
        d = approve(eng=eng, state=s)
        assert d.approved, "0 disables the gate at any streak length"

    def test_the_default_streak_limit_still_trips(self):
        """The negative control for the guard: 3 must still mean 3."""
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.consecutive_losses = 3
        assert not approve(state=s).approved

    def test_a_full_drawdown_limit_never_trips(self):
        """1.0 is how the drawdown halt is switched off in paper.

        There is no sentinel: equity would have to reach zero, and at zero
        there is no sizing left to approve anyway.
        """
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.peak_equity, s.equity = 20_000.0, 10_400.0     # 48% drawdown
        s.day_start_equity = 10_400.0
        d = approve(eng=engine(max_drawdown_pct=1.0), state=s)
        assert d.approved, "48% drawdown must pass when the gate is disabled"

    def test_the_default_drawdown_limit_still_trips(self):
        """Negative control: disabling it must be opt-in, not accidental."""
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.peak_equity, s.equity = 20_000.0, 10_400.0
        s.day_start_equity = 10_400.0
        d = approve(state=s)
        assert not d.approved and d.limit_name == "max_drawdown_pct"

    def test_six_slots_allow_six_symbols_but_never_two_in_one(self):
        """Raising max_open_positions must not weaken the per-symbol rule.

        They are separate checks, and the per-symbol one is the application
        half of ux_positions_open_symbol -- the database would reject a second
        open row for a symbol regardless, so an engine that approved it would
        produce an order that could never be recorded.
        """
        eng = engine(max_open_positions=6)
        five = [FakePos(s) for s in
                ("ETHUSD", "SOLUSD", "XRPUSD", "BEATUSD", "BANKUSD")]
        assert approve(eng=eng, positions=five).approved, (
            "a sixth symbol must be allowed when six slots are configured")

        assert not approve(eng=eng, exp=setup(symbol="SOLUSD"),
                           positions=five).approved, (
            "a SECOND position in a symbol already held must still be refused")

        six = five + [FakePos("AKEUSD")]
        d = approve(eng=eng, positions=six)
        assert not d.approved and d.limit_name == "max_open_positions"

    def test_the_streak_clears_on_the_next_utc_day(self):
        """Otherwise the brake never releases.

        FOUND 2026-08-14. consecutive_losses was incremented in apply_close on
        a loss and cleared in exactly ONE place -- apply_close on a win.
        roll_day reset day, day_start_equity, daily_pnl and trades_today and
        left the streak alone. So at the limit every entry was rejected,
        clearing the streak needed a win, and a win needed an entry. The halt
        was permanent and silent: no alert, no halt event, and a daily report
        that said "the setup simply did not occur" every morning forever.
        """
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        for i in range(3):
            s.apply_close(-50.0, NOW + i * 3600)
        assert not approve(state=s).approved

        s.roll_day(NOW + 86_400)
        assert s.consecutive_losses == 0
        assert approve(state=s, now=NOW + 86_400).approved, (
            "a new UTC day must release the brake, or the bot never trades again")

    def test_but_it_does_not_clear_within_the_same_day(self):
        """The negative control: it is a DAILY breaker, not a no-op."""
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        for i in range(3):
            s.apply_close(-50.0, NOW + i * 60)
        s.roll_day(NOW + 3600)                    # same UTC day
        assert s.consecutive_losses == 3
        assert not approve(state=s, now=NOW + 3600).approved

    def test_a_win_still_clears_it_immediately(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.apply_close(-50.0, NOW)
        s.apply_close(-50.0, NOW + 60)
        assert s.consecutive_losses == 2
        s.apply_close(+80.0, NOW + 120)
        assert s.consecutive_losses == 0

    def test_a_week_of_silence_does_not_leave_it_stuck(self):
        """The regression, stated as the symptom an operator would see."""
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        for i in range(5):
            s.apply_close(-50.0, NOW + i * 3600)
        for d in range(1, 8):
            s.roll_day(NOW + d * 86_400)
        assert s.consecutive_losses == 0
        assert approve(state=s, now=NOW + 7 * 86_400).approved

    def test_the_other_daily_counters_still_reset_too(self):
        """Guard against a fix that resets the streak and breaks its siblings."""
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.trades_today = 4
        s.apply_close(-50.0, NOW)
        s.roll_day(NOW + 86_400)
        assert (s.trades_today, s.daily_pnl) == (0, 0.0)
        assert s.day_start_equity == s.equity

    def test_cooldown_after_trade(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.last_trade_at = NOW - 60
        d = approve(state=s)
        assert not d.approved
        assert d.limit_name == "cooldown_after_trade_seconds"
        assert "60s elapsed of 900s" in d.reason

    def test_cooldown_after_loss_is_longer(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.last_trade_at = NOW - 1000        # trade cooldown satisfied
        s.last_loss_at = NOW - 1000         # loss cooldown is 3600
        d = approve(state=s)
        assert not d.approved
        assert d.limit_name == "cooldown_after_loss_seconds"

    def test_cooldowns_expire(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.last_trade_at = NOW - 5000
        s.last_loss_at = NOW - 5000
        assert approve(state=s).approved

    def test_max_position_notional(self):
        d = approve(eng=engine(max_position_notional=10.0))
        assert not d.approved
        assert d.limit_name in ("max_position_notional", "min_contract_size")

    def test_max_total_notional_counts_open_positions(self):
        d = approve(eng=engine(max_open_positions=5, max_total_notional=100.0),
                    positions=[FakePos("ETHUSD", notional=99.0)])
        assert not d.approved
        assert d.limit_name == "max_total_notional"

    def test_max_leverage(self):
        d = approve(eng=engine(max_leverage=0.001))
        assert not d.approved
        assert d.limit_name in ("max_leverage", "min_contract_size")

    def test_symbol_universe_is_enforced(self):
        d = approve(exp=setup(symbol="DOGEUSD"))
        assert not d.approved
        assert "not in the configured universe" in d.reason

    def test_halted_market_blocks_entry(self):
        d = approve(live=False)
        assert not d.approved
        assert "halted" in d.reason

    def test_non_detected_setups_are_not_evaluated(self):
        d = approve(exp=setup(outcome=Outcome.NO_SETUP))
        assert not d.approved
        assert "not a detected setup" in d.reason

    def test_zero_stop_distance_is_rejected(self):
        e = setup(entry=63_000.0, stop=63_000.0, target=64_000.0)
        e.detail["risk_per_unit"] = 0.0
        d = approve(exp=e)
        assert not d.approved
        assert "not positive" in d.reason


# =====================================================================
# THE STRATEGY CANNOT OVERRIDE RISK
# =====================================================================


class TestNotOverridable:
    def test_explanation_fields_cannot_raise_the_risk_fraction(self):
        e = setup()
        e.risk_amount = 9_999.0              # a strategy "asking" for more
        e.quantity = 1_000_000
        d = approve(exp=e)
        assert d.approved
        assert d.intent.risk_amount <= 50.000001
        assert d.intent.quantity == 100

    def test_explanation_reward_risk_is_recomputed_not_trusted(self):
        e = setup(entry=63_000.0, stop=62_500.0, target=63_500.0)
        e.reward_risk = 99.0                 # a strategy claiming a fat RR
        d = approve(exp=e)
        assert not d.approved
        assert d.limit_name == "minimum_rr"

    def test_checks_are_recorded_on_the_intent(self):
        d = approve()
        assert "minimum_rr" in d.intent.checks_passed
        assert "max_daily_loss" in d.intent.checks_passed
        assert "realised_risk_within_budget" in d.intent.checks_passed


# =====================================================================
# STATE BOOKKEEPING
# =====================================================================


class TestRiskState:
    def test_loss_increments_consecutive_losses(self):
        s = RiskState.fresh(10_000.0)
        s.apply_close(-50.0, NOW)
        s.apply_close(-40.0, NOW + 10)
        assert s.consecutive_losses == 2 and s.losses == 2

    def test_a_win_resets_the_streak(self):
        s = RiskState.fresh(10_000.0)
        s.apply_close(-50.0, NOW)
        s.apply_close(+90.0, NOW + 10)
        assert s.consecutive_losses == 0 and s.wins == 1

    def test_peak_equity_only_rises(self):
        s = RiskState.fresh(10_000.0)
        s.apply_close(+500.0, NOW)
        s.apply_close(-300.0, NOW + 10)
        assert s.peak_equity == 10_500.0
        assert s.drawdown_pct == pytest.approx(300.0 / 10_500.0)

    def test_daily_counters_reset_on_a_new_utc_day(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.trades_today, s.daily_pnl = 6, -300.0
        assert s.roll_day(NOW + 86_400) is True
        assert s.trades_today == 0 and s.daily_pnl == 0.0

    def test_daily_counters_survive_within_the_same_day(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.trades_today = 3
        assert s.roll_day(NOW + 3600) is False
        assert s.trades_today == 3

    def test_state_round_trips_through_a_dict(self):
        s = RiskState.fresh(10_000.0)
        s.roll_day(NOW)
        s.apply_close(-50.0, NOW)
        back = RiskState.from_dict(s.to_dict())
        assert back.to_dict() == s.to_dict()

    def test_unknown_keys_are_ignored_on_load(self):
        d = RiskState.fresh(10_000.0).to_dict()
        d["a_field_from_a_future_version"] = 1
        assert RiskState.from_dict(d).equity == 10_000.0


class TestSessions:
    def test_no_sessions_means_always_open(self):
        assert _in_session(NOW, ()) is True

    def test_window_is_respected(self):
        # NOW = 2026-08-12 18:40 UTC
        assert _in_session(NOW, ("18:00-19:00",)) is True
        assert _in_session(NOW, ("13:00-14:00",)) is False

    def test_window_wrapping_midnight(self):
        assert _in_session(NOW, ("18:00-04:00",)) is True
        assert _in_session(NOW, ("22:00-04:00",)) is False

    def test_malformed_window_is_treated_as_closed_not_open(self):
        assert _in_session(NOW, ("garbage",)) is False


class TestTheTimeStop:
    """Nothing but stop or target closed a position before this.

    ExitReason.TIME_EXIT was declared and never emitted, so a target that could
    not be reached held its symbol's slot forever -- and one open position per
    symbol is enforced both in the engine and by ux_positions_open_symbol.
    Measured 2026-08-17: a BTCUSD short opened on 2026-08-14 was 66.9 hours old
    and had refused 75 BTCUSD setups in a single day. The run had stopped
    measuring the strategy and started measuring the strategy with a symbol
    switched off.
    """

    DAY = 86_400

    @staticmethod
    def _broker(max_hold):
        from app.execution.paper_broker import PaperBroker
        return PaperBroker(COSTS, starting_equity=10_000.0,
                           max_hold_seconds=max_hold)

    @staticmethod
    def _pos(opened_at):
        from app.execution.paper_broker import PaperPosition
        return PaperPosition(
            position_uid="p1", signal_key="s1", symbol="BTCUSD", side=1,
            quantity=10, entry_price=63_000.0, stop_price=62_500.0,
            target_price=64_000.0, risk_per_unit=500.0, initial_risk=50.0,
            notional=630.0, equity_before=10_000.0, opened_at=opened_at,
            strategy_version="v")

    def test_a_position_older_than_the_limit_times_out(self):
        b = self._broker(self.DAY)
        p = self._pos(NOW - self.DAY - 1)
        assert b._timed_out(p, NOW)

    def test_a_younger_position_does_not(self):
        b = self._broker(self.DAY)
        assert not b._timed_out(self._pos(NOW - self.DAY + 60), NOW)

    def test_exactly_at_the_limit_times_out(self):
        """>= not >, so a 24h limit means 24h is the most it is ever held."""
        b = self._broker(self.DAY)
        assert b._timed_out(self._pos(NOW - self.DAY), NOW)

    def test_zero_disables_it(self):
        """The default. Every run before 2026-08-17 had no time stop at all."""
        b = self._broker(0)
        assert not b._timed_out(self._pos(NOW - 10 * self.DAY), NOW)

    def test_the_age_survives_a_restart(self):
        """Measured from the RECORDED opened_at, so a recovered position keeps
        its true age rather than restarting the clock."""
        b = self._broker(self.DAY)
        recovered = self._pos(NOW - 67 * 3600)   # the real BTCUSD case
        assert b._timed_out(recovered, NOW)

    def test_it_is_ranked_below_stop_and_target(self):
        """A position that reaches its stop on the same tick must book a
        STOP_LOSS. Ranking the time stop first would rewrite a real exit as an
        administrative one."""
        src = (ROOT_SRC := __import__("pathlib").Path(
            "app/execution/paper_broker.py").read_text())
        for path in ("hit_stop", "hit_target", "_timed_out"):
            assert path in src
        # In both exit paths the time check is the final elif.
        for block in src.split("if hit_stop:")[1:]:
            head = block.split("return")[0]
            assert head.index("hit_target") < head.index("_timed_out"), (
                "the time stop must be checked after stop and target")

