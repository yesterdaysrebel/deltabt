"""The drawdown breaker must not be a permanent SILENT halt.

THE DEFECT THESE TESTS PIN
    ``drawdown_pct`` is ``(peak_equity - equity) / peak_equity`` and
    ``peak_equity`` only moves in ``apply_close``. So a breach reached while
    flat refuses every entry, which means nothing can close, which means equity
    never changes, which means the drawdown never recovers. The bot went quiet
    permanently and the daily report attributed it to an absent setup.

    ``roll_day`` documents and fixes exactly this failure for the loss streak.
    The drawdown gate had the same shape and was never given the same
    treatment. Measured in a portfolio backtest before the fix:
    ``trend_wide_stop@60m`` under a 5% limit took its last trade after 26% of a
    19-month window and logged 4,193 drawdown rejections.

WHAT THE FIX IS AND IS NOT
    The halt is still terminal -- a 10% account drawdown should stop trading,
    unlike a loss streak, which the backtests measured as a daily breaker. What
    changed is that it is now latched, persisted, logged at ERROR, and
    distinguishable from a quiet market, with ``resume`` as the defined way out.
"""

from __future__ import annotations

import pytest

from app.config.settings import RiskConfig
from app.risk.engine import RiskEngine, RiskState
from app.strategy.explanation import LONG, Explanation, Outcome
from deltabt.costs import SymbolCosts

NOW = 1_760_000_000


class _Position:
    """Mirrors the shape app/risk/engine.py reads off an open position."""

    def __init__(self, symbol="BTCUSD", notional=1_000.0, is_open=True):
        self.symbol, self.notional, self.is_open = symbol, notional, is_open


def _costs() -> dict:
    return {"BTCUSD": SymbolCosts(
        symbol="BTCUSD", tick_size=0.5, contract_value=0.001, maker_fee=0.0002,
        taker_fee=0.0005, max_leverage=50.0, position_size_limit=1_000_000,
        funding_interval_seconds=3600)}


def _engine(**kw) -> RiskEngine:
    cfg = RiskConfig(**{"max_drawdown_pct": 0.10, **kw})
    return RiskEngine(cfg, _costs())


def _setup() -> Explanation:
    exp = Explanation(symbol="BTCUSD", bar_open=NOW, primary_timeframe="5m",
                      confirmation_timeframe="1m", strategy_version="t",
                      strategy_config_hash="h", outcome=Outcome.DETECTED)
    exp.direction = LONG
    exp.entry_price, exp.stop_price, exp.target_price = 100_000.0, 99_000.0, 102_000.0
    exp.detail["risk_per_unit"] = 1_000.0
    return exp


def _breached(equity=9_000.0, peak=10_000.0) -> RiskState:
    s = RiskState.fresh(equity)
    s.peak_equity = peak
    s.day = "1970-01-01"          # force a day roll on first evaluate
    return s


def test_breaching_while_flat_latches_an_explicit_halt():
    state = _breached(equity=8_000.0)          # 20% drawdown
    eng = _engine()
    d = eng.evaluate(_setup(), state, open_positions=[], now=NOW)

    assert not d.approved
    assert state.halted_at == NOW, "the halt was not latched"
    assert "drawdown" in state.halt_reason
    assert "while flat" in state.halt_reason


def test_the_halt_is_distinguishable_from_a_quiet_market():
    """The whole defect was that it looked like 'no setup occurred'."""
    state = _breached(equity=8_000.0)
    eng = _engine()
    eng.evaluate(_setup(), state, open_positions=[], now=NOW)

    d = eng.evaluate(_setup(), state, open_positions=[], now=NOW + 300)
    assert not d.approved
    assert d.limit_name == "halted"
    assert "HALTED" in d.reason
    assert "does not clear on its own" in d.reason


def test_breaching_with_a_position_open_does_not_latch():
    """That case is self-recovering: the open position can close green."""
    state = _breached(equity=8_000.0)
    eng = _engine()
    d = eng.evaluate(_setup(), state, open_positions=[_Position("ETHUSD")], now=NOW)

    assert not d.approved
    assert state.halted_at == 0, "latched a halt that could still recover"


def test_a_list_of_closed_positions_counts_as_flat():
    """`open_positions` can carry closed rows -- that state cannot recover."""
    state = _breached(equity=8_000.0)
    eng = _engine()
    eng.evaluate(_setup(), state,
                 open_positions=[_Position("ETHUSD", is_open=False)], now=NOW)
    assert state.halted_at == NOW, "closed positions were treated as recoverable"


def test_the_halt_survives_a_day_roll():
    """Unlike the loss streak, this is not a daily breaker."""
    state = _breached(equity=8_000.0)
    eng = _engine()
    eng.evaluate(_setup(), state, open_positions=[], now=NOW)
    assert state.halted_at

    state.roll_day(NOW + 86_400 * 3)
    assert state.halted_at, "the drawdown halt cleared at midnight"
    assert state.consecutive_losses == 0, "the streak should still be daily"


def test_resume_rebases_the_peak_so_it_does_not_immediately_re_halt():
    """Clearing the flag alone would re-halt on the next evaluation."""
    state = _breached(equity=8_000.0)
    eng = _engine()
    eng.evaluate(_setup(), state, open_positions=[], now=NOW)
    assert state.halted_at

    state.resume(NOW + 60)
    assert state.halted_at == 0
    assert state.peak_equity == state.equity, "peak was not rebased"
    assert state.drawdown_pct == 0.0

    d = eng.evaluate(_setup(), state, open_positions=[], now=NOW + 120)
    assert d.approved, f"re-halted immediately after resume: {d.reason}"


def test_resume_on_a_healthy_state_is_a_no_op():
    state = RiskState.fresh(10_000.0)
    state.peak_equity = 12_000.0
    state.resume(NOW)
    assert state.peak_equity == 12_000.0, "resume moved the peak without a halt"


def test_the_halt_round_trips_through_persistence():
    """It is worthless if a redeploy clears it."""
    state = _breached(equity=8_000.0)
    _engine().evaluate(_setup(), state, open_positions=[], now=NOW)
    assert state.halted_at

    restored = RiskState.from_dict(state.to_dict())
    assert restored.halted_at == state.halted_at
    assert restored.halt_reason == state.halt_reason

    d = _engine().evaluate(_setup(), restored, open_positions=[], now=NOW + 600)
    assert not d.approved and d.limit_name == "halted"


def test_state_persisted_before_the_fix_still_loads():
    """Old rows have no halt fields; they must default to 'not halted'."""
    legacy = {"equity": 10_000.0, "peak_equity": 10_000.0, "day": "2026-01-01",
              "day_start_equity": 10_000.0, "daily_pnl": 0.0, "trades_today": 0,
              "consecutive_losses": 0, "last_trade_at": 0, "last_loss_at": 0,
              "realized_pnl": 0.0, "wins": 0, "losses": 0}
    s = RiskState.from_dict(legacy)
    assert s.halted_at == 0 and s.halt_reason == ""


def test_a_disabled_drawdown_gate_never_halts():
    """1.0 is how paper switched the breaker off; it must stay off."""
    state = _breached(equity=5_200.0)          # 48% drawdown
    eng = _engine(max_drawdown_pct=1.0)
    d = eng.evaluate(_setup(), state, open_positions=[], now=NOW)
    assert state.halted_at == 0
    assert d.approved, d.reason
