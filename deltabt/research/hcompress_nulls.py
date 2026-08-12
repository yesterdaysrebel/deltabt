"""Null models for H-Compress-1.

The H-Scalp null design failed because "random entry" is under-specified for a
limit-entry strategy: three plausible constructions gave three different
verdicts. These nulls avoid that by holding the mechanics fixed and destroying
exactly one ingredient at a time, so each answers a specific question.

  A  random eligible timing  -> is the compression->expansion SEQUENCE needed,
                                or would the same geometry work anywhere?
  B  shuffled direction      -> does the expansion pick the right SIDE?
  C  volatility-matched      -> is the edge just a volatility-regime effect?
     timestamp permutation

None uses future information: every null re-runs the same forward simulation
from a candidate bar, with the same fill, stop, target and cost rules.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.research.hcompress import (
    MAX_HOLD_5M,
    MAX_STOP_PCT,
    ORDER_LIFETIME_5M,
    build_frames,
)


def _simulate(
    j: int, side: int, zone_hi: float, zone_lo: float, *,
    arm: str, target_r: float, o5, h5, l5, c5, mh, ml, t5, n,
    costs: SymbolCosts, maker: float, taker: float, conservative: bool,
    stamps: set, frate: dict,
) -> float | None:
    """Forward simulation shared by strategy and nulls -- identical mechanics."""
    if not (np.isfinite(zone_hi) and np.isfinite(zone_lo)) or zone_hi <= zone_lo:
        return None

    if arm == "A":
        limit = zone_hi if side > 0 else zone_lo
        filled = -1
        for m in range(j + 1, min(j + 1 + ORDER_LIFETIME_5M, n)):
            if side > 0:
                hit = (l5[m] < limit - costs.tick_size) if conservative else (l5[m] <= limit)
            else:
                hit = (h5[m] > limit + costs.tick_size) if conservative else (h5[m] >= limit)
            if hit:
                filled = m
                break
        if filled < 0:
            return None
        entry, entry_rate = limit, maker
    else:
        if j + 1 >= n:
            return None
        filled = j + 1
        entry, entry_rate = o5[filled], taker

    stop = zone_lo if side > 0 else zone_hi
    r_price = abs(entry - stop)
    if r_price <= 0 or r_price / entry > MAX_STOP_PCT:
        return None
    target = entry + side * target_r * r_price

    exit_price = np.nan; reason = ""; exit_bar = filled
    for m in range(filled, min(filled + MAX_HOLD_5M, n)):
        entry_bar_passive = (m == filled) and (arm == "A")
        hit_stop = (ml[m] <= stop) if side > 0 else (mh[m] >= stop)
        hit_tgt = (h5[m] >= target) if side > 0 else (l5[m] <= target)
        if entry_bar_passive:
            hit_tgt = False
        if hit_stop:
            exit_price, reason, exit_bar = stop, "stop", m
            break
        if hit_tgt:
            exit_price, reason, exit_bar = target, "target", m
            break
        exit_bar = m
    if not reason:
        exit_price, reason = c5[exit_bar], "time"

    exit_rate = maker if (arm == "A" and reason == "target") else taker
    r_gross = side * (exit_price - entry) / r_price
    cost_r = (entry * entry_rate + exit_price * exit_rate) / r_price
    f_r = 0.0
    for s in stamps:
        if t5[filled] <= s <= t5[exit_bar]:
            f_r += side * (frate.get(s, 0.0) / 100.0) * entry / r_price
    return float(r_gross - cost_r - f_r)


def run_nulls(
    ltp_1m: pd.DataFrame, mark_1m: pd.DataFrame, funding: pd.DataFrame,
    costs: SymbolCosts, trades: pd.DataFrame, *, start: int, end: int | None,
    arm: str = "A", target_r: float = 2.0, fill_model: str = "touch",
    n_sims: int = 40, seed: int = 0,
) -> dict:
    """Return per-trade net R for nulls A, B and C."""
    out = {"A": np.zeros(0), "B": np.zeros(0), "C": np.zeros(0)}
    if trades.empty:
        return out

    b5, b15, m5 = build_frames(ltp_1m, mark_1m, start)
    t5 = b5["time"].to_numpy("int64")
    o5 = b5["open"].to_numpy("float64"); h5 = b5["high"].to_numpy("float64")
    l5 = b5["low"].to_numpy("float64"); c5 = b5["close"].to_numpy("float64")
    mh = m5["high"].to_numpy("float64"); ml = m5["low"].to_numpy("float64")
    n = len(b5)
    hi = n if end is None else int(np.searchsorted(t5, end, side="right"))

    maker = costs.effective_maker
    taker = costs.effective_taker + costs.slippage_rate
    conservative = fill_model == "through"
    stamps = set(int(s) for s in funding_timestamps(int(t5[0]), int(t5[-1]),
                                                    costs.funding_interval_seconds))
    frate = {}
    if funding is not None and not funding.empty:
        ft = funding["time"].to_numpy("int64"); fv = funding["close"].to_numpy("float64")
        for s in stamps:
            i = np.searchsorted(ft, s, side="right") - 1
            if i >= 0 and np.isfinite(fv[i]):
                frate[s] = float(fv[i])

    kw = dict(arm=arm, target_r=target_r, o5=o5, h5=h5, l5=l5, c5=c5, mh=mh, ml=ml,
              t5=t5, n=n, costs=costs, maker=maker, taker=taker,
              conservative=conservative, stamps=stamps, frate=frate)

    # realised-volatility decile per 5m bar, for null C. Causal: uses a trailing
    # window, and deciles are computed on the study slice only.
    ret = np.concatenate(([np.nan], np.diff(np.log(c5))))
    rv = pd.Series(ret).rolling(96, min_periods=96).std().shift(1).to_numpy()
    valid_rv = np.isfinite(rv)
    deciles = np.full(n, -1, dtype="int64")
    if valid_rv.sum() > 100:
        edges = np.nanquantile(rv[valid_rv][:hi], np.linspace(0, 1, 11)[1:-1])
        deciles[valid_rv] = np.searchsorted(edges, rv[valid_rv])

    ev_idx = np.searchsorted(t5, trades["expansion_time"].to_numpy("int64"))
    sides = trades["side"].to_numpy("int64")
    zhi = trades["zone_high"].to_numpy("float64")
    zlo = trades["zone_low"].to_numpy("float64")

    rng = np.random.default_rng(seed)
    lo_b, hi_b = 100, hi - MAX_HOLD_5M - ORDER_LIFETIME_5M - 2
    if hi_b <= lo_b:
        return out

    a_res, b_res, c_res = [], [], []
    for _ in range(n_sims):
        for k in range(len(trades)):
            side = int(sides[k]); zh = float(zhi[k]); zl = float(zlo[k])
            width = zh - zl

            # A: same geometry, uniformly random eligible timing. The zone is
            # re-centred on the random bar's close so the retest level is
            # reachable, preserving width and side.
            jb = int(rng.integers(lo_b, hi_b))
            mid = c5[jb]
            r = _simulate(jb, side, mid + width / 2, mid - width / 2, **kw)
            if r is not None:
                a_res.append(r)

            # B: real event, real timing, real zone -- direction reassigned.
            j0 = int(ev_idx[k])
            if lo_b <= j0 < hi_b:
                r = _simulate(j0, int(rng.choice([-1, 1])), zh, zl, **kw)
                if r is not None:
                    b_res.append(r)

            # C: move the signal to a different bar in the SAME volatility
            # decile -- keeps the volatility conditioning, destroys the
            # compression->expansion sequence.
            if lo_b <= j0 < hi_b and deciles[j0] >= 0:
                pool = np.flatnonzero((deciles[lo_b:hi_b] == deciles[j0])) + lo_b
                if pool.size > 1:
                    jc = int(rng.choice(pool))
                    midc = c5[jc]
                    r = _simulate(jc, side, midc + width / 2, midc - width / 2, **kw)
                    if r is not None:
                        c_res.append(r)

    out["A"] = np.asarray(a_res, dtype="float64")
    out["B"] = np.asarray(b_res, dtype="float64")
    out["C"] = np.asarray(c_res, dtype="float64")
    return out
