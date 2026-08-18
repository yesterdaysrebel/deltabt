"""H-EMA-3 -- paired mirror-direction barrier test.

See out/hema3/hema3_preregistration.md (sha256 in out/hema3/config.json).

THE IDEA
    At every signal bar, evaluate BOTH directions and score the signal against
    the average of the two:

        stat(k) = out_d(k) - ( out_long(k) + out_short(k) ) / 2

    where out_x(k) is 1 if direction x reaches +k*R before -1*R, each direction
    using its OWN frozen structural stop.

WHY IT BEATS A RESAMPLED CONTROL
    Under a martingale, P(hit +kR before -1R) = 1/(1+k) for ANY stop width, so
    the long/short stop-width asymmetry that wrecked H-EMA-2's control cannot
    bias this. The two outcomes come from the same bar, so bar selection,
    volatility regime and window drift cancel exactly. There is no control to
    construct: no deciles, no seeds, no shortfalls, no collisions. And nothing
    is discarded for being concurrent with an open position.

ONE WALK, ALL BARRIERS
    The walk records the maximum favourable excursion reached STRICTLY BEFORE
    the stop bar. For any k: reached it => hit; stopped without reaching it =>
    miss; ran out of data => unresolved at that k, reported and excluded.
    The frozen same-bar rule is preserved -- a bar touching both the barrier and
    the stop resolves to the STOP, so that bar's excursion does not count.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numba import njit

from deltabt.research import hema2

BARRIERS = (0.5, 1.0, 2.0, 4.0)


@njit(cache=True)
def _barrier_walk(entry_idx, entry_px, stop_px, side, mh, ml, hi, lo,
                  k_max, n_keep):
    """Max favourable excursion in R before the stop, per bet.

    Reads only bars at or after the entry bar, and never past ``n_keep`` -- the
    split boundary -- so a bet cannot resolve on the next segment's data.
    """
    m = entry_idx.size
    best = np.full(m, np.nan)
    stopped = np.zeros(m, np.bool_)
    for a in range(m):
        j = entry_idx[a]
        e = entry_px[a]
        s = stop_px[a]
        d = side[a]
        r = (e - s) if d > 0 else (s - e)
        if r <= 0 or not np.isfinite(r):
            continue
        b = 0.0
        t = j
        hit_stop = False
        while t < n_keep:
            # same-bar resolves to the STOP, so test it before counting this
            # bar's favourable excursion
            if (ml[t] <= s) if d > 0 else (mh[t] >= s):
                hit_stop = True
                break
            ex = (hi[t] - e) / r if d > 0 else (e - lo[t]) / r
            if ex > b:
                b = ex
            if b >= k_max:
                break
            t += 1
        best[a] = b
        stopped[a] = hit_stop
    return best, stopped


def outcomes(best, stopped, k):
    """(hit, resolved) at barrier k. Unresolved bets are excluded downstream."""
    hit = best >= k
    resolved = hit | stopped
    return hit, resolved


def build_bets(sym: dict, F: dict, bars: np.ndarray, window, k_max: float):
    """Both directions' barrier outcomes for a set of execution-TF bars."""
    t1 = sym["t1"]
    n_keep = int(np.searchsorted(t1, window[1], side="right"))
    e = hema2.entry_index(F["time"], F["tf"], t1)
    ok = (e > 0) & (e < len(t1)) & hema2.valid_stop_mask(F)
    bars = bars[ok[bars]]
    if bars.size == 0:
        return pd.DataFrame()
    ent_i = e[bars]
    inwin = (t1[ent_i] >= window[0]) & (t1[ent_i] < window[1])
    bars, ent_i = bars[inwin], ent_i[inwin]
    ent_px = sym["o"][ent_i]
    good = np.isfinite(ent_px) & (ent_px > 0)
    bars, ent_i, ent_px = bars[good], ent_i[good], ent_px[good]
    if bars.size == 0:
        return pd.DataFrame()

    out = dict(bar=bars, entry_idx=ent_i, entry_time=t1[ent_i], entry_px=ent_px,
               symbol=sym["costs"].symbol, exec_tf=F["tf"])
    for side, stop in ((1, F["stop_long"][bars]), (-1, F["stop_short"][bars])):
        b, s = _barrier_walk(ent_i.astype("int64"), ent_px, stop,
                             np.full(bars.size, side, "int64"),
                             sym["mh"], sym["ml"], sym["h"], sym["l"],
                             float(k_max), n_keep)
        tag = "L" if side > 0 else "S"
        out[f"best_{tag}"] = b
        out[f"stopped_{tag}"] = s
        out[f"r_{tag}"] = (ent_px - stop) if side > 0 else (stop - ent_px)
    df = pd.DataFrame(out)
    df["stop_pct_L"] = df.r_L / df.entry_px
    df["stop_pct_S"] = df.r_S / df.entry_px
    return df


def paired_statistic(bets: pd.DataFrame, k: float) -> dict:
    """excess_p(k), cluster-robust on symbol-day, and the R it implies."""
    if bets is None or bets.empty:
        return dict(n=0, k=k)
    hit_L, res_L = outcomes(bets.best_L.to_numpy(), bets.stopped_L.to_numpy(), k)
    hit_S, res_S = outcomes(bets.best_S.to_numpy(), bets.stopped_S.to_numpy(), k)
    keep = res_L & res_S                      # both legs must be decidable
    side = bets.side.to_numpy()
    out_d = np.where(side > 0, hit_L, hit_S).astype("float64")
    stat = out_d - (hit_L.astype("float64") + hit_S.astype("float64")) / 2.0
    n_total = len(bets)
    stat, keep_side = stat[keep], side[keep]
    n = stat.size
    if n < 2:
        return dict(n=n, k=k, unresolved=int(n_total - n))
    mean = float(stat.mean())
    cl = (bets.symbol.astype(str) + "|"
          + pd.to_datetime(bets.entry_time, unit="s").dt.strftime("%Y-%m-%d"))[keep]
    dev = pd.Series(stat - mean).groupby(cl.to_numpy()).sum().to_numpy()
    se = float(np.sqrt((dev ** 2).sum()) / n)
    t = mean / se if se > 0 else np.nan
    return dict(
        k=k, n=n, unresolved=int(n_total - n), clusters=int(dev.size),
        excess_p=mean, se_p=se, t=float(t),
        excess_gross_R=(1.0 + k) * mean,
        se_gross_R=(1.0 + k) * se,
        ci_low_R=(1.0 + k) * (mean - 1.96 * se),
        ci_high_R=(1.0 + k) * (mean + 1.96 * se),
        mde_gross_R=(1.0 + k) * 2.8 * se,      # 80% power, alpha .05
        p_arm=float(out_d[keep].mean()),
        p_mirror=float(((hit_L.astype(float) + hit_S.astype(float)) / 2)[keep].mean()),
        pct_long=float((keep_side > 0).mean()),
    )
