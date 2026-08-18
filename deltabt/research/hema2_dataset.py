"""Unchained evaluation: every signal becomes a trade, overlaps allowed.

WHY THIS EXISTS, AND WHAT IT IS NOT
    The frozen simulator holds ONE position at a time (`i = m + 1`), so a signal
    arriving while a position is open is discarded. On H-EMA-2 TRAIN that
    silently deleted 58.5% of eligible setups -- 194,143 of 332,315. For a
    PORTFOLIO that rule is correct: it is what a single-position account can
    actually do. For MEASURING whether a signal predicts direction it is
    destructive, and worse, non-randomly so: the discarded signals cluster in
    exactly the trending stretches a trend signal is supposed to exploit.

    This module answers the measurement question instead. Each signal is
    evaluated independently from its own entry bar, so the dataset contains
    every setup the mechanism produced.

    IT IS NOT A TRADEABLE STRATEGY. Concurrent exposure is unbounded, so the
    equity path is meaningless and no position-sizing or leverage constraint is
    applied. Only per-unit-risk quantities are defined here, and every one of
    them is exactly invariant to contract count -- fee_r = (entry+exit)*taker /
    r_price and r_gross = side*(exit-entry) / r_price both cancel `contracts`
    and `contract_value` identically -- which is why sizing can be dropped
    rather than faked.

    IT DOES NOT REPLACE THE FROZEN H-EMA-2 RESULT. The pre-registered
    experiment keeps its one-position-at-a-time semantics; this is a parallel
    dataset track, reported separately. Nothing in hwpr.py or hema2.py changes.

STATISTICAL WARNING
    Overlapping trades are NOT independent observations. Nominal n rises sharply
    while effective n does not: two trades opened three bars apart on the same
    symbol share almost all of their price path. `concurrency` is recorded per
    trade so the overlap can be measured rather than assumed away, and every
    report built on this dataset must quote effective n beside nominal n.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.research import hema2

REASONS = {0: "stop", 1: "target", 2: "end"}


@njit(cache=True)
def _simulate_overlap(long_sig, short_sig, o, h, l, c, mh, ml,
                      st1, leg_lo, leg_hi, tradable, target_r,
                      taker, slip, max_stop_pct):
    """hwpr._simulate's trade resolution, with the position lock removed.

    Every branch that decides an OUTCOME is identical to the frozen simulator:
    entry at the next bar's open, stop triggered on MARK, a bar containing both
    stop and target resolves to the STOP, exit at the stop/target price rather
    than the gapped price. The only removed line is `i = m + 1`.
    """
    n = o.size
    max_tr = 2_000_000
    out = np.zeros((max_tr, 12))
    k = 0
    skipped_stop = 0
    skipped_untradable = 0
    for i in range(n - 2):
        side = 0
        if long_sig[i]:
            side = 1
        elif short_sig[i]:
            side = -1
        if side == 0:
            continue
        j = i + 1
        if not tradable[j]:
            skipped_untradable += 1
            continue
        entry = o[j]
        if not np.isfinite(entry) or entry <= 0:
            skipped_untradable += 1
            continue
        s = st1[i]
        stop = min(s, leg_lo[i]) if side > 0 else max(s, leg_hi[i])
        if not np.isfinite(stop):
            skipped_stop += 1
            continue
        r_price = (entry - stop) if side > 0 else (stop - entry)
        if r_price <= 0 or (r_price / entry) > max_stop_pct:
            skipped_stop += 1
            continue
        target = entry + side * target_r * r_price

        exit_px = np.nan
        reason = 2
        amb = 0
        m = j
        while m < n:
            hit_stop = (ml[m] <= stop) if side > 0 else (mh[m] >= stop)
            hit_tgt = (h[m] >= target) if side > 0 else (l[m] <= target)
            if hit_stop and hit_tgt:
                amb = 1; exit_px = stop; reason = 0
                break
            if hit_stop:
                exit_px = stop; reason = 0
                break
            if hit_tgt:
                exit_px = target; reason = 1
                break
            m += 1
        if m >= n:
            m = n - 1
            exit_px = c[m]
            reason = 2

        # every quantity below is per unit of risk and cancels contract count
        out[k, 0] = side
        out[k, 1] = i
        out[k, 2] = j
        out[k, 3] = m
        out[k, 4] = entry
        out[k, 5] = exit_px
        out[k, 6] = stop
        out[k, 7] = target
        out[k, 8] = r_price
        out[k, 9] = reason
        out[k, 10] = side * (exit_px - entry) / r_price          # r_gross
        out[k, 11] = amb
        k += 1
        if k >= max_tr:
            break
    return out[:k], skipped_stop, skipped_untradable


def _concurrency(entry_idx, exit_idx):
    """How many other trades were open when each trade opened.

    Counted by sweep, not pairwise: #{open at t} = #{entry <= t} - #{exit < t}.
    The naive O(n^2) version is unusable here -- an unchained arm can produce
    tens of thousands of trades per symbol.
    """
    e = np.sort(np.asarray(entry_idx, dtype="int64"))
    x = np.sort(np.asarray(exit_idx, dtype="int64"))
    t = np.asarray(entry_idx, dtype="int64")
    started = np.searchsorted(e, t, side="right")
    ended = np.searchsorted(x, t, side="left")
    return (started - ended - 1).astype("int64")   # exclude the trade itself


def simulate_unchained(sym: dict, lo1, sh1, sl1, ss1, *, window,
                       label: str = "", with_concurrency: bool = True) -> pd.DataFrame:
    """Full-dataset evaluation of one arm on one symbol."""
    t1 = sym["t1"]
    beyond = (t1 > window[1]) | (t1 < window[0])
    lo1 = np.asarray(lo1, bool) & ~beyond
    sh1 = np.asarray(sh1, bool) & ~beyond
    st1, leg_lo, leg_hi = hema2.injection_arrays(lo1, sh1, sl1, ss1)
    costs: SymbolCosts = sym["costs"]
    arr, sk_stop, sk_untradable = _simulate_overlap(
        lo1, sh1, sym["o"], sym["h"], sym["l"], sym["c"], sym["mh"], sym["ml"],
        st1, leg_lo, leg_hi, sym["tradable"], float(hema2.TARGET_R),
        costs.effective_taker, costs.slippage_rate, float(hema2.MAX_STOP_PCT))
    if arr.shape[0] == 0:
        return pd.DataFrame()

    side = arr[:, 0].astype("int64")
    i_ = arr[:, 1].astype("int64"); j_ = arr[:, 2].astype("int64")
    m_ = arr[:, 3].astype("int64")
    entry = arr[:, 4]; exit_px = arr[:, 5]
    r_price = arr[:, 8]

    fee_r = (entry + exit_px) * costs.effective_taker / r_price
    slip_r = (entry + exit_px) * costs.slippage_rate / r_price

    stamps = np.array(sorted(funding_timestamps(
        int(t1[0]), int(t1[-1]), costs.funding_interval_seconds)), dtype="int64")
    frate = np.zeros(stamps.size)
    f = sym["funding"]
    if f is not None and not f.empty and stamps.size:
        ft = f["time"].to_numpy("int64"); fv = f["close"].to_numpy("float64")
        idx = np.searchsorted(ft, stamps, side="right") - 1
        ok = idx >= 0
        frate[ok] = np.where(np.isfinite(fv[idx[ok]]), fv[idx[ok]], 0.0)
    funding_r = np.zeros(len(side))
    if stamps.size:
        et, xt = t1[j_], t1[m_]
        cum = np.concatenate(([0.0], np.cumsum(frate)))
        a = np.searchsorted(stamps, et, side="left")
        b = np.searchsorted(stamps, xt, side="right")
        funding_r = side * (cum[b] - cum[a]) / 100.0 * entry / r_price

    cost_r = fee_r + slip_r + funding_r
    out = pd.DataFrame({
        "symbol": costs.symbol, "arm": label, "side": side,
        "signal_time": t1[i_], "entry_time": t1[j_], "exit_time": t1[m_],
        "entry_price": entry, "exit_price": exit_px,
        "stop_price": arr[:, 6], "target_price": arr[:, 7],
        "r_price": r_price, "stop_pct": r_price / entry,
        "bars_held": (m_ - j_).astype("int64"),
        "exit_reason": [REASONS[int(x)] for x in arr[:, 9]],
        "r_gross": arr[:, 10], "fee_r": fee_r, "slip_r": slip_r,
        "funding_r": funding_r, "cost_r": cost_r,
        "r_net": arr[:, 10] - cost_r,
        "ambiguous": arr[:, 11] > 0,
        "cluster": pd.to_datetime(t1[j_], unit="s").strftime("%Y-%m-%d"),
    })
    out["concurrency"] = _concurrency(j_, m_) if with_concurrency else -1
    out["skipped_stop"] = sk_stop
    out["skipped_untradable"] = sk_untradable
    return out
