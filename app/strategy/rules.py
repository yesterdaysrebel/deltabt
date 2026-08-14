"""H-WPR-1 Variant A, evaluated on closed bars with 5m primary / 1m confirmation.

INDICATORS ARE NOT REIMPLEMENTED HERE. Every value comes from
``deltabt.indicators``, the same numba functions the backtester and all eight
research experiments used, tested against hand-computed Wilder values. The
whole point of bounded-window recomputation is that there is exactly one
implementation of Supertrend/ADX/DI/WPR in this repository.

The structural stop reuses ``deltabt.research.hwpr._leg_extreme`` for the same
reason.

WHAT "CLOSED BAR" MEANS HERE
    ``evaluate()`` is handed a DataFrame of closed bars and reads only its
    final row. The caller never puts a forming bar into that frame. Two tests
    enforce it: one mutates the forming bar and asserts the signal is
    unchanged, another appends future bars and asserts earlier signals are
    identical.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.config.strategy import StrategyConfig
from app.strategy.explanation import LONG, SHORT, Explanation, Outcome
from deltabt import indicators as ind
from deltabt.research.hwpr import _leg_extreme


class InsufficientHistory(Exception):
    """Fewer bars than the indicator warm-up needs."""


def warmup_bars(cfg: StrategyConfig) -> int:
    return max(
        cfg.williams_r.period,
        cfg.adx.di_period + 2 * cfg.adx.period,
        cfg.supertrend.atr_period,
    ) + 5


def _arr(df: pd.DataFrame, col: str) -> np.ndarray:
    return df[col].to_numpy("float64")


class IndicatorSnapshot:
    """Indicator values on one timeframe, computed over a bounded window."""

    __slots__ = ("st", "direction", "plus_di", "minus_di", "adx", "wpr",
                 "leg_low", "leg_high", "close", "high", "low", "time", "n",
                 "leg_start", "leg_truncated")

    def __init__(self, df: pd.DataFrame, cfg: StrategyConfig) -> None:
        h, l, c = _arr(df, "high"), _arr(df, "low"), _arr(df, "close")
        self.st, self.direction = ind.supertrend(
            h, l, c, cfg.supertrend.multiplier, cfg.supertrend.atr_period)
        self.plus_di, self.minus_di, self.adx = ind.dmi(
            h, l, c, cfg.adx.di_period, cfg.adx.period)
        self.wpr = ind.wpr(h, l, c, cfg.williams_r.period)
        self.leg_low, self.leg_high = _leg_extreme(h, l, self.direction)
        self.close, self.high, self.low = c, h, l
        self.time = df["time"].to_numpy("int64")
        self.n = len(df)
        self.leg_start, self.leg_truncated = self._leg_bounds(warmup_bars(cfg))

    def _leg_bounds(self, warmup: int) -> tuple[int, bool]:
        """Where the current Supertrend leg began, and whether we can see it.

        Every other indicator here converges: Wilder smoothing forgets its
        seed, so a long enough window reproduces the whole-history value at the
        tail. The leg extreme does NOT converge -- it is an extremum since the
        last flip, so if the leg started before the window began, the value is
        the extremum of an arbitrary truncation rather than of the leg.

        That would silently move the stop, so it is detected rather than
        tolerated. On real 5m data a leg outrunning a 1500-bar window means
        five straight days without a single Supertrend flip; the flag exists
        because "vanishingly rare" is not "impossible".

        A flip found INSIDE the warm-up region does not count. Supertrend seeds
        its bands from the first bars of whatever array it is given, so the
        first apparent direction change in a window is an artifact of where the
        window starts, not a market event -- and measured on a strong trend it
        moves the leg extreme by 60% between window lengths.
        """
        d = self.direction
        last = d[-1] if self.n else np.nan
        if not np.isfinite(last):
            return -1, True
        for i in range(self.n - 1, 0, -1):
            prev = d[i - 1]
            if not np.isfinite(prev):
                return i, True          # leg runs back into the warm-up NaNs
            if prev != d[i]:
                return i, i < warmup
        return 0, True

    def at(self, i: int = -1) -> dict:
        return {
            "close": float(self.close[i]),
            "supertrend": float(self.st[i]),
            "direction": float(self.direction[i]),
            "plus_di": float(self.plus_di[i]),
            "minus_di": float(self.minus_di[i]),
            "adx": float(self.adx[i]),
            "wpr": float(self.wpr[i]),
            "wpr_prev": float(self.wpr[i - 1]) if self.n > 1 else float("nan"),
            "leg_low": float(self.leg_low[i]),
            "leg_high": float(self.leg_high[i]),
            "leg_start_bar": int(self.time[self.leg_start]) if self.leg_start >= 0 else None,
            "leg_truncated": bool(self.leg_truncated),
            "bar_open": int(self.time[i]),
        }


def _finite(*vals) -> bool:
    return all(v is not None and np.isfinite(v) for v in vals)


def _checks(p: dict, c: dict, cfg: StrategyConfig) -> tuple[list, list]:
    """The complete long and short conjunctions at one bar.

    Pulled out of ``evaluate`` so the identical logic can be applied to the
    PREVIOUS closed bar as well, which is what makes one-shot firing possible
    without holding any state -- see ``evaluate``.
    """
    st_long = p["direction"] < 0          # Pine: direction < 0 is bullish
    st_short = p["direction"] > 0
    adx_ok = p["adx"] >= cfg.adx.minimum
    di_long = p["plus_di"] > p["minus_di"]
    di_short = p["minus_di"] > p["plus_di"]

    # Williams %R Variant A: above -80 AND rising (mirrored for shorts).
    # `wpr_prev` is the previous CLOSED bar, never a forming one.
    wpr_rising = _finite(p["wpr_prev"]) and p["wpr"] > p["wpr_prev"]
    wpr_falling = _finite(p["wpr_prev"]) and p["wpr"] < p["wpr_prev"]

    cst_long = c["direction"] < 0
    cst_short = c["direction"] > 0
    cadx_ok = c["adx"] >= cfg.adx.minimum
    cdi_long = c["plus_di"] > c["minus_di"]
    cdi_short = c["minus_di"] > c["plus_di"]
    # V2: the same oscillator rule, on the confirmation timeframe.
    cwpr_rising = _finite(c["wpr_prev"]) and c["wpr"] > c["wpr_prev"]
    cwpr_falling = _finite(c["wpr_prev"]) and c["wpr"] < c["wpr_prev"]

    conf_long = ((cst_long if cfg.confirm_supertrend else True)
                 and ((cadx_ok and cdi_long) if cfg.confirm_adx_di else True)
                 and ((c["wpr"] > -80.0 and cwpr_rising) if cfg.confirm_wpr else True))
    conf_short = ((cst_short if cfg.confirm_supertrend else True)
                  and ((cadx_ok and cdi_short) if cfg.confirm_adx_di else True)
                  and ((c["wpr"] < -20.0 and cwpr_falling) if cfg.confirm_wpr else True))

    checks_long = [
        ("primary_supertrend_bullish", st_long),
        ("primary_adx_ge_min", adx_ok),
        ("primary_di_plus_dominant", di_long),
        ("primary_wpr_above_-80", p["wpr"] > -80.0),
        ("primary_wpr_rising", wpr_rising),
        ("confirm_1m_agrees_long", conf_long),
    ]
    checks_short = [
        ("primary_supertrend_bearish", st_short),
        ("primary_adx_ge_min", adx_ok),
        ("primary_di_minus_dominant", di_short),
        ("primary_wpr_below_-20", p["wpr"] < -20.0),
        ("primary_wpr_falling", wpr_falling),
        ("confirm_1m_agrees_short", conf_short),
    ]
    return checks_long, checks_short


def evaluate(
    primary: pd.DataFrame,
    confirmation: pd.DataFrame,
    cfg: StrategyConfig,
    *,
    symbol: str,
) -> Explanation:
    """Evaluate one closed primary bar.

    ``primary`` is closed 5m bars, ``confirmation`` closed 1m bars. Only the
    last row of each is read. Both must already exclude any forming bar.
    """
    cfg.validate()
    exp = Explanation(
        symbol=symbol,
        bar_open=int(primary["time"].iloc[-1]) if len(primary) else 0,
        primary_timeframe=cfg.primary_timeframe,
        confirmation_timeframe=cfg.confirmation_timeframe,
        strategy_version=cfg.version,
        strategy_config_hash=cfg.config_hash,
        outcome=Outcome.NO_SETUP,
    )

    need = warmup_bars(cfg)
    if len(primary) < need or len(confirmation) < need:
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = (
            f"warm-up incomplete: have {len(primary)} primary / "
            f"{len(confirmation)} confirmation bars, need {need}")
        return exp

    P = IndicatorSnapshot(primary.tail(cfg.window_bars).reset_index(drop=True), cfg)
    C = IndicatorSnapshot(confirmation.tail(cfg.window_bars).reset_index(drop=True), cfg)
    p, c = P.at(-1), C.at(-1)
    exp.indicators = {"primary": p, "confirmation": c}

    if not _finite(p["adx"], p["wpr"], p["direction"], p["supertrend"],
                   c["adx"], c["direction"]):
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = "indicator warm-up produced NaN"
        return exp

    if P.leg_truncated:
        # The structural stop depends on the extreme since the last Supertrend
        # flip. If that flip is not inside the window, the extreme is an
        # artifact of where the window happens to start, and the stop -- and
        # therefore the position size -- would be wrong. Suppress rather than
        # substitute a different stop, which would be a silent rule change.
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = (
            f"Supertrend leg extends beyond the {cfg.window_bars}-bar window; "
            f"the structural stop is not determinable from it")
        return exp

    checks_long, checks_short = _checks(p, c, cfg)
    long_ok = all(v for _, v in checks_long)
    short_ok = all(v for _, v in checks_short)

    # ---- one-shot: fire on the setup's FALSE -> TRUE edge -------------------
    # Evaluated from the SAME window rather than from remembered state. A flag
    # carried between calls would have to survive restarts, replays and
    # duplicate feed messages; recomputing the previous closed bar cannot drift,
    # cannot be stale after a redeploy, and keeps evaluate() a pure function of
    # the bars it is handed -- which two existing tests rely on.
    if cfg.fire_once and (long_ok or short_ok):
        prev_long, prev_short = _checks(P.at(-2), C.at(-2), cfg)
        was_long = all(v for _, v in prev_long)
        was_short = all(v for _, v in prev_short)
        if (long_ok and was_long) or (short_ok and was_short):
            exp.outcome = Outcome.NO_SETUP
            side = "long" if long_ok else "short"
            exp.conditions_passed = [n for n, _ in
                                     (checks_long if long_ok else checks_short)]
            exp.rejection_reason = (
                f"{side} setup was already true on the previous closed bar; "
                f"one signal per FALSE->TRUE transition")
            exp.detail["suppressed_repeat"] = True
            return exp

    if long_ok and short_ok:
        # Structurally impossible (Supertrend cannot be both), but if it ever
        # happens the honest response is to take neither.
        exp.outcome = Outcome.SUPPRESSED
        exp.rejection_reason = "long and short both satisfied -- refusing both"
        return exp

    if not (long_ok or short_ok):
        # Report the near miss: whichever side got further explains the day.
        chosen = checks_long if sum(v for _, v in checks_long) >= sum(
            v for _, v in checks_short) else checks_short
        exp.conditions_passed = [n for n, v in chosen if v]
        exp.conditions_failed = [n for n, v in chosen if not v]
        return exp

    checks = checks_long if long_ok else checks_short
    side = LONG if long_ok else SHORT
    exp.direction = side
    exp.conditions_passed = [n for n, _ in checks]

    # ---- structural stop and target ---------------------------------------
    # Frozen H-WPR-1 stop: the leg extreme since the Supertrend last flipped,
    # bounded by the Supertrend line itself. Both are computed from closed bars
    # only; neither consults a future bar.
    entry = p["close"]
    if side == LONG:
        stop = min(p["leg_low"], p["supertrend"])
    else:
        stop = max(p["leg_high"], p["supertrend"])

    risk_per_unit = (entry - stop) if side == LONG else (stop - entry)
    exp.entry_price, exp.stop_price = entry, stop

    if not _finite(risk_per_unit) or risk_per_unit <= 0:
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = (
            f"stop distance {risk_per_unit} is not positive (entry {entry}, "
            f"stop {stop})")
        return exp

    stop_pct = risk_per_unit / entry
    exp.stop_distance_pct = 100.0 * stop_pct
    if stop_pct > cfg.max_stop_pct:
        exp.outcome = Outcome.REJECTED
        exp.rejection_reason = (
            f"stop {100*stop_pct:.2f}% exceeds max_stop_pct "
            f"{100*cfg.max_stop_pct:.2f}%")
        return exp

    target = (entry + cfg.target_r * risk_per_unit if side == LONG
              else entry - cfg.target_r * risk_per_unit)
    exp.target_price = target
    exp.reward_risk = cfg.target_r
    exp.detail["risk_per_unit"] = risk_per_unit
    if side == LONG:
        basis = "leg_low" if p["leg_low"] <= p["supertrend"] else "supertrend"
    else:
        basis = "leg_high" if p["leg_high"] >= p["supertrend"] else "supertrend"
    exp.detail["stop_basis"] = basis
    exp.outcome = Outcome.DETECTED
    return exp
