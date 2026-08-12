"""Experiment 8 — cross-market lead/lag diagnostics (Delta India vs Binance).

This module contains ONLY the mandatory §13 diagnostic stage. No trading logic
is implemented here on purpose: the pre-registration requires establishing that
the phenomenon exists before any strategy is written, and if the lead/lag is
absent the correct action is to stop.

RESOLUTION LIMIT, stated before any result:
    Delta India serves 1-minute bars as its finest historical granularity, and
    no historical trades, bid/ask or order book. Binance spot offers 1s, but a
    comparison is bounded by the slower side, so every measurement here is at
    1-MINUTE resolution. Lags below one minute are unobservable. Conclusions
    about millisecond or second-scale latency CANNOT be drawn from this data.

WHAT THAT DOES AND DOES NOT COST US:
    Sub-minute lead/lag plausibly exists on a venue like this, and this
    experiment is blind to it. It is also uncapturable without colocation. The
    >=1-minute region measured here is the region a retail operator could
    actually act in.

QUOTE-ASSET CAVEAT:
    Delta quotes and settles in USD (INR banking rails); Binance in USDT. A
    price LEVEL spread therefore embeds a USDT/USD basis. Log RETURNS remove
    the level but not changes in the basis, so a persistent basis drift would
    appear as a slow common component, not as a lead/lag.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DELTA_TO_BINANCE = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT"}
MAX_LAG_MIN = 10


@dataclass
class QualityReport:
    symbol: str
    delta_bars: int
    ext_bars: int
    aligned: int
    delta_only: int
    ext_only: int
    delta_synthetic: int
    delta_stale: int
    ext_stale: int
    dup_delta: int
    dup_ext: int
    gaps_delta: int
    gaps_ext: int
    first: int
    last: int

    def as_dict(self) -> dict:
        return self.__dict__


def align(delta_1m: pd.DataFrame, ext_1m: pd.DataFrame, start: int,
          end: int | None = None) -> tuple[pd.DataFrame, QualityReport]:
    """Inner-join on identical UTC minute stamps. Nothing is forward-filled.

    Both feeds use unix seconds at bar OPEN, UTC, so alignment is exact rather
    than approximate. Bars present on only one venue are dropped and counted.
    """
    d = delta_1m[delta_1m.time >= start].copy()
    e = ext_1m[ext_1m.time >= start].copy()
    if end is not None:
        d = d[d.time <= end]; e = e[e.time <= end]

    dup_d = int(d.time.duplicated().sum()); dup_e = int(e.time.duplicated().sum())
    d = d.drop_duplicates("time"); e = e.drop_duplicates("time")

    # stale = consecutive identical close with no volume (Delta forward-fills)
    d_stale = int(((d.close.diff() == 0) & (d.volume == 0)).sum())
    e_stale = int(((e.close.diff() == 0) & (e.volume == 0)).sum())
    d_syn = int(((d.high == d.low) & (d.volume == 0)).sum())

    gd = int((d.time.diff().dropna() > 60).sum())
    ge = int((e.time.diff().dropna() > 60).sum())

    m = d.merge(e, on="time", how="inner", suffixes=("_d", "_e"))
    q = QualityReport(
        symbol="", delta_bars=len(d), ext_bars=len(e), aligned=len(m),
        delta_only=len(d) - len(m), ext_only=len(e) - len(m),
        delta_synthetic=d_syn, delta_stale=d_stale, ext_stale=e_stale,
        dup_delta=dup_d, dup_ext=dup_e, gaps_delta=gd, gaps_ext=ge,
        first=int(m.time.iloc[0]) if len(m) else 0,
        last=int(m.time.iloc[-1]) if len(m) else 0,
    )
    return m, q


def tradable_mask(m: pd.DataFrame) -> np.ndarray:
    """Bars on which a Delta order could plausibly have been filled.

    Excludes Delta forward-filled bars and any bar where either feed shows a
    zero-volume stall; a lead/lag measured on untraded minutes is an artifact.
    """
    d_ok = (m.volume_d > 0) & ~((m.high_d == m.low_d) & (m.volume_d == 0))
    e_ok = m.volume_e > 0
    return (d_ok & e_ok).to_numpy()


def returns(m: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    rd = np.concatenate(([np.nan], np.diff(np.log(m.close_d.to_numpy("float64")))))
    re = np.concatenate(([np.nan], np.diff(np.log(m.close_e.to_numpy("float64")))))
    return rd, re


def cross_correlation(rd: np.ndarray, re: np.ndarray, mask: np.ndarray,
                      max_lag: int = MAX_LAG_MIN) -> pd.DataFrame:
    """corr(external return at t, Delta return at t+k) for k in [-max_lag, max_lag].

    A positive k with elevated correlation means the EXTERNAL market moved
    first and Delta followed k minutes later -- the hypothesis. A symmetric
    profile peaking at k=0 means the two are simply contemporaneous.
    """
    ok = mask & np.isfinite(rd) & np.isfinite(re)
    rows = []
    for k in range(-max_lag, max_lag + 1):
        if k >= 0:
            a, b = re[:len(re) - k], rd[k:]
            v = ok[:len(ok) - k] & ok[k:]
        else:
            a, b = re[-k:], rd[:len(rd) + k]
            v = ok[-k:] & ok[:len(ok) + k]
        a, b = a[v], b[v]
        if a.size < 100:
            rows.append(dict(lag_min=k, n=int(a.size), corr=np.nan)); continue
        rows.append(dict(lag_min=k, n=int(a.size), corr=float(np.corrcoef(a, b)[0, 1])))
    return pd.DataFrame(rows)


def predictive_regression(rd: np.ndarray, re: np.ndarray, mask: np.ndarray,
                          horizon: int = 1) -> dict:
    """Does the external return predict Delta's NEXT return, beyond contemporaneous?

    Regresses Delta's forward return on the external return at t while
    controlling for Delta's own return at t. The controlled coefficient is the
    quantity of interest: an uncontrolled one is inflated by the shared
    contemporaneous component.

    Newey-West standard errors with a lag of `horizon` handle the overlap.
    """
    n = len(rd)
    ok = mask & np.isfinite(rd) & np.isfinite(re)
    fwd = np.full(n, np.nan)
    if horizon >= 1:
        fwd[:n - horizon] = np.array([
            np.nansum(rd[i + 1:i + 1 + horizon]) for i in range(n - horizon)
        ])
    v = ok & np.isfinite(fwd)
    if v.sum() < 500:
        return dict(n=int(v.sum()), beta=np.nan, t=np.nan, r2=np.nan)

    y = fwd[v]
    X = np.column_stack([np.ones(v.sum()), re[v], rd[v]])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    XtX_inv = np.linalg.pinv(X.T @ X)

    # Newey-West
    L = max(horizon, 1)
    S = (X * resid[:, None]).T @ (X * resid[:, None])
    for l in range(1, L + 1):
        w = 1 - l / (L + 1)
        A = (X[l:] * resid[l:, None]).T @ (X[:-l] * resid[:-l, None])
        S += w * (A + A.T)
    cov = XtX_inv @ S @ XtX_inv
    se = float(np.sqrt(max(cov[1, 1], 0)))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return dict(n=int(v.sum()), beta=float(coef[1]),
                t=float(coef[1] / se) if se > 0 else np.nan,
                r2=float(1 - (resid ** 2).sum() / ss_tot) if ss_tot > 0 else np.nan,
                beta_own=float(coef[2]))


def dislocation_bps(m: pd.DataFrame, mask: np.ndarray, window: int = 1440) -> np.ndarray:
    """Delta vs reference, de-based with a causal rolling mean.

    The raw level difference is dominated by the USDT/USD basis, so it is
    de-meaned with a trailing window ENDING AT t-1. What remains is the
    short-horizon dislocation the hypothesis is about.
    """
    raw = (np.log(m.close_d.to_numpy("float64"))
           - np.log(m.close_e.to_numpy("float64"))) * 1e4
    s = pd.Series(np.where(mask, raw, np.nan))
    base = s.rolling(window, min_periods=window // 2).mean().shift(1)
    return (s - base).to_numpy()


def event_response(rd: np.ndarray, re: np.ndarray, mask: np.ndarray,
                   threshold_bps: float, horizons=(1, 2, 3, 5, 10)) -> pd.DataFrame:
    """Conditional response: after a large EXTERNAL move, what does Delta do next?

    This is the economically relevant form of the question. Signed by the
    direction of the external move, so a positive value means Delta followed.
    """
    thr = threshold_bps / 1e4
    ok = mask & np.isfinite(rd) & np.isfinite(re)
    ev = ok & (np.abs(re) >= thr)
    sign = np.sign(re)
    rows = []
    n = len(rd)
    for h in horizons:
        vals = []
        for i in np.flatnonzero(ev):
            if i + h >= n:
                continue
            seg = rd[i + 1:i + 1 + h]
            if not np.all(np.isfinite(seg)):
                continue
            vals.append(sign[i] * seg.sum() * 1e4)
        a = np.asarray(vals)
        if a.size < 30:
            rows.append(dict(threshold_bps=threshold_bps, horizon_min=h, n=int(a.size),
                             mean_bps=np.nan, t=np.nan)); continue
        se = a.std(ddof=1) / np.sqrt(a.size)
        rows.append(dict(threshold_bps=threshold_bps, horizon_min=h, n=int(a.size),
                         mean_bps=float(a.mean()), median_bps=float(np.median(a)),
                         t=float(a.mean() / se) if se > 0 else np.nan,
                         pct_positive=float((a > 0).mean())))
    return pd.DataFrame(rows)


def contemporaneous_share(rd: np.ndarray, re: np.ndarray, mask: np.ndarray) -> dict:
    """How much of Delta's move happens in the SAME minute as the external move?

    If Delta is already ~fully repriced within the same 1m bar, there is no
    minute-scale lag to trade regardless of what happens at finer resolution.
    """
    ok = mask & np.isfinite(rd) & np.isfinite(re)
    a, b = re[ok], rd[ok]
    if a.size < 500:
        return dict(n=int(a.size))
    beta = float(np.polyfit(a, b, 1)[0])
    return dict(n=int(a.size), corr_same_bar=float(np.corrcoef(a, b)[0, 1]),
                beta_same_bar=beta,
                delta_vol_bps=float(b.std() * 1e4),
                ext_vol_bps=float(a.std() * 1e4))
