"""H-NULL-1 -- adversarial null and estimator validation.

Research infrastructure, not a strategy search. Nothing here is used to find an
edge; it exists to find out when our estimators INVENT one.

The convention-parameterised barrier walk below is deliberately a separate
implementation from hema3._barrier_walk, which stays frozen. It reproduces that
walk exactly under its default flags and lets each execution convention be
switched off one at a time, which is the only way to attribute a bias to a
convention rather than argue about it.
"""

from __future__ import annotations

import numpy as np
from numba import njit

# same-bar resolution when a bar touches both the barrier and the stop
SAMEBAR_STOP = 0        # frozen production convention
SAMEBAR_TARGET = 1      # the optimistic mirror of it
SAMEBAR_EXCLUDE = 2     # drop the bet; unbiased but discards information


@njit(cache=True)
def barrier_walk(entry_idx, entry_px, stop_px, side, mh, ml, hi, lo,
                 k_max, n_keep, samebar, use_mark):
    """Max favourable excursion in R before the stop, with conventions exposed.

    `samebar` and `use_mark` are the two places where a real simulator's
    execution rules enter a statistic that theory says should be scale-free.
    Isolating them is the whole point of this module.
    """
    m = entry_idx.size
    best = np.full(m, np.nan)
    stopped = np.zeros(m, np.bool_)
    voided = np.zeros(m, np.bool_)
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
        void = False
        while t < n_keep:
            if use_mark:
                s_hit = (ml[t] <= s) if d > 0 else (mh[t] >= s)
            else:
                s_hit = (lo[t] <= s) if d > 0 else (hi[t] >= s)
            ex = (hi[t] - e) / r if d > 0 else (e - lo[t]) / r
            t_hit = ex >= k_max
            if s_hit and t_hit:
                # genuinely ambiguous bar: it touched BOTH the barrier and the
                # stop, and bar data cannot say which came first
                if samebar == SAMEBAR_STOP:
                    hit_stop = True
                elif samebar == SAMEBAR_EXCLUDE:
                    void = True
                else:
                    b = ex
                break
            if s_hit:
                hit_stop = True
                break
            if ex > b:
                b = ex
            if b >= k_max:
                break
            t += 1
        best[a] = b
        stopped[a] = hit_stop
        voided[a] = void
    return best, stopped, voided


def gbm(n=1_500_000, seed=0, vol=0.0004, spread=0.35, mark_widen=0.10):
    """Driftless random walk with intrabar range. Zero directional information.

    `mark_widen` inflates the MARK range relative to LTP, reproducing the
    measured property that mark lows sit below LTP lows.
    """
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.standard_normal(n) * vol))
    w = np.abs(rng.standard_normal(n)) * spread * vol * c
    hi, lo = c + w, c - w
    mh = c + w * (1.0 + mark_widen)
    ml = c - w * (1.0 + mark_widen)
    return c, hi, lo, mh, ml


def hit_rate(c, hi, lo, mh, ml, idx, width, k, *, samebar=SAMEBAR_STOP,
             use_mark=True, side=1):
    """P(reach +kR before -1R) for one direction at one stop width."""
    ent = c[idx]
    stop = ent * (1.0 - side * width)
    best, stopped, voided = barrier_walk(
        idx.astype("int64"), ent, stop, np.full(idx.size, side, "int64"),
        mh, ml, hi, lo, float(k), c.size, samebar, use_mark)
    hit = best >= k
    resolved = (hit | stopped) & ~voided
    return float(hit[resolved].mean()), int(resolved.sum()), float(voided.mean())


def paired_excess(c, hi, lo, mh, ml, idx, width_long, width_short, k, sides,
                  *, samebar=SAMEBAR_STOP, use_mark=True):
    """The H-EMA-3 statistic, with each leg's stop width supplied separately."""
    ent = c[idx]
    out = {}
    for tag, s, w in (("L", 1, width_long), ("S", -1, width_short)):
        stop = ent * (1.0 - s * w)
        b, st, vd = barrier_walk(idx.astype("int64"), ent, stop,
                                 np.full(idx.size, s, "int64"), mh, ml, hi, lo,
                                 float(k), c.size, samebar, use_mark)
        out[tag] = (b >= k, (b >= k) | st, vd)
    hit_L, res_L, v_L = out["L"]
    hit_S, res_S, v_S = out["S"]
    keep = res_L & res_S & ~v_L & ~v_S
    out_d = np.where(sides > 0, hit_L, hit_S).astype("float64")
    stat = (out_d - (hit_L.astype("float64") + hit_S.astype("float64")) / 2.0)[keep]
    n = stat.size
    if n < 2:
        return dict(n=n)
    mean = float(stat.mean())
    se = float(stat.std(ddof=1) / np.sqrt(n))
    return dict(n=n, excess_p=mean, se_p=se, t=mean / se if se else np.nan,
                excess_gross_R=(1.0 + k) * mean, se_gross_R=(1.0 + k) * se,
                mde_gross_R=(1.0 + k) * 2.8 * se)


# ---------------------------------------------------------------- Gate 2


class InvalidComparison(Exception):
    """Raised when an estimator is asked to compare legs with unequal R.

    Gate 2 is structural, not advisory: the safe estimator REFUSES the input
    rather than returning a number that a reader might quote. A post-hoc
    demonstration that a symmetric control happened to pass is not a substitute.
    """


R_TOL = 1e-9


def classify_comparison(r_long, r_short, sides, *, rtol=1e-6) -> dict:
    """SAFE / POTENTIALLY INVALID, with the reason, per the §6 matrix.

    The dangerous condition is NOT asymmetry by itself. It is asymmetry PLUS
    directional selection correlated with that asymmetry. Both are measured.
    """
    r_long = np.asarray(r_long, "float64")
    r_short = np.asarray(r_short, "float64")
    sides = np.asarray(sides)
    ok = np.isfinite(r_long) & np.isfinite(r_short) & (r_long > 0) & (r_short > 0)
    rel = np.abs(r_long - r_short) / np.maximum(r_long, r_short)
    symmetric = bool(np.all(rel[ok] <= rtol))
    # does direction track which leg carries the larger R?
    wider_is_long = r_long > r_short
    agree = float((wider_is_long[ok] == (sides[ok] > 0)).mean()) if ok.any() else 0.5
    correlated = abs(agree - 0.5) > 0.05
    if symmetric:
        return dict(status="SAFE", symmetric=True, direction_r_agreement=agree,
                    reason="equal R across directions")
    if not correlated:
        return dict(status="SAFE", symmetric=False, direction_r_agreement=agree,
                    reason=("unequal R, but direction is not correlated with which leg "
                            "carries the larger R"))
    return dict(status="POTENTIALLY INVALID", symmetric=False,
                direction_r_agreement=agree,
                median_asymmetry=float(np.median(
                    np.maximum(r_long, r_short)[ok] / np.minimum(r_long, r_short)[ok])),
                reason=("unequal R AND direction correlated with which leg carries the "
                        "larger R -- the condition that reproduces a false positive on "
                        "zero-signal data"))


def safe_paired_excess(c, hi, lo, mh, ml, idx, width_long, width_short, k, sides,
                       *, samebar=SAMEBAR_STOP, use_mark=True):
    """The paired statistic, but it REFUSES unequal-R input (Gate 2).

    Inputs        entry bars, per-direction stop widths, direction rule
    Pairing       both directions evaluated at the SAME bar
    Risk norm     enforced equal: unequal R is refused, not adjusted
    Outcome       first-passage to +kR vs -1R under the declared conventions
    Bootstrap     none needed; the paired statistic is an iid mean over bets
    Statistic     mean(stat) / se, with (1+k) scaling into R

    Can a direction rule gain statistical advantage merely by selecting the side
    with a different R?  NO -- by construction, because different R cannot reach
    this estimator at all.
    """
    ent = c[idx]
    r_long = ent * width_long
    r_short = ent * width_short
    verdict = classify_comparison(r_long, r_short, sides)
    if not verdict["symmetric"]:
        raise InvalidComparison(
            f"{verdict['status']}: {verdict['reason']} "
            f"(long R {float(np.median(r_long)):.6g} vs short R "
            f"{float(np.median(r_short)):.6g})")
    out = paired_excess(c, hi, lo, mh, ml, idx, width_long, width_short, k, sides,
                        samebar=samebar, use_mark=use_mark)
    out["comparison"] = verdict
    return out


def evaluate(c, hi, lo, mh, ml, idx, width_long, width_short, k, sides, **kw) -> dict:
    """Run the safe estimator, reporting refusal as a RESULT rather than a crash."""
    ent = c[idx]
    verdict = classify_comparison(ent * width_long, ent * width_short, sides)
    try:
        r = safe_paired_excess(c, hi, lo, mh, ml, idx, width_long, width_short,
                               k, sides, **kw)
        r["status"] = "MEASURED"
        return r
    except InvalidComparison as e:
        return dict(status="INVALID_COMPARISON", reason=str(e), comparison=verdict,
                    excess_gross_R=None, t=None, n=0)


def planted_edge_sides(c, hi, lo, mh, ml, idx, width, k, p_correct, seed):
    """A signal that is right `p_correct` of the time, independent of stop geometry.

    Truth is taken from the SYMMETRIC-stop outcome, so the planted edge cannot
    smuggle in a stop-width advantage -- which is exactly the confound this whole
    experiment exists to exclude.
    """
    ent = c[idx]
    b, st, _ = barrier_walk(idx.astype("int64"), ent, ent * (1.0 - width),
                            np.ones(idx.size, "int64"), mh, ml, hi, lo,
                            float(k), c.size, SAMEBAR_STOP, True)
    long_wins = b >= k
    truth = np.where(long_wins, 1, -1)
    rng = np.random.default_rng(seed)
    flip = rng.random(idx.size) >= p_correct
    return np.where(flip, -truth, truth)


# ------------------------------------------------- explicit-barrier machinery


@njit(cache=True)
def walk_px(entry_idx, entry_px, stop_px, target_px, side, mh, ml, hi, lo,
            n_keep, samebar, use_mark):
    """First passage to an EXPLICIT target price vs an explicit stop price.

    `barrier_walk` fixes the target at a multiple of the stop DISTANCE, which
    silently hard-codes a linear-price barrier construction. Taking both prices
    explicitly lets the construction itself become an experimental variable,
    which is what D3 requires.
    """
    m = entry_idx.size
    hit = np.zeros(m, np.bool_)
    stopped = np.zeros(m, np.bool_)
    voided = np.zeros(m, np.bool_)
    for a in range(m):
        j = entry_idx[a]
        s = stop_px[a]
        g = target_px[a]
        d = side[a]
        t = j
        while t < n_keep:
            if use_mark:
                s_hit = (ml[t] <= s) if d > 0 else (mh[t] >= s)
            else:
                s_hit = (lo[t] <= s) if d > 0 else (hi[t] >= s)
            t_hit = (hi[t] >= g) if d > 0 else (lo[t] <= g)
            if s_hit and t_hit:
                if samebar == SAMEBAR_STOP:
                    stopped[a] = True
                elif samebar == SAMEBAR_EXCLUDE:
                    voided[a] = True
                else:
                    hit[a] = True
                break
            if s_hit:
                stopped[a] = True
                break
            if t_hit:
                hit[a] = True
                break
            t += 1
    return hit, stopped, voided


def barriers(entry_px, width, k, side, *, space="linear"):
    """Stop/target prices under a declared barrier construction.

    linear  stop = e*(1 -/+ w)          target = e*(1 +/- k*w)
    log     stop = e*exp(-/+ s)         target = e*exp(+/- k*s),  s = -log(1-w)

    The linear construction is NOT symmetric under price reflection: a long's
    stop sits |log(1-w)| away in log space while a short's sits log(1+w), and
    |log(1-w)| > log(1+w). The log construction is symmetric by design. Which
    one the simulator uses is therefore a candidate cause of any directional
    offset, and D3 exists to measure it.
    """
    e = np.asarray(entry_px, "float64")
    s = np.asarray(side)
    if space == "linear":
        stop = e * (1.0 - s * width)
        target = e * (1.0 + s * k * width)
    elif space == "log":
        u = -np.log(1.0 - width)
        stop = e * np.exp(-s * u)
        target = e * np.exp(s * k * u)
    else:
        raise ValueError(space)
    return stop, target


def log_path(n, seed, vol=0.0004, spread=0.35, base=100.0):
    """A driftless LOG random walk with explicit up/down intrabar offsets.

    Kept separate from `gbm` so the reflection can be constructed exactly: the
    mirror negates the log path AND swaps the intrabar offsets, which is only
    well defined if those offsets are generated independently.
    """
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.standard_normal(n) * vol)
    up = np.abs(rng.standard_normal(n)) * spread * vol
    dn = np.abs(rng.standard_normal(n)) * spread * vol
    return x, up, dn, base


def materialise(x, up, dn, base, mark_widen=0.0):
    c = base * np.exp(x)
    hi = base * np.exp(x + up)
    lo = base * np.exp(x - dn)
    mh = base * np.exp(x + up * (1 + mark_widen))
    ml = base * np.exp(x - dn * (1 + mark_widen))
    return c, hi, lo, mh, ml


def reflect(x, up, dn):
    """Exact directional mirror: negate the log path, swap the intrabar wings."""
    return -x, dn, up


def leg_outcome(c, hi, lo, mh, ml, idx, width, k, side, *, space="linear",
                samebar=SAMEBAR_STOP, use_mark=True):
    ent = c[idx]
    sides = np.full(idx.size, side, "int64")
    stop, target = barriers(ent, width, k, sides, space=space)
    return walk_px(idx.astype("int64"), ent, stop, target, sides,
                   mh, ml, hi, lo, c.size, samebar, use_mark)


def paired_px(c, hi, lo, mh, ml, idx, width, k, sides, *, space="linear",
              samebar=SAMEBAR_STOP, use_mark=True, block=None):
    """Paired mirror statistic with the barrier construction as a parameter.

    `block` selects the dependence assumption and must be stated, never assumed:
        None  -> iid standard error. Valid only if bets are independent.
        int   -> moving-block standard error with that block length, for
                 temporally persistent or overlapping bets.
    """
    ent = c[idx]
    res = {}
    for tag, s in (("L", 1), ("S", -1)):
        ss = np.full(idx.size, s, "int64")
        stop, target = barriers(ent, width, k, ss, space=space)
        h, st, vd = walk_px(idx.astype("int64"), ent, stop, target, ss,
                            mh, ml, hi, lo, c.size, samebar, use_mark)
        res[tag] = (h, h | st, vd)
    hL, rL, vL = res["L"]
    hS, rS, vS = res["S"]
    keep = rL & rS & ~vL & ~vS
    sides = np.asarray(sides)
    out_d = np.where(sides > 0, hL, hS).astype("float64")
    stat = (out_d - (hL.astype("float64") + hS.astype("float64")) / 2.0)[keep]
    n = stat.size
    if n < 2:
        return dict(n=n)
    mean = float(stat.mean())
    if block is None:
        se = float(stat.std(ddof=1) / np.sqrt(n))
        dep = "iid"
    else:
        b = int(block)
        nb = n // b
        if nb < 2:
            return dict(n=n, excess_gross_R=(1 + k) * mean, se_gross_R=np.nan, t=np.nan)
        bm = stat[:nb * b].reshape(nb, b).mean(axis=1)
        se = float(bm.std(ddof=1) / np.sqrt(nb))
        dep = f"moving-block({b})"
    return dict(n=n, excess_p=mean, se_p=se, t=mean / se if se else np.nan,
                excess_gross_R=(1 + k) * mean, se_gross_R=(1 + k) * se,
                mde_gross_R=(1 + k) * 2.8 * se, dependence=dep, space=space)


# ------------------------------------------------- dependence-aware inference


def iid_se(x) -> float:
    x = np.asarray(x, "float64")
    return float(x.std(ddof=1) / np.sqrt(x.size))


def moving_block_se(x, b: int, *, n_boot: int = 2000, seed: int = 0) -> float:
    """Moving-block bootstrap SE of the mean, preserving within-block order.

    Blocks of length `b` are drawn WITH replacement from all `n-b+1` overlapping
    start positions, and concatenated in draw order. Temporal ordering inside a
    block is never permuted, which is the whole point: it is what carries the
    serial dependence the iid SE ignores.

    Fallback is deterministic and declared: `b` is clamped to at most n//2 so at
    least two distinct blocks always exist. Allowing b == n is not a conservative
    choice but a catastrophic one -- every bootstrap draw becomes the identical
    full series, the SE collapses to exactly 0, and any mean becomes infinitely
    significant. That failure is silent, which is precisely the class of bug this
    module exists to catch.
    """
    x = np.asarray(x, "float64")
    n = x.size
    if n < 4:
        return iid_se(x)
    b = int(max(1, min(b, n // 2)))
    nblocks = int(np.ceil(n / b))
    rng = np.random.default_rng(seed)
    out = np.empty(n_boot, "float64")
    chunk = max(1, min(n_boot, int(4_000_000 // max(nblocks * b, 1))))
    done = 0
    offs = np.arange(b)
    while done < n_boot:
        r = min(chunk, n_boot - done)
        starts = rng.integers(0, n - b + 1, size=(r, nblocks))
        idx = (starts[:, :, None] + offs[None, None, :]).reshape(r, -1)[:, :n]
        out[done:done + r] = x[idx].mean(axis=1)
        done += r
    return float(out.std(ddof=1))


def cluster_se(x, cluster_id) -> float:
    """Cluster-robust SE of the mean. Var = (1/n^2) * sum_g (sum_{i in g} dev)^2."""
    x = np.asarray(x, "float64")
    g = np.asarray(cluster_id)
    n = x.size
    if n < 2:
        return float("nan")
    dev = x - x.mean()
    order = np.argsort(g, kind="stable")
    gs, ds = g[order], dev[order]
    bounds = np.flatnonzero(np.r_[True, gs[1:] != gs[:-1], True])
    sums = np.add.reduceat(ds, bounds[:-1])
    return float(np.sqrt((sums ** 2).sum()) / n)


def block_length_from_dependence(x, *, max_frac: float = 0.05) -> dict:
    """FROZEN RULE. Chosen from observable dependence, never from rejection rates.

    b = the FIRST lag at which |acf| falls below the white-noise band 2/sqrt(n).
    That is the horizon beyond which the series is indistinguishable from noise
    at this sample size, and the block must span it.

    An earlier version of this rule took the LARGEST lag exceeding the band. It
    was discarded before any Type-I result was computed, because it is
    uninformative by construction: scanning ~300 lags at a 5% band lets ~15
    exceed it by chance, so the largest such lag tracks the scan window rather
    than the dependence. Measured on known series it returned b = 244 for white
    noise, 288 for AR(0.5) and 270 for AR(0.9) -- no discrimination at all.

    The rule reads ONLY the autocorrelation of the series. It cannot see a
    Type-I error rate, so freezing it leaves the null experiment blind to it.
    """
    x = np.asarray(x, "float64")
    n = x.size
    band = 2.0 / np.sqrt(n)
    l_max = max(1, int(n * max_frac))
    xc = x - x.mean()
    denom = float((xc * xc).sum())
    acf = []
    first_below = None
    for lag in range(1, l_max + 1):
        a = float((xc[lag:] * xc[:-lag]).sum() / denom) if denom > 0 else 0.0
        acf.append(a)
        if abs(a) < band and first_below is None:
            first_below = lag
    b = int(min(max(1, first_below if first_below else l_max), l_max))
    return dict(block_length=b, dependence_horizon=(b - 1),
                white_noise_band=float(band), n=int(n), lags_scanned=l_max,
                rule="b = first lag with |acf| < 2/sqrt(n), capped at 5% of n",
                acf_head=[round(v, 4) for v in acf[:10]])


def inference(x, *, block=None, cluster_id=None, seed=0) -> dict:
    """One estimate, three declared dependence assumptions. Never silent."""
    x = np.asarray(x, "float64")
    m = float(x.mean())
    out = dict(mean=m, n=int(x.size), se_iid=iid_se(x))
    out["dependence"] = "iid"
    out["se"] = out["se_iid"]
    if block is not None:
        out["se_block"] = moving_block_se(x, block, seed=seed)
        out["block_length"] = int(block)
        out["dependence"] = f"moving-block({int(block)})"
        out["se"] = out["se_block"]
    if cluster_id is not None:
        out["se_cluster"] = cluster_se(x, cluster_id)
        if block is None:
            out["dependence"] = "cluster"
            out["se"] = out["se_cluster"]
    out["t"] = m / out["se"] if out["se"] and np.isfinite(out["se"]) else np.nan
    out["mde"] = 2.8 * out["se"] if np.isfinite(out["se"]) else np.nan
    return out
