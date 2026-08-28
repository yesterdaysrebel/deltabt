"""Contemporaneous-volatility control. Spec: docs/volume_shock_control_spec.md.

A KILL TEST, NOT A VALIDATION. No untouched historical window exists -- the
discovery reported chronological thirds across the whole span, so those numbers
have been seen. The asymmetry is what makes this worth running anyway: a
negative result is dispositive, because sample reuse cannot manufacture the
DISAPPEARANCE of an effect, while a positive result licenses nothing beyond
freezing these parameters for a forward test.

THE ALTERNATIVE EXPLANATION BEING TESTED
    Discovery measured trailing volatility as LOWER at shock times than at
    baseline on every symbol. So the volatility-normalised effect may be
    nothing more than a trailing estimator lagging a regime change that volume
    detects first. If that is the whole story, the effect must vanish once
    shocks are compared only against non-shock bars at the SAME contemporaneous
    volatility.

WHY sigma_contemp IS NOT LOOK-AHEAD
    Bar `t` opens at t and closes at t+60s. The event is classified from
    volume(t), known only when that bar closes, so the decision instant is
    t+60s. The outcome runs close(t) -> close(t+h), i.e. t+60s -> t+h*60+60s,
    strictly after. sigma_contemp uses closes through t+60s: exactly the
    information available at the decision instant and none beyond it.

Event definition, cooldown, baseline and validity are imported UNCHANGED from
the discovery module. Nothing about the phenomenon's definition moves here --
only what it is compared against.

No P&L. No costs. No execution. No options data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import volume_shock_discovery as vs

CONTEMP_BARS = 15          # frozen
N_DECILES = 10             # frozen
MIN_SHOCKS_PER_STRATUM = 30
N_BOOT = 10_000
N_PERM = 10_000


def add_contemp_vol(d: pd.DataFrame, bars: int = CONTEMP_BARS) -> pd.DataFrame:
    """Realised vol over the last `bars` bars INCLUSIVE of t.

    No `.shift(1)` here, deliberately, and it is the one place in this
    programme where that is correct: the decision instant is the close of bar
    t, so bar t's own return is available. The discovery's trailing features
    keep their shift because they define the event; this conditions on it.
    """
    d = d.copy()
    logret = np.log(d["close"]).diff()
    d["sigma_contemp"] = logret.rolling(bars).std(ddof=1)
    return d


def strata(sigma: np.ndarray, mask: np.ndarray,
           n: int = N_DECILES) -> tuple[np.ndarray, np.ndarray]:
    """Decile edges from the pooled usable sample, and each bar's bucket."""
    pool = sigma[mask & np.isfinite(sigma)]
    edges = np.quantile(pool, np.linspace(0, 1, n + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    return edges, np.digitize(sigma, edges[1:-1], right=False)


def _weighted_ratio(y: np.ndarray, ev: np.ndarray, bs: np.ndarray,
                    bucket: np.ndarray) -> tuple[float, list[dict]]:
    rows, num, den = [], 0.0, 0.0
    for b in range(N_DECILES):
        s = y[ev & (bucket == b)]
        z = y[bs & (bucket == b)]
        if len(s) == 0 or len(z) == 0:
            continue
        mz = float(np.median(z))
        r = float(np.median(s) / mz) if mz > 0 else float("nan")
        used = len(s) >= MIN_SHOCKS_PER_STRATUM and np.isfinite(r)
        rows.append({"decile": b, "n_shock": int(len(s)), "n_base": int(len(z)),
                     "median_shock": float(np.median(s)), "median_base": mz,
                     "ratio": r, "used": bool(used)})
        if used:
            num += r * len(s)
            den += len(s)
    return (num / den if den else float("nan")), rows


def stratified_bootstrap(y, ev, bs, bucket, n_boot=N_BOOT, seed=31):
    """Resample shocks and baseline WITHIN each decile, preserving the strata."""
    rng = np.random.default_rng(seed)
    idx = []
    for b in range(N_DECILES):
        s = np.flatnonzero(ev & (bucket == b))
        z = np.flatnonzero(bs & (bucket == b))
        if len(s) >= MIN_SHOCKS_PER_STRATUM and len(z) > 0:
            idx.append((s, z))
    if not idx:
        return float("nan"), float("nan")
    out = np.empty(n_boot)
    for i in range(n_boot):
        num = den = 0.0
        for s, z in idx:
            ms = np.median(y[s[rng.integers(0, len(s), len(s))]])
            mz = np.median(y[z[rng.integers(0, len(z), min(len(z), 20_000))]])
            if mz > 0:
                num += (ms / mz) * len(s)
                den += len(s)
        out[i] = num / den if den else np.nan
    out = out[np.isfinite(out)]
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def stratified_permutation_p(y, ev, bs, bucket, n_perm=N_PERM, seed=37):
    """Shuffle the shock label WITHIN each decile.

    The null therefore preserves the sigma_contemp distribution exactly, which
    is the whole point: a permutation that ignored the strata would recreate
    the confound it is meant to remove.
    """
    rng = np.random.default_rng(seed)
    obs, _ = _weighted_ratio(y, ev, bs, bucket)
    if not np.isfinite(obs):
        return float("nan")
    pools = []
    for b in range(N_DECILES):
        pool = np.flatnonzero((ev | bs) & (bucket == b))
        k = int((ev & (bucket == b)).sum())
        if k >= MIN_SHOCKS_PER_STRATUM and len(pool) > k:
            pools.append((pool, k))
    if not pools:
        return float("nan")
    hits = 0
    for _ in range(n_perm):
        num = den = 0.0
        for pool, k in pools:
            pick = rng.choice(len(pool), k, replace=False)
            m = np.zeros(len(pool), dtype=bool)
            m[pick] = True
            vals = y[pool]
            mz = np.median(vals[~m])
            if mz > 0:
                num += (np.median(vals[m]) / mz) * k
                den += k
        if den and (num / den) >= obs:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def control(d: pd.DataFrame, horizon: int, endpoint: str = "raw", *,
            heavy: bool = True) -> dict:
    """Full stratified comparison for one symbol/horizon/endpoint."""
    ev = vs.events(d, "rvol_median", vs.RVOL_THRESHOLD)
    bs = vs.baseline_mask(d, ev)
    col = f"{'rn' if endpoint == 'normalised' else 'r'}{horizon}"
    y = d[col].to_numpy(dtype=float)
    sig = d["sigma_contemp"].to_numpy(dtype=float)
    ok = np.isfinite(y) & np.isfinite(sig)
    ev, bs = ev & ok, bs & ok
    _, bucket = strata(sig, ev | bs)

    unstrat_base = float(np.median(y[bs]))
    unstrat = float(np.median(y[ev]) / unstrat_base) if unstrat_base > 0 else float("nan")
    wr, rows = _weighted_ratio(y, ev, bs, bucket)
    res = {"horizon": horizon, "endpoint": endpoint,
           "n_shock": int(ev.sum()), "n_base": int(bs.sum()),
           "unstratified_ratio": unstrat, "stratified_ratio": wr,
           "deciles": rows}
    if heavy:
        res["ci_low"], res["ci_high"] = stratified_bootstrap(y, ev, bs, bucket)
        res["p_perm"] = stratified_permutation_p(y, ev, bs, bucket)
    return res
