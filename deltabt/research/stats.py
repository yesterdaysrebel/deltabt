"""Time-series-aware inference.

Trades on correlated instruments held over overlapping windows are not iid, so
the ordinary t-table overstates significance -- badly. Measured on this
project's own trade distribution (skew 14.3, kurtosis 272), a bootstrap of the
demeaned null gave P(|t| > 1.96) = 14% against a nominal 5%. Everything here
exists to avoid that error.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def stationary_bootstrap_indices(
    n: int, mean_block: float, rng: np.random.Generator, reps: int = 1
) -> np.ndarray:
    """Politis-Romano stationary bootstrap index draws, shape (reps, n).

    Block lengths are geometric with mean ``mean_block``, which keeps the
    resampled series stationary (unlike fixed-length blocks) while preserving
    serial dependence up to roughly the block scale.

    Fully vectorised across replicates: a per-draw Python loop is O(reps*n) in
    interpreted code and dominates runtime once reps reaches the thousands.
    """
    if n <= 0:
        return np.zeros((reps, 0), dtype=np.int64)
    p = 1.0 / max(mean_block, 1.0)

    restart = rng.random((reps, n)) < p
    restart[:, 0] = True
    starts = rng.integers(0, n, size=(reps, n))

    pos = np.arange(n, dtype=np.int64)[None, :]
    # index of the most recent restart at or before each position
    block_start = np.maximum.accumulate(np.where(restart, pos, 0), axis=1)
    base = np.take_along_axis(starts, block_start, axis=1)
    return (base + (pos - block_start)) % n


def _boot_means(
    x: np.ndarray, mean_block: float, n_boot: int, rng: np.random.Generator
) -> np.ndarray:
    """Bootstrap replicate means, chunked to bound peak memory."""
    n = x.size
    # ~64 MB of int64 indices per chunk
    chunk = max(1, min(n_boot, int(8_000_000 // max(n, 1))))
    out = np.empty(n_boot, dtype="float64")
    done = 0
    while done < n_boot:
        r = min(chunk, n_boot - done)
        idx = stationary_bootstrap_indices(n, mean_block, rng, reps=r)
        out[done:done + r] = x[idx].mean(axis=1)
        done += r
    return out


def block_bootstrap_mean(
    x: np.ndarray,
    *,
    mean_block: float = 10.0,
    n_boot: int = 5000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict:
    """Bootstrap CI and a bootstrap t for the mean of a serially dependent series.

    Returns a bootstrap t computed as mean / sd(bootstrap means), which is the
    quantity to compare against the significance bar -- not a t-table value.
    """
    x = np.asarray(x, dtype="float64")
    x = x[np.isfinite(x)]
    n = x.size
    if n < 2:
        return dict(mean=float(x.mean()) if n else np.nan, ci_low=np.nan,
                    ci_high=np.nan, t=np.nan, se=np.nan, n=n)

    rng = np.random.default_rng(seed)
    means = _boot_means(x, mean_block, n_boot, rng)
    se = float(means.std(ddof=1))
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return dict(
        mean=float(x.mean()),
        ci_low=float(lo),
        ci_high=float(hi),
        se=se,
        t=float(x.mean() / se) if se > 0 else np.nan,
        n=n,
    )


def bootstrap_diff(
    a: np.ndarray,
    b: np.ndarray,
    *,
    mean_block: float = 10.0,
    n_boot: int = 5000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict:
    """Bootstrap the difference in means (strategy minus null).

    Resampled independently: the null is a separate simulated population, not a
    paired observation of the same events.
    """
    a = np.asarray(a, dtype="float64"); a = a[np.isfinite(a)]
    b = np.asarray(b, dtype="float64"); b = b[np.isfinite(b)]
    if a.size < 2 or b.size < 2:
        return dict(diff=np.nan, ci_low=np.nan, ci_high=np.nan, t=np.nan)

    rng = np.random.default_rng(seed)
    # A simulated null can run to hundreds of thousands of observations, which
    # buys no precision (its mean is already pinned) but makes the bootstrap
    # allocate tens of GB. Subsample it to a size where its sampling error is
    # negligible relative to the strategy's.
    cap = max(20_000, 4 * a.size)
    if b.size > cap:
        b = rng.choice(b, size=cap, replace=False)
    d = (_boot_means(a, mean_block, n_boot, rng)
         - _boot_means(b, mean_block, n_boot, rng))
    se = float(d.std(ddof=1))
    lo, hi = np.quantile(d, [alpha / 2, 1 - alpha / 2])
    return dict(
        diff=float(a.mean() - b.mean()),
        ci_low=float(lo), ci_high=float(hi), se=se,
        t=float((a.mean() - b.mean()) / se) if se > 0 else np.nan,
    )


def effective_n(returns: pd.DataFrame) -> dict:
    """Effective number of independent instruments from a return panel.

    Three estimators are reported because they disagree by design and the
    spread is informative: the participation ratio is generous, the design
    effect is conservative.
    """
    R = returns.dropna()
    K = R.shape[1]
    if K < 2 or len(R) < 10:
        return dict(K=K, mean_rho=np.nan, pc1=np.nan, pr=float(K), deff=float(K))
    C = R.corr().to_numpy()
    ev = np.clip(np.linalg.eigvalsh(C)[::-1], 0, None)
    t = ev / ev.sum()
    off = C[np.triu_indices(K, 1)]
    rho = float(off.mean())
    return dict(
        K=K,
        mean_rho=rho,
        pc1=float(t[0]),
        pr=float(1.0 / np.sum(t**2)),
        entropy=float(np.exp(-np.sum(np.where(t > 0, t * np.log(t), 0.0)))),
        deff=float(K / (1 + (K - 1) * rho)),
    )


def trade_design_effect(trades: pd.DataFrame, *, cluster_col: str = "cluster") -> dict:
    """Design effect from clustered trades.

    Trades that overlap in time on correlated instruments carry duplicate
    information. DEFF = 1 + (m-1)*rho_intra where m is the mean cluster size;
    effective N is nominal N divided by it.
    """
    if trades.empty or cluster_col not in trades:
        return dict(n=len(trades), n_eff=float(len(trades)), deff=1.0, m=1.0, rho=0.0)

    g = trades.groupby(cluster_col)["r_net"]
    sizes = g.size()
    m = float(sizes.mean())
    if m <= 1.0 or len(sizes) < 2:
        return dict(n=len(trades), n_eff=float(len(trades)), deff=1.0, m=m, rho=0.0)

    # One-way random-effects intra-cluster correlation.
    overall = trades["r_net"].mean()
    between = float(((g.mean() - overall) ** 2 * sizes).sum() / max(len(sizes) - 1, 1))
    within = float(((trades["r_net"] - trades[cluster_col].map(g.mean())) ** 2).sum()
                   / max(len(trades) - len(sizes), 1))
    ms_b, ms_w = between, within
    rho = (ms_b - ms_w) / (ms_b + (m - 1) * ms_w) if (ms_b + (m - 1) * ms_w) > 0 else 0.0
    rho = float(np.clip(rho, 0.0, 1.0))
    deff = 1.0 + (m - 1.0) * rho
    return dict(n=len(trades), n_eff=float(len(trades) / deff), deff=float(deff),
                m=m, rho=rho)


def sharpe_sortino(returns: np.ndarray, periods_per_year: float) -> tuple[float, float]:
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    if r.size < 2 or r.std(ddof=1) == 0:
        return (np.nan, np.nan)
    ann = np.sqrt(periods_per_year)
    sharpe = float(r.mean() / r.std(ddof=1) * ann)
    downside = r[r < 0]
    sortino = (float(r.mean() / downside.std(ddof=1) * ann)
               if downside.size > 1 and downside.std(ddof=1) > 0 else np.nan)
    return sharpe, sortino


def max_drawdown(equity: np.ndarray) -> float:
    e = np.asarray(equity, dtype="float64")
    if e.size == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    return float(np.max(peak - e))
