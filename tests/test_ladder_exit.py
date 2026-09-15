"""The stop ratchet: what it does, and the two ways it could flatter itself.

The rule is a stop that only ever moves in the position's favour, promoted when
favourable excursion passes a rung. Its whole risk is measurement error in its
own favour, so these tests are mostly about ordering and monotonicity rather
than about P&L.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.config import StrategyParams
from deltabt.costs import SymbolCosts
from deltabt.engine import LONG, SHORT, run_backtest
from deltabt.strategy import Signals

COSTS = SymbolCosts(symbol="T", tick_size=0.0001, contract_value=1,
                    maker_fee=0.0, taker_fee=0.0, max_leverage=50,
                    position_size_limit=1e9, funding_interval_seconds=28800,
                    slippage_bps=0.0)


def _frame(bars):
    """bars: list of (open, high, low, close)."""
    t = np.arange(len(bars), dtype="int64") * 60
    o, h, l, c = map(list, zip(*bars))
    return pd.DataFrame({"time": t, "open": o, "high": h, "low": l,
                         "close": c, "volume": [1.0] * len(bars)})


def _signals(n, entry_at=1, side=LONG, stop=1.0):
    """One entry, a stop `stop` away from 100, everything else inert.

    stop_long/stop_short are what the engine turns into risk_per_unit, so they
    are the only numbers here that matter to the ladder.
    """
    z = np.zeros(n, dtype=bool)
    le, se = z.copy(), z.copy()
    (le if side == LONG else se)[entry_at] = True
    ones = np.ones(n)
    return Signals(
        long_entry=le, short_entry=se,
        stop_long=np.full(n, 100.0 - stop), stop_short=np.full(n, 100.0 + stop),
        supertrend=np.full(n, 100.0), direction=ones * (1 if side == LONG else -1),
        atr=ones, wpr=np.full(n, -50.0), adx_1m=ones * 30, adx_5m=ones * 30,
        bull_1m=np.ones(n, dtype=bool), bear_1m=np.ones(n, dtype=bool),
        long_base=le, short_base=se, warmup=0)


def _run(bars, rungs, side=LONG, **kw):
    df = _frame(bars)
    p = StrategyParams(base_minutes=1, confirm_minutes=5, max_hold_bars=10_000,
                       exit_on_trend_flip=False, reward_risk=3.0,
                       ladder_rungs=rungs, **kw)
    return run_backtest(df, df, pd.DataFrame(), _signals(len(bars), side=side),
                        p, COSTS, initial_capital=100_000.0)


class TestOrderingWithinABar:
    def test_a_bar_cannot_promote_the_stop_and_be_stopped_by_the_promotion(self):
        """THE FLATTERING BUG THIS RULE INVITES.

        One bar reaches +1R and falls back through breakeven. If the stop is
        promoted before the bar is tested, the trade books +0R; it really took
        the original stop. On a rule whose entire claim is "it rescues losers",
        booking a rescue that never happened is the failure that would make it
        look good and be wrong.
        """
        # entry ~100, stop ~99 (risk 1). One bar spans +1R and back to -1R.
        bars = [(100, 100, 100, 100), (100, 100, 100, 100),
                (100, 101.0, 98.5, 99.0), (99, 99, 99, 99)]
        res = _run(bars, ((1.0, 0.0),))
        assert res.trades, "no trade was taken"
        t = res.trades[0]
        assert t.r_multiple < 0, (
            f"booked {t.r_multiple:+.3f}R on a bar that reached +1R and then "
            f"took out the original stop; the promotion was applied before the "
            f"stop test")


class TestMonotonicity:
    def test_the_stop_never_moves_against_the_position(self):
        """A rung that resolves worse than the current stop must be ignored."""
        # Reaches +2R, then falls through BOTH candidate stops. With the
        # rungs applied monotonically the trade exits at +0.5R; if the second,
        # worse rung is allowed to pull the stop back it exits at -0.5R.
        bars = [(100, 100, 100, 100), (100, 100, 100, 100),
                (100, 102.0, 100, 101.5), (101.5, 101.5, 99.0, 99.0)]
        res = _run(bars, ((1.0, 0.5), (1.5, -0.5)))
        assert res.trades, "the position never closed, so nothing was tested"
        r = res.trades[0].r_multiple
        assert r > 0, (
            f"booked {r:+.3f}R: a rung resolving BELOW the current stop pulled "
            f"it back against the position")

    @pytest.mark.parametrize("side", [LONG, SHORT])
    def test_it_works_in_both_directions(self, side):
        if side == LONG:
            bars = [(100, 100, 100, 100), (100, 100, 100, 100),
                    (100, 102, 100, 102), (102, 102, 99.0, 99.0)]
        else:
            bars = [(100, 100, 100, 100), (100, 100, 100, 100),
                    (100, 100, 98, 98), (98, 101.0, 98, 101.0)]
        res = _run(bars, ((1.0, 0.0),), side=side)
        assert res.trades
        t = res.trades[0]
        # Promoted to breakeven on bar 2, taken out on bar 3: ~0R, not -1R.
        assert t.r_multiple > -0.5, (
            f"{('LONG','SHORT')[side == SHORT]} booked {t.r_multiple:+.3f}R; "
            f"the breakeven rung did not hold")


class TestDisabledByDefault:
    def test_no_rungs_is_the_default_and_changes_nothing(self):
        assert StrategyParams().ladder_rungs == ()

    def test_the_same_bars_score_identically_with_an_empty_ladder(self):
        bars = [(100, 100, 100, 100), (100, 100, 100, 100),
                (100, 102, 100, 102), (102, 102, 99.0, 99.0)]
        a = _run(bars, ())
        b = _run(bars, ((1.0, 0.0),))
        assert a.trades and b.trades
        assert a.trades[0].r_multiple != b.trades[0].r_multiple, (
            "the ladder changed nothing, so these tests prove nothing")


class TestAdverseRInteraction:
    def test_a_promoted_stop_does_not_trigger_the_adverse_exit_immediately(self):
        """exit_at_adverse_r measured risk as the CURRENT stop distance.

        Once a rung promotes the stop to breakeven that distance is zero, and
        `adverse >= fraction * 0` is true of any adverse tick at all -- so the
        position would close on the first tick against entry, for a reason the
        log calls "adverse_r". Risk is now measured against the entry stop.
        """
        bars = [(100, 100, 100, 100), (100, 100, 100, 100),
                (100, 102, 100, 102), (102, 102, 101.9, 101.95),
                (101.95, 103.5, 101.9, 103.5)]
        res = _run(bars, ((1.0, 0.0),), exit_at_adverse_r=0.5)
        assert res.trades
        assert res.trades[0].exit_reason != "adverse_r", (
            "a trade 2R in front exited on 'adverse_r' because the promoted "
            "stop made the measured risk zero")


class TestLadderCooldown:
    """The cooldown must apply to LADDER exits and nothing else.

    The ladder's measured damage is that it exits early, frees the position
    slot, and the arm takes trades it would otherwise have held through: 342
    becomes 917. A cooldown aimed at promoted-stop exits suppresses exactly
    those. Aimed at every exit it would change the entry set for reasons
    unrelated to the ladder, and the entry set is the single most violent lever
    in this system -- the no-ladder baseline moves 65.3R to 3.1R on cooldown
    length alone.
    """

    def test_zero_falls_back_to_the_normal_cooldown(self):
        assert StrategyParams().ladder_cooldown_bars == 0

    def test_a_promoted_stop_uses_the_ladder_cooldown(self):
        # Entry, promote to breakeven, stop out at breakeven, then a second
        # entry signal well inside the ladder cooldown.
        bars = ([(100, 100, 100, 100)] * 2
                + [(100, 102, 100, 102), (102, 102, 99.0, 99.0)]
                + [(100, 100, 100, 100)] * 6)
        df = _frame(bars)
        n = len(bars)
        sig = _signals(n, entry_at=1)
        sig.long_entry[5] = True          # second signal, 2 bars after the exit
        p = StrategyParams(base_minutes=1, confirm_minutes=5,
                           max_hold_bars=10_000, exit_on_trend_flip=False,
                           reward_risk=3.0, ladder_rungs=((1.0, 0.0),),
                           cooldown_bars=1, ladder_cooldown_bars=50)
        res = run_backtest(df, df, pd.DataFrame(), sig, p, COSTS,
                           initial_capital=100_000.0)
        assert res.rejects["cooldown"] > 0, (
            "the second entry was not refused; the ladder cooldown did not "
            "apply to a promoted-stop exit")

    def test_an_ordinary_stop_keeps_the_normal_cooldown(self):
        """No rung ever fires, so the long ladder cooldown must not apply."""
        # The LAST bar exists so the second position closes -- only closed
        # trades are recorded, and an open one is invisible to this assertion.
        bars = ([(100, 100, 100, 100)] * 2
                + [(100, 100.1, 98.0, 98.0)]
                + [(100, 100, 100, 100)] * 6
                + [(100, 100, 97.0, 97.0)])
        df = _frame(bars)
        n = len(bars)
        sig = _signals(n, entry_at=1)
        sig.long_entry[5] = True
        p = StrategyParams(base_minutes=1, confirm_minutes=5,
                           max_hold_bars=10_000, exit_on_trend_flip=False,
                           reward_risk=3.0, ladder_rungs=((1.0, 0.0),),
                           cooldown_bars=1, ladder_cooldown_bars=50)
        res = run_backtest(df, df, pd.DataFrame(), sig, p, COSTS,
                           initial_capital=100_000.0)
        assert len(res.trades) == 2, (
            f"expected the second entry to be taken after an ordinary stop; "
            f"got {len(res.trades)} trade(s) and "
            f"{res.rejects['cooldown']} cooldown rejection(s)")
