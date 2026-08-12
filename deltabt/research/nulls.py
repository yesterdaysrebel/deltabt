"""Null models for H-Scalp-1.

The primary null is exposure-matched random entry: the same instrument, the
same direction mix, the same number of trades, and -- critically -- the same
stop distance and target structure, placed at random times. Anything the
strategy earns that the null also earns is drift or cost structure, not signal.

Matching the stop distance matters more than it sounds. Extreme-return bars
have large ranges, so H-Scalp-1's R is systematically bigger than a randomly
chosen bar's R. Sampling the random control's R from the strategy's own
realised R distribution removes that confound; otherwise the "edge" is partly
just a different risk denominator.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.research.hscalp1 import (
    MAX_HOLD_BARS,
    STOP_MULT,
    TARGET_FRAC,
    VOL_LOOKBACK,
    ScalpTrade,
    build_bars,
)


def random_entry_null(
    ltp_1m: pd.DataFrame,
    mark_1m: pd.DataFrame,
    funding: pd.DataFrame,
    costs: SymbolCosts,
    template: pd.DataFrame,
    *,
    start: int,
    n_sims: int = 200,
    seed: int = 0,
    cost_multiplier: float = 1.0,
    slippage_multiplier: float = 1.0,
) -> dict:
    """Exposure-matched random entry.

    `template` is the strategy's realised trade frame; the null copies its
    trade count, long/short ratio, and the joint distribution of
    (event_range, holding rule) by resampling actual strategy trades for the
    risk parameters, then placing them at uniformly random eligible bars.

    Returns pooled per-trade net R across all simulations plus the per-sim mean
    distribution, which is what the strategy's mean must be compared against.
    """
    bars, mark = build_bars(ltp_1m, mark_1m, start)
    n = len(bars)
    out = dict(per_trade=np.zeros(0), sim_means=np.zeros(0), n_sims=0)
    if template.empty or n < VOL_LOOKBACK + MAX_HOLD_BARS + 5:
        return out

    t_ = bars["time"].to_numpy("int64")
    o = bars["open"].to_numpy("float64"); h = bars["high"].to_numpy("float64")
    lo = bars["low"].to_numpy("float64"); c = bars["close"].to_numpy("float64")
    mh = mark["high"].to_numpy("float64"); ml = mark["low"].to_numpy("float64")

    taker_rate = (costs.effective_taker * cost_multiplier
                  + costs.slippage_rate * slippage_multiplier)
    maker_rate = costs.effective_maker * cost_multiplier

    stamps = funding_timestamps(int(t_[0]), int(t_[-1]),
                                costs.funding_interval_seconds)
    frate = {}
    if funding is not None and not funding.empty:
        ft = funding["time"].to_numpy("int64"); fv = funding["close"].to_numpy("float64")
        for s in stamps:
            i = np.searchsorted(ft, s, side="right") - 1
            if i >= 0 and np.isfinite(fv[i]):
                frate[int(s)] = float(fv[i])
    stamps = set(int(s) for s in stamps)

    # Exposure to match, taken from the strategy's own realised trades.
    n_trades = len(template)
    sides = template["side"].to_numpy()
    ranges = template["event_range"].to_numpy("float64")
    moves = (template["target_price"].to_numpy("float64")
             - template["entry_price"].to_numpy("float64"))
    maker_entry = bool((template["entry_price"] == template["entry_price"]).all())

    rng = np.random.default_rng(seed)
    lo_idx, hi_idx = VOL_LOOKBACK + 2, n - MAX_HOLD_BARS - 2
    if hi_idx <= lo_idx:
        return out

    per_trade: list[float] = []
    sim_means: list[float] = []

    for _ in range(n_sims):
        picks = rng.integers(lo_idx, hi_idx, size=n_trades)
        draw = rng.integers(0, n_trades, size=n_trades)
        sim_r = []
        for bar_i, tmpl_i in zip(picks, draw):
            side = int(sides[tmpl_i])
            rng_px = float(ranges[tmpl_i])
            if rng_px <= 0:
                continue
            entry = float(o[bar_i])
            r_price = STOP_MULT * rng_px
            stop = entry - side * r_price
            target = entry + abs(float(moves[tmpl_i])) * side
            if side * (target - entry) <= 0:
                continue

            exit_price = np.nan; reason = ""; exit_bar = bar_i
            for j in range(bar_i, min(bar_i + MAX_HOLD_BARS, n)):
                hit_stop = (ml[j] <= stop) if side > 0 else (mh[j] >= stop)
                hit_tgt = (h[j] >= target) if side > 0 else (lo[j] <= target)
                if hit_stop:
                    exit_price, reason, exit_bar = stop, "stop", j
                    break
                if hit_tgt:
                    exit_price, reason, exit_bar = target, "target", j
                    break
                exit_bar = j
            if not reason:
                exit_price, reason = c[exit_bar], "time"

            entry_rate = maker_rate if maker_entry else taker_rate
            exit_rate = maker_rate if (maker_entry and reason == "target") else taker_rate
            r_gross = side * (exit_price - entry) / r_price
            cost_r = (entry * entry_rate + exit_price * exit_rate) / r_price
            f_r = 0.0
            for s in stamps:
                if t_[bar_i] <= s <= t_[exit_bar]:
                    f_r += side * (frate.get(s, 0.0) / 100.0) * entry / r_price
            sim_r.append(r_gross - cost_r - f_r)

        if sim_r:
            per_trade.extend(sim_r)
            sim_means.append(float(np.mean(sim_r)))

    out["per_trade"] = np.asarray(per_trade, dtype="float64")
    out["sim_means"] = np.asarray(sim_means, dtype="float64")
    out["n_sims"] = len(sim_means)
    return out


def directional_baseline(
    ltp_1m: pd.DataFrame, costs: SymbolCosts, *, start: int, side: int
) -> dict:
    """Long-only / short-only buy-and-hold over the same window, for context."""
    bars, _ = build_bars(ltp_1m, None, start)
    if len(bars) < 2:
        return dict(total_return=np.nan, sharpe=np.nan)
    c = bars["close"].to_numpy("float64")
    r = side * np.diff(np.log(c))
    ann = np.sqrt(365 * 96)
    return dict(
        total_return=float(np.exp(r.sum()) - 1),
        sharpe=float(r.mean() / r.std(ddof=1) * ann) if r.std(ddof=1) > 0 else np.nan,
        n=len(r),
    )
