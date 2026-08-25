"""Run any :class:`~deltabt.spec.StrategySpec` live.

WHY THIS REPLACES A HAND-WRITTEN ARM
    ``rules.py``, ``atr_arm.py`` and ``flip_arm.py`` are ~940 lines that each
    reimplement the same rule family for one configuration, and the backtester
    implements it a fourth time. Keeping them equal was a job for parity tests,
    which detect drift only after it ships -- and one had already drifted: the
    one-shot check in ``rules.py`` paired the previous PRIMARY bar with the
    previous CONFIRMATION bar, one minute back instead of one primary bar back.

    This evaluator has no rule of its own. It resamples the 1m history, calls
    ``deltabt.rulecore`` -- the same function the backtester calls -- and reads
    the last row. A spec that was backtested is the spec that trades.

THE BOUNDED WINDOW IS THE WHOLE ASSUMPTION, AND IT IS TESTED
    Live cannot recompute from genesis every bar, so it recomputes over a
    trailing window. That is only the same strategy if the tail of a windowed
    computation equals the tail of the full one.
    ``tests/test_rulecore_invariance.py`` asserts exactly that for every family
    in the catalog, and asserts the one deliberate exception: a Supertrend leg
    that outruns the window makes the leg-extreme stop indeterminate, and those
    bars are suppressed rather than sized off an arbitrary truncation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategy.explanation import LONG, SHORT, Explanation, Outcome
from deltabt import rulecore
from deltabt.spec import StrategySpec
from deltabt.strategy import resample_complete


def warmup_1m_bars(spec: StrategySpec) -> int:
    """Minutes of 1m history the spec needs before its first real signal.

    Stated in MINUTES rather than bars because that is the quantity the candle
    buffer and the backfill are sized in, and the two differ by a factor of the
    primary timeframe -- 145 bars is 145 minutes at 1m and 24 DAYS at 240m.
    """
    return spec.warmup_bars * spec.primary_minutes


def evaluate_spec(one_minute: pd.DataFrame, spec: StrategySpec, *,
                  symbol: str) -> Explanation:
    """Evaluate ``spec`` on the closed 1m history and report the last bar."""
    spec.validate()
    exp = Explanation(
        symbol=symbol,
        bar_open=0,
        primary_timeframe=f"{spec.primary_minutes}m",
        confirmation_timeframe=f"{spec.confirm_minutes}m" if spec.confirm.enabled else "",
        strategy_version=spec.name,
        strategy_config_hash=spec.config_hash,
        outcome=Outcome.NO_SETUP,
    )

    if one_minute is None or one_minute.empty:
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = "no 1m history"
        return exp

    primary = (one_minute if spec.primary_minutes == 1
               else _complete(one_minute, spec.primary_minutes))
    if len(primary) < spec.warmup_bars + 2:
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = (
            f"warm-up incomplete: {len(primary)} complete {spec.primary_minutes}m "
            f"bars, need {spec.warmup_bars + 2} "
            f"({warmup_1m_bars(spec):,} minutes of history)")
        return exp
    exp.bar_open = int(primary["time"].iloc[-1])

    confirm = None
    if spec.confirm.enabled:
        confirm = (one_minute if spec.confirm_minutes == 1
                   else _complete(one_minute, spec.confirm_minutes))
        if len(confirm) < spec.warmup_bars + 2:
            exp.outcome = Outcome.SUPPRESSED
            exp.rejection_reason = (
                f"confirmation warm-up incomplete: {len(confirm)} complete "
                f"{spec.confirm_minutes}m bars")
            return exp

    sig = rulecore.compute(primary, confirm, spec)
    i = len(sig) - 1

    exp.indicators = {"primary": _snapshot(sig.primary, i)}
    if spec.confirm.enabled and sig.confirm_index[i] >= 0:
        exp.indicators["confirmation"] = _snapshot(sig.confirm, int(sig.confirm_index[i]))

    if bool(sig.rejected_leg_truncated[i]):
        # The stop depends on the extreme since the Supertrend last flipped. If
        # that flip is outside the window the extreme is an artifact of where
        # the window starts, so the position would be sized off a rule nobody
        # wrote. Suppress rather than substitute.
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = (
            "the Supertrend leg extends beyond the history held; the "
            "structural stop is not determinable from it")
        return exp

    long_setup = bool(sig.long_setup[i])
    short_setup = bool(sig.short_setup[i])
    fired_long = bool(sig.long_entry[i])
    fired_short = bool(sig.short_entry[i])

    if not (fired_long or fired_short):
        if long_setup or short_setup:
            side = "long" if long_setup else "short"
            if spec.trigger == "edge" and (
                (long_setup and bool(sig.long_setup[i - 1]))
                or (short_setup and bool(sig.short_setup[i - 1]))
            ):
                exp.rejection_reason = (
                    f"{side} setup was already true on the previous closed bar; "
                    f"one signal per FALSE->TRUE transition")
                exp.detail["suppressed_repeat"] = True
            elif bool(sig.rejected_stop_pct[i]):
                exp.outcome = Outcome.REJECTED
                exp.rejection_reason = (
                    f"stop distance fails the {100*spec.max_stop_pct:.2f}% guard")
            else:
                exp.rejection_reason = f"{side} setup did not fire"
        return exp

    side = LONG if fired_long else SHORT
    entry = float(sig.close[i])
    stop = float(sig.stop_long[i] if fired_long else sig.stop_short[i])
    target = float(sig.target_long[i] if fired_long else sig.target_short[i])
    risk = entry - stop if fired_long else stop - entry

    exp.direction = side
    exp.entry_price, exp.stop_price, exp.target_price = entry, stop, target
    exp.stop_distance_pct = 100.0 * risk / entry
    exp.reward_risk = spec.target_r
    exp.detail["risk_per_unit"] = risk
    exp.detail["stop_mode"] = spec.stop
    exp.conditions_passed = _passed(spec, side is LONG)
    exp.outcome = Outcome.DETECTED
    return exp


def _complete(one_minute: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Resample, keeping only sufficiently complete buckets.

    Delegates to ``deltabt.strategy.resample_complete`` -- the same function
    the backtester uses, so the live bar set and the backtest bar set are
    identical by construction rather than by coincidence.
    """
    return resample_complete(one_minute, minutes)


def _snapshot(ti, i: int) -> dict:
    def f(a):
        v = float(a[i])
        return v if np.isfinite(v) else None
    return {"close": f(ti.close), "supertrend": f(ti.st), "direction": f(ti.direction),
            "plus_di": f(ti.plus_di), "minus_di": f(ti.minus_di), "adx": f(ti.adx),
            "wpr": f(ti.wpr), "atr": f(ti.atr), "leg_low": f(ti.leg_low),
            "leg_high": f(ti.leg_high), "bar_open": int(ti.time[i])}


def _passed(spec: StrategySpec, is_long: bool) -> list[str]:
    """Name the gates the spec actually enables, for the audit trail."""
    out: list[str] = []
    for label, rules in (("primary", spec.primary), ("confirm", spec.confirm)):
        if not rules.enabled:
            continue
        if rules.supertrend != "off":
            out.append(f"{label}_supertrend_{rules.supertrend}")
        if rules.di:
            out.append(f"{label}_di_{'plus' if is_long else 'minus'}_dominant")
        if rules.adx_min is not None:
            out.append(f"{label}_adx_ge_{rules.adx_min:g}")
        if rules.wpr_rule != "none":
            out.append(f"{label}_wpr_{rules.wpr_rule}")
    return out
