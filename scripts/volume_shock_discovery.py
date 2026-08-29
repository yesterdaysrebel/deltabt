"""Volume-shock DISCOVERY gate. Frozen spec: docs/volume_shock_discovery_spec.md.

DISCOVERY, NOT VALIDATION. Nothing here may be promoted to a finding. A
candidate that survives this gate must then be frozen and re-tested on a
window this code has not touched. The separation is the point.

NO PRIOR DEFINITION EXISTED. `volume_shock` returns zero matches across all 24
branches and the registry. H-Compress-1's `volume >= 1.5x 20-bar average` is a
conjunct of a rejected breakout entry, never evaluated alone, and is
deliberately not adopted -- see the spec.

THE CONFOUND THIS IS BUILT AROUND
    Volume and volatility cluster together. "High volume predicts large
    subsequent moves" is nearly true by construction if the shock simply marks
    a period when volatility was already elevated. The volatility-normalised
    endpoint is therefore pre-specified rather than added later, and the
    decision rule requires it: a raw ratio without a normalised one is
    volatility clustering wearing a volume label.

WHAT IS DELIBERATELY REFUSED
    A trailing window is valid only if it contains 1440 bars at CONSECUTIVE
    one-minute timestamps. The series has real gaps (169 on BTCUSD), and
    spanning one would silently compare volume across a discontinuity. Same
    for the outcome: the bar at exactly t + h*60 must exist, or the event is
    excluded rather than measured against whatever bar happens to be next.

No P&L. No costs. No execution. No options data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.config import CACHE_DIR, OUT_DIR

# ---- frozen parameters (docs/volume_shock_discovery_spec.md) ----------------
LOOKBACK = 1440                     # bars, 24h
RVOL_THRESHOLD = 5.0                # C1
LOGZ_THRESHOLD = 4.0                # C2
HORIZONS = (15, 30, 60)             # minutes
PRIMARY_HORIZON = 30
COOLDOWN_MIN = 60                   # = max horizon
PRIMARY_SYMBOLS = ("BTCUSD", "ETHUSD")
ROBUSTNESS_SYMBOLS = ("SOLUSD", "XRPUSD")
EXPLORATORY_SYMBOLS = ("BEATUSD",)
BLOCK_MIN = 60
N_PERM = 10_000
N_BOOT = 10_000

OUT = OUT_DIR / "volshock"


# ------------------------------------------------------------------ features

def load(symbol: str) -> pd.DataFrame:
    d = pd.read_parquet(CACHE_DIR / symbol / "ltp_1m.parquet")
    return d.sort_values("time").reset_index(drop=True)


def build(df: pd.DataFrame, lookback: int = LOOKBACK) -> pd.DataFrame:
    """Causal features. Every trailing statistic ends at t-1 and excludes t.

    `.shift(1)` after the rolling window is what makes that true: without it
    the bar's own volume sits inside its own baseline and every shock is
    partly self-referential.
    """
    d = df.copy()
    v = d["volume"].astype(float)
    logv = np.log1p(v)

    d["trail_med"] = v.rolling(lookback).median().shift(1)
    d["trail_logmean"] = logv.rolling(lookback).mean().shift(1)
    d["trail_logstd"] = logv.rolling(lookback).std(ddof=1).shift(1)

    d["rvol_median"] = np.where(d["trail_med"] > 0, v / d["trail_med"], np.nan)
    d["logvol_z"] = np.where(
        d["trail_logstd"] > 0,
        (logv - d["trail_logmean"]) / d["trail_logstd"], np.nan)

    logret = np.log(d["close"]).diff()
    d["sigma_trail"] = logret.rolling(lookback).std(ddof=1).shift(1)

    # CONTIGUITY. A window is valid only if the previous `lookback` bars sit on
    # consecutive one-minute timestamps. Rolling over a gap would compare
    # volume across a discontinuity.
    step_ok = d["time"].diff().eq(60)
    d["window_ok"] = step_ok.rolling(lookback).min().shift(1).eq(1.0)
    return d


def outcomes(d: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
    """|log return| over each horizon, requiring the exact future bar to exist."""
    t = d["time"].to_numpy()
    close = d["close"].to_numpy(dtype=float)
    pos = {int(x): i for i, x in enumerate(t)}
    for h in horizons:
        idx = np.array([pos.get(int(x) + h * 60, -1) for x in t])
        ok = idx >= 0
        r = np.full(len(d), np.nan)
        r[ok] = np.abs(np.log(close[idx[ok]] / close[ok]))
        d[f"r{h}"] = r
        d[f"rn{h}"] = r / (d["sigma_trail"].to_numpy() * np.sqrt(h))
    return d


def events(d: pd.DataFrame, column: str, threshold: float,
           cooldown: int = COOLDOWN_MIN) -> np.ndarray:
    """Boolean event mask with episode de-duplication.

    Consecutive shock bars are one episode: after firing at t nothing fires
    again until t + cooldown, so two events never share an outcome window and
    one price move is never counted twice.
    """
    cand = (d[column] >= threshold).to_numpy() & d["window_ok"].to_numpy()
    t = d["time"].to_numpy()
    out = np.zeros(len(d), dtype=bool)
    last = -np.inf
    for i in np.flatnonzero(cand):
        if t[i] - last >= cooldown * 60:
            out[i] = True
            last = t[i]
    return out


def baseline_mask(d: pd.DataFrame, ev: np.ndarray,
                  cooldown: int = COOLDOWN_MIN) -> np.ndarray:
    """Non-event bars, excluding the `cooldown` window AFTER any event.

    Leaving post-event bars in the baseline would load the comparison
    denominator with the very effect under test.
    """
    t = d["time"].to_numpy()
    ev_t = t[ev]
    contaminated = np.zeros(len(d), dtype=bool)
    if len(ev_t):
        j = np.searchsorted(ev_t, t, side="right") - 1
        near = j >= 0
        contaminated[near] = (t[near] - ev_t[j[near]]) <= cooldown * 60
    return (~ev) & (~contaminated) & d["window_ok"].to_numpy()


# ----------------------------------------------------------------- statistics

def _ratio(shock: np.ndarray, base: np.ndarray) -> float:
    mb = np.median(base)
    return float(np.median(shock) / mb) if mb > 0 else float("nan")


def block_permutation_p(values: np.ndarray, is_event: np.ndarray,
                        block: int, n_perm: int, seed: int = 17) -> float:
    """One-sided p for ratio_observed under a circular block label shuffle.

    Labels move in contiguous blocks so the permuted 'events' inherit the same
    serial dependence as the real ones. Shuffling bar-by-bar would destroy it
    and manufacture significance.
    """
    rng = np.random.default_rng(seed)
    n = len(values)
    obs = _ratio(values[is_event], values[~is_event])
    if not np.isfinite(obs):
        return float("nan")
    nb = int(np.ceil(n / block))
    # EFFICIENCY, NOT A DESIGN CHANGE. The declared test shuffles labels in
    # contiguous blocks and recomputes the ratio. Recomputing the BASELINE
    # median each time means a median over ~800k values per permutation, which
    # is 8e9 operations. Under a label shuffle the baseline set changes by only
    # n_event elements out of ~800k, so its median is fixed to well beyond the
    # precision of the result. Held constant here and asserted in the test
    # suite against the exact form on a subsample.
    base_med = float(np.median(values[~is_event]))
    if not base_med > 0:
        return float("nan")
    # Vectorised block shuffle. The obvious loop rebuilds the index with ~13k
    # python-level aranges per permutation, which is 100x slower than the
    # statistics it feeds. Reshaping the whole-block prefix into (nb, block)
    # and permuting rows is the same operation.
    whole = n // block
    grid = np.arange(whole * block).reshape(whole, block)
    tail = np.arange(whole * block, n)
    hits = 0
    for _ in range(n_perm):
        order = grid[rng.permutation(whole)].ravel()
        if tail.size:
            order = np.concatenate([order, tail])
        lab = np.roll(is_event[order], int(rng.integers(0, n)))
        if not lab.any():
            continue
        if np.median(values[lab]) / base_med >= obs:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def block_bootstrap_ci(shock: np.ndarray, base: np.ndarray,
                       n_boot: int, seed: int = 23) -> tuple[float, float]:
    """Percentile CI on the median ratio, resampling each group with replacement."""
    rng = np.random.default_rng(seed)
    # The baseline resample is capped at 50k draws. The median's standard error
    # scales as 1/sqrt(n), so drawing 50k instead of ~800k OVERSTATES the
    # baseline's contribution and widens the interval -- the conservative
    # direction. The shock group, which dominates the uncertainty at n of a few
    # hundred, is resampled in full.
    cap = min(len(base), 50_000)
    s_idx = rng.integers(0, len(shock), (n_boot, len(shock)))
    out = np.empty(n_boot)
    for b in range(n_boot):
        mz = np.median(base[rng.integers(0, len(base), cap)])
        out[b] = np.median(shock[s_idx[b]]) / mz if mz > 0 else np.nan
    out = out[np.isfinite(out)]
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


@dataclass
class Cell:
    symbol: str
    definition: str
    horizon: int
    endpoint: str
    n_shock: int
    n_base: int
    median_shock: float
    median_base: float
    mean_shock: float
    mean_base: float
    ratio_median: float
    ratio_mean: float
    ci_low: float = float("nan")
    ci_high: float = float("nan")
    p_perm: float = float("nan")
    tier: str = "exploratory"
    excluded_no_outcome: int = 0


def measure(d: pd.DataFrame, symbol: str, definition: str, column: str,
            threshold: float, horizon: int, endpoint: str, *,
            tier: str, heavy: bool) -> Cell:
    ev = events(d, column, threshold)
    bs = baseline_mask(d, ev)
    col = f"{'rn' if endpoint == 'normalised' else 'r'}{horizon}"
    y = d[col].to_numpy(dtype=float)
    ok = np.isfinite(y)
    excluded = int((ev & ~ok).sum())
    s, b = y[ev & ok], y[bs & ok]
    if len(s) < 20 or len(b) < 100:
        return Cell(symbol, definition, horizon, endpoint, len(s), len(b),
                    *[float("nan")] * 6, tier=tier, excluded_no_outcome=excluded)
    c = Cell(symbol, definition, horizon, endpoint, len(s), len(b),
             float(np.median(s)), float(np.median(b)),
             float(np.mean(s)), float(np.mean(b)),
             _ratio(s, b), float(np.mean(s) / np.mean(b)),
             tier=tier, excluded_no_outcome=excluded)
    if heavy:
        sub = ev | bs
        c.p_perm = block_permutation_p(y[sub & ok], ev[sub & ok],
                                       BLOCK_MIN, N_PERM)
        c.ci_low, c.ci_high = block_bootstrap_ci(s, b, N_BOOT)
    return c
