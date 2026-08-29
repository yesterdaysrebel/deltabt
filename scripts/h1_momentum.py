"""H1 momentum kill test -- cross-sectional momentum on the broad daily universe.

WHAT THIS IS
    Execution of a frozen kill test. Every parameter below was fixed before any
    return was computed and none may be searched afterwards:

        formation   30d primary; 7d / 90d robustness
        portfolio   long top quintile, short bottom quintile, equal notional
        rebalance   30 days (monthly) for the primary
        liquidity   $1M primary; $500k / $5M robustness
        universe    causal, launch-date respecting, 30d MEDIAN notional

    EXPLORATORY. This window has already been examined by H-XSec-1, by the
    32-cell factor panel and by the 80-cell rebalance sweep. The temporal split
    is a consistency diagnostic, not out-of-sample credibility.

WHAT IS GENUINELY NEW HERE, AND WHAT IS NOT
    NOT new: 30d and 90d cross-sectional momentum on daily data. The factor
    panel ran both against four universes at daily rebalance, and
    slow_strategies ran both at 1/7/30/90-day rebalance. Both used TERCILES and
    a 30-day MEAN turnover screen. Neither survived Benjamini-Hochberg.

    New: quintiles rather than terciles, an explicit causal 30-day MEDIAN
    turnover floor swept over three levels, per-symbol taker fees, Newey-West
    standard errors, a leg census and effective-N, and the three predeclared
    controls. 7d momentum is new only in sign -- the panel ran 7d REVERSAL,
    which is this signal negated, so a 7d momentum gross near -21%/yr is a
    prediction of the existing record and doubles as a wiring check.

COSTS ARE PER SYMBOL, NOT FLAT
    deltabt.costs.SymbolCosts reads taker_fee off the product. This venue is
    not uniform: 186 perps are 0.05% taker, 31 are 0.02% and 3 are 0.01%. A
    flat 0.05% would overstate friction on the cheap tier, which would bias
    this test TOWARD rejection. Per-symbol fees are used so the hypothesis gets
    the cheapest honest cost model the venue actually offers.

        leg cost = taker_fee x 1.18 (GST) + 2 bps slippage, per unit of gross
        notional traded, charged on turnover at every rebalance.

NO SKIP PERIOD
    Canonical equity momentum skips the most recent month to avoid short-term
    reversal contamination. The frozen spec says "return over the predefined
    formation period" with no skip, and adding one would be a free parameter.
    It is therefore NOT applied, and that is recorded rather than chosen.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from deltabt.config import CACHE_DIR, GST_MULTIPLIER, META_DIR

SLIPPAGE = 0.0002
TURNOVER_WINDOW = 30
QUINTILE = 0.20
MIN_UNIVERSE = 5          # both quintiles must exist and be disjoint
DAYS_PER_YEAR = 365.25

FORMATIONS = {"primary": 30, "robust_short": 7, "robust_long": 90}
THRESHOLDS = {"primary": 1_000_000.0, "low": 500_000.0, "high": 5_000_000.0}
REBALANCE = 30

SPLITS = {
    "TRAIN": ("2025-01-01", "2025-12-31"),
    "VALIDATION": ("2026-01-01", "2026-05-31"),
    "TEST": ("2026-06-01", "2026-08-28"),
}


@dataclass
class Panel:
    px: pd.DataFrame
    turnover_med: pd.DataFrame     # causal 30d median notional, known at day t
    listed: pd.DataFrame
    ret: pd.DataFrame
    leg_cost: pd.Series            # per symbol
    symbols: list[str]


def load_panel() -> Panel:
    products = json.loads((META_DIR / "products.json").read_text())
    close, turn, listed, cost = {}, {}, {}, {}
    for sym in sorted(os.listdir(CACHE_DIR)):
        path = CACHE_DIR / sym / "ltp_1d.parquet"
        if not path.exists() or sym not in products:
            continue
        p = products[sym]
        d = pd.read_parquet(path)
        d.index = pd.to_datetime(d["time"], unit="s", utc=True)
        d = d.sort_index()
        close[sym] = d["close"]
        turn[sym] = notional_usd(d["close"], d["volume"], float(p["contract_value"]))
        listed[sym] = pd.Timestamp(p["launch_time"]).tz_convert("UTC")
        cost[sym] = float(p["taker_fee"]) * GST_MULTIPLIER + SLIPPAGE
    px = pd.DataFrame(close).sort_index()
    tn = pd.DataFrame(turn).reindex(px.index)
    syms = list(px.columns)
    return Panel(
        px=px,
        # .shift(1): the median known at day t uses bars through day t-1 only.
        turnover_med=tn.rolling(TURNOVER_WINDOW).median().shift(1),
        listed=pd.DataFrame({s: px.index >= listed[s] for s in syms}, index=px.index),
        ret=px.pct_change(),
        leg_cost=pd.Series(cost),
        symbols=syms,
    )


def notional_usd(close: pd.Series, volume: pd.Series, contract_value: float) -> pd.Series:
    """Daily turnover in DOLLARS. `close * volume` is CONTRACTS."""
    return close * volume * contract_value


def momentum(px: pd.DataFrame, formation: int) -> pd.DataFrame:
    """Formation-period return, shifted so day t uses only prices before day t.

    ``pct_change(formation)`` at row t-1 spans close(t-1-F) -> close(t-1), both
    strictly before the day-t trade. No skip period; see the module docstring.
    """
    return px.pct_change(formation).shift(1)


def eligible(panel: Panel, day: pd.Timestamp, score: pd.DataFrame,
             threshold: float) -> pd.Series:
    med = panel.turnover_med.loc[day]
    ok = panel.listed.loc[day] & med.notna() & (med >= threshold)
    return score.loc[day].where(ok).dropna()


def _weights(sig: pd.Series, mode: str, rng) -> pd.Series:
    """Frozen construction. `mode` only scrambles or flips the ranking."""
    n_side = max(1, int(round(len(sig) * QUINTILE)))
    if mode == "random":
        sig = pd.Series(rng.permutation(sig.to_numpy()), index=sig.index)
    elif mode == "reverse":
        sig = -sig
    elif mode == "tsmom":
        # Time-series control: sign of own past return, gross normalised to 1.
        s = np.sign(sig)
        s = s[s != 0]
        return s / len(s) if len(s) else pd.Series(dtype=float)
    elif mode not in ("real", "long_only"):
        raise ValueError(mode)
    ranked = sig.sort_values(ascending=False)
    longs, shorts = ranked.index[:n_side], ranked.index[-n_side:]
    if mode == "long_only":
        return pd.Series(1.0 / n_side, index=longs)
    w = pd.Series(0.0, index=list(longs) + list(shorts))
    w[longs] = 0.5 / n_side
    w[shorts] = -0.5 / n_side
    return w


def backtest(panel: Panel, formation: int, threshold: float,
             rebalance: int = REBALANCE, mode: str = "real",
             seed: int = 0) -> dict:
    score = momentum(panel.px, formation)
    rng = np.random.default_rng(seed)
    rows, census, sizing = [], [], []
    held = pd.Series(dtype=float)
    for i, day in enumerate(panel.px.index):
        cost = 0.0
        if i % rebalance == 0:
            sig = eligible(panel, day, score, threshold)
            new = (_weights(sig, mode, rng) if len(sig) >= MIN_UNIVERSE
                   else pd.Series(dtype=float))
            delta = new.subtract(held, fill_value=0.0).abs()
            cost = float((delta * panel.leg_cost.reindex(delta.index)).sum())
            held = new
            sizing.append(dict(day=day, n_eligible=len(sig), n_side=len(new)))
            for s, w in held.items():
                census.append(dict(day=day, symbol=s,
                                   side="LONG" if w > 0 else "SHORT"))
        if not len(held):
            continue
        r = panel.ret.loc[day].reindex(held.index).fillna(0.0)
        rows.append(dict(day=day, gross=float((held * r).sum()), cost=cost,
                         turnover=float(held.abs().sum()),
                         net=float((held * r).sum()) - cost,
                         n=len(held)))
    daily = pd.DataFrame(rows).set_index("day") if rows else pd.DataFrame()
    return dict(daily=daily, census=pd.DataFrame(census),
                sizing=pd.DataFrame(sizing))


# ------------------------------------------------------------------ statistics

def newey_west_t(x: np.ndarray, lag: int) -> float:
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return float("nan")
    e = x - x.mean()
    var = float(e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        var += 2.0 * (1.0 - k / (lag + 1.0)) * float(e[k:] @ e[:-k]) / n
    return float(x.mean() / np.sqrt(var / n)) if var > 0 else float("nan")


def one_sided_p(t: float) -> float:
    """H1 is one-sided: mean long-short return > 0."""
    import math
    if not np.isfinite(t):
        return float("nan")
    return float(0.5 * math.erfc(t / math.sqrt(2.0)))


def stationary_bootstrap(x: np.ndarray, mean_block: int, n_boot: int,
                         seed: int) -> tuple[float, float]:
    x = np.asarray(x, dtype=float)
    n = len(x)
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    starts = rng.integers(0, n, size=(n_boot, n))
    jumps = rng.random((n_boot, n)) < p
    out = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=np.int64)
        cur = starts[b, 0]
        for i in range(n):
            if i:
                cur = starts[b, i] if jumps[b, i] else (cur + 1) % n
            idx[i] = cur
        s = x[idx]
        sd = s.std(ddof=1)
        out[b] = s.mean() / sd * np.sqrt(DAYS_PER_YEAR) if sd > 0 else np.nan
    out = out[np.isfinite(out)]
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def summarise(daily: pd.DataFrame, rebalance: int, *, boot: bool = False,
              seed: int = 11) -> dict:
    if daily is None or not len(daily):
        return {"days": 0}
    yrs = len(daily) / DAYS_PER_YEAR
    net = daily["net"]
    vol = float(net.std(ddof=1) * np.sqrt(DAYS_PER_YEAR))
    eq = net.cumsum()
    out = dict(
        days=int(len(daily)),
        rebalances=int((daily["cost"] > 0).sum()),
        gross_ann=float(daily["gross"].sum() / yrs),
        cost_ann=float(daily["cost"].sum() / yrs),
        net_ann=float(net.sum() / yrs),
        vol_ann=vol,
        sharpe=float(net.sum() / yrs / vol) if vol > 0 else float("nan"),
        max_drawdown=float((eq - eq.cummax()).min()),
        skew=float(net.skew()),
        turnover_ann=float(daily["cost"].gt(0).sum() / yrs * 2.0),
        nw_t=newey_west_t(net.to_numpy(), lag=2 * rebalance),
        gross_nw_t=newey_west_t(daily["gross"].to_numpy(), lag=2 * rebalance),
    )
    out["p_value_one_sided"] = one_sided_p(out["nw_t"])
    if boot:
        lo, hi = stationary_bootstrap(net.to_numpy(), rebalance, 10_000, seed)
        out["sharpe_ci"] = [lo, hi]
    return out
