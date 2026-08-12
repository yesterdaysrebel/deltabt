"""Nulls for H-Funding.

Each destroys exactly one ingredient, so each answers a specific question:

  A  randomised entry timestamps within the same volatility regime
     -> does the TIMING of the funding extreme matter, or would the same
        exposure at any comparable moment do as well?
  B  randomised sign, magnitude distribution preserved
     -> does the DIRECTION implied by crowding matter?
  C  funding series shifted relative to price
     -> is the funding/price relationship CAUSAL, or an artifact of both being
        autocorrelated?

None uses future information relative to its own simulated entry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts
from deltabt.research.hfunding import MAX_LEVERAGE, build


def _one(j: int, side: int, hold_h: int, *, ho, hc, ht, n, fmap, interval,
         taker, slip) -> dict | None:
    """Simulate one position with identical mechanics to the strategy."""
    k = min(j + hold_h, n - 1)
    if k <= j:
        return None
    entry, exit_px = ho[j], hc[k]
    first = ((int(ht[j]) + interval - 1) // interval) * interval
    f_units = 0.0
    for s_ in range(first, int(ht[k]) + 1, interval):
        r_ = fmap.get(s_)
        if r_ is not None:
            f_units += -side * (r_ / 100.0)
    price_units = side * (exit_px - entry) / entry
    cost_units = 2 * taker + 2 * slip
    return dict(price_bps=price_units * 1e4, funding_bps=f_units * 1e4,
                net_bps=(price_units + f_units - cost_units) * 1e4)


def run_nulls(
    ltp_1m: pd.DataFrame, funding: pd.DataFrame, costs: SymbolCosts,
    trades: pd.DataFrame, *, start: int, end: int | None,
    hold_h: int = 24, n_sims: int = 60, seed: int = 0,
) -> dict:
    out = {"A": [], "B": [], "C": []}
    if trades.empty:
        return {k: np.zeros(0) for k in out}

    h1, f = build(ltp_1m, funding, start)
    ht = h1["time"].to_numpy("int64")
    ho = h1["open"].to_numpy("float64"); hc = h1["close"].to_numpy("float64")
    n = len(h1)
    hi = n if end is None else int(np.searchsorted(ht, end, side="right"))
    interval = costs.funding_interval_seconds
    taker = costs.effective_taker; slip = costs.slippage_rate

    ft = f["time"].to_numpy("int64"); fv = f["close"].to_numpy("float64")
    fmap = {int(t): v for t, v in zip(ft, fv) if np.isfinite(v)}
    # Null C: funding shifted by a large lag, breaking the causal alignment
    # while preserving the marginal distribution and the autocorrelation.
    shift = 24 * 14
    fmap_shift = {int(ft[i]): fv[i + shift] for i in range(len(ft) - shift)
                  if np.isfinite(fv[i + shift])}

    # realised-vol decile per 1H bar, causal
    ret = np.concatenate(([np.nan], np.diff(np.log(hc))))
    rv = pd.Series(ret).rolling(24 * 7, min_periods=24 * 7).std().shift(1).to_numpy()
    ok = np.isfinite(rv)
    dec = np.full(n, -1, dtype="int64")
    if ok.sum() > 100:
        edges = np.nanquantile(rv[ok][:hi], np.linspace(0, 1, 11)[1:-1])
        dec[ok] = np.searchsorted(edges, rv[ok])

    ent = np.searchsorted(ht, trades["entry_time"].to_numpy("int64"))
    sides = trades["side"].to_numpy("int64")
    rng = np.random.default_rng(seed)
    lo_b, hi_b = 24 * 7 + 1, hi - hold_h - 1
    if hi_b <= lo_b:
        return {k: np.zeros(0) for k in out}

    base = dict(hold_h=hold_h, ho=ho, hc=hc, ht=ht, n=n, interval=interval,
                taker=taker, slip=slip)
    for _ in range(n_sims):
        for idx, side in zip(ent, sides):
            j0 = int(idx)
            # A: same side, same holding, a random bar in the SAME vol decile
            if lo_b <= j0 < hi_b and dec[j0] >= 0:
                pool = np.flatnonzero(dec[lo_b:hi_b] == dec[j0]) + lo_b
                if pool.size > 1:
                    r = _one(int(rng.choice(pool)), int(side), fmap=fmap, **base)
                    if r:
                        out["A"].append(r["net_bps"])
            # B: real timing, random side
            if lo_b <= j0 < hi_b:
                r = _one(j0, int(rng.choice([-1, 1])), fmap=fmap, **base)
                if r:
                    out["B"].append(r["net_bps"])
                # C: real timing and side, funding series decoupled from price
                r = _one(j0, int(side), fmap=fmap_shift, **base)
                if r:
                    out["C"].append(r["net_bps"])
    return {k: np.asarray(v, dtype="float64") for k, v in out.items()}
