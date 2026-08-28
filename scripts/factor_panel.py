"""Every documented cross-sectional crypto factor, tested at once.

WHY ALL AT ONCE, AND WHY THAT MATTERS MORE THAN THE FACTORS
    This repository has repeatedly found a "result" by running many cells and
    reading the best one: XRPUSD@45m looked significant until the same cell was
    checked on three other symbols; a 4R target held out of sample at 15m and
    nowhere else; a funding book showed Sharpe 3.7 until turnover was measured
    in dollars. Each was one survivor of a search nobody corrected for.

    So this runs the WHOLE factor set in a single pass and applies
    Benjamini-Hochberg across every test performed. A factor that clears the
    corrected threshold has survived the search that found it. One that only
    clears the raw p-value has not, and is reported as such rather than
    quietly promoted.

THE FACTORS, AND WHERE THEY COME FROM
    Momentum, short-term reversal, size, illiquidity and volatility are the
    predictors documented in the cross-sectional crypto literature (Dobrynskaya
    2023; Zhang et al., "Up or down? Short-term reversal, momentum and
    liquidity effects in cryptocurrency markets", 2021). Carry is added because
    funding is the one cash flow this venue pays directly.

THE CONDITIONAL PREDICTION IS THE INTERESTING ONE
    The literature does not claim momentum works everywhere. It claims momentum
    is concentrated in LIQUID coins while ILLIQUID ones mean-revert. A bare
    factor that works on the pooled universe is a weak result; a factor that
    flips sign between the liquid and illiquid halves in the predicted
    direction is a much stronger one, because chance does not usually produce
    a specific reversal. Both are reported.

WHAT THIS IS
    A backtest on cached daily data. No pre-registration, no entry in
    out/experiments.jsonl, no data fetched at run time.
"""

from __future__ import annotations

import glob
import json
import math
import os

import numpy as np
import pandas as pd

from deltabt.config import CACHE_DIR, OUT_DIR

#: One leg, one way: taker + slippage, matching deltabt.costs.
LEG_COST = 0.00059 + 0.00020

MIN_DAYS = 200
#: Terciles rather than deciles: with ~30 names a decile is three coins and
#: the portfolio is a single-name bet wearing a factor's name.
FRACTION = 1.0 / 3.0


def load_panel() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Daily close, dollar turnover and per-day funding for every symbol."""
    products = json.loads((CACHE_DIR.parent / "meta" / "products.json").read_text())
    close, turn, fund = {}, {}, {}
    for path in sorted(glob.glob(str(CACHE_DIR / "*" / "ltp_1d.parquet"))):
        sym = os.path.basename(os.path.dirname(path))
        d = pd.read_parquet(path)
        if len(d) < MIN_DAYS:
            continue
        d["t"] = pd.to_datetime(d["time"], unit="s", utc=True)
        d = d.set_index("t").sort_index()
        cv = products.get(sym, {}).get("contract_value", 1.0)
        close[sym] = d["close"]
        turn[sym] = d["close"] * d["volume"] * cv        # NOTIONAL, not contracts
        f = CACHE_DIR / sym / "funding_1h.parquet"
        if f.exists():
            r = pd.read_parquet(f)
            r["t"] = pd.to_datetime(r["time"], unit="s", utc=True)
            iv = products.get(sym, {}).get("funding_interval_seconds", 28800)
            s = (r.set_index("t")["close"] / 100.0).resample(
                f"{iv // 3600}h").last().dropna()
            fund[sym] = s.resample("1D").sum()
    px = pd.DataFrame(close).sort_index()
    return px, pd.DataFrame(turn).reindex(px.index), pd.DataFrame(fund).reindex(px.index)


def factors(px: pd.DataFrame, turn: pd.DataFrame,
            fund: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Every signal, shifted so day t uses only information before day t."""
    ret = px.pct_change()
    amihud = (ret.abs() / turn.replace(0, np.nan)).rolling(30).mean()
    return {
        # sign convention: HIGH score -> LONG leg.
        "reversal_1d": -ret,
        "reversal_7d": -ret.rolling(7).sum(),
        "momentum_30d": ret.rolling(30).sum(),
        "momentum_90d": ret.rolling(90).sum(),
        "size_small": -turn.rolling(30).mean(),
        "illiquidity": amihud,
        "low_volatility": -ret.rolling(30).std(),
        "carry": -fund.rolling(7).mean(),
    }


def backtest(score: pd.DataFrame, ret: pd.DataFrame,
             universe: pd.DataFrame) -> pd.Series | None:
    """Daily-rebalanced dollar-neutral tercile spread, net of turnover cost."""
    s = score.shift(1).where(universe.shift(1))
    daily, prev = [], pd.Series(dtype=float)
    for day in s.index:
        row = s.loc[day].dropna()
        if len(row) < 6:
            daily.append((np.nan, np.nan))
            continue
        n = max(1, int(len(row) * FRACTION))
        longs, shorts = row.nlargest(n).index, row.nsmallest(n).index
        w = pd.Series(0.0, index=row.index)
        w[longs] = 0.5 / n
        w[shorts] = -0.5 / n
        turnover = w.subtract(prev, fill_value=0.0).abs().sum()
        r = ret.loc[day].reindex(w.index).fillna(0.0)
        daily.append((float((w * r).sum()), float(turnover * LEG_COST)))
        prev = w
    out = pd.DataFrame(daily, index=s.index, columns=["gross", "cost"]).dropna()
    return out if len(out) > 60 else None


def _two_sided_p(t_stat: float, n: int) -> float:
    """Normal approximation to the t distribution.

    Exact for the purpose: every test here has n > 60 daily observations, where
    the t and normal tails agree to about three decimal places, and the
    Benjamini-Hochberg ranking below depends on the ORDER of the p-values
    rather than their third decimal.
    """
    return math.erfc(abs(t_stat) / math.sqrt(2.0))


def stats(df: pd.DataFrame) -> dict:
    """Gross AND net, because a daily-rebalanced tercile book on 163 names
    turns over ~2x a day. At 7.9 bps a leg that is roughly 57%/yr of drag --
    enough to bury a real factor, so 'no signal' and 'signal minus costs' have
    to be told apart rather than merged into one number."""
    n = len(df)
    net = df["gross"] - df["cost"]
    se = net.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
    t = net.mean() / se if se else float("nan")
    gse = df["gross"].std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
    gt = df["gross"].mean() / gse if gse else float("nan")
    yrs = n / 365.25
    return dict(days=n,
                gross_ann=100 * df["gross"].sum() / yrs,
                cost_ann=100 * df["cost"].sum() / yrs,
                ann_return=100 * net.sum() / yrs,
                ann_vol=100 * net.std() * np.sqrt(365.25),
                sharpe=(net.mean() / net.std() * np.sqrt(365.25)) if net.std() else np.nan,
                gross_t=float(gt),
                t_stat=float(t), p_value=_two_sided_p(t, n) if np.isfinite(t) else 1.0)


def main() -> None:
    px, turn, fund = load_panel()
    ret = px.pct_change()
    print(f"panel: {px.shape[1]} symbols, {px.shape[0]} days, "
          f"{px.index.min().date()} -> {px.index.max().date()}")

    liq = turn.rolling(30).mean()
    median_liq = liq.median(axis=1)
    universes = {
        "all": px.notna(),
        "liquid_half": liq.gt(median_liq, axis=0),
        "illiquid_half": liq.le(median_liq, axis=0),
        "tradeable_1m": liq.ge(1_000_000),
    }

    rows = []
    for fname, score in factors(px, turn, fund).items():
        for uname, mask in universes.items():
            r = backtest(score, ret, mask & px.notna())
            if r is None:
                continue
            rows.append(dict(factor=fname, universe=uname, **stats(r)))

    d = pd.DataFrame(rows).sort_values("p_value")
    # Benjamini-Hochberg across EVERY test performed, not per factor.
    m = len(d)
    d["bh_threshold"] = 0.05 * (np.arange(1, m + 1)) / m
    d["survives_bh"] = d["p_value"].values <= d["bh_threshold"].values
    # BH is a step-up procedure: once a test fails, everything above it fails.
    if d["survives_bh"].any():
        cut = np.where(d["survives_bh"].values)[0].max()
        d["survives_bh"] = [i <= cut for i in range(m)]

    pd.set_option("display.width", 200)
    print(f"\n{m} tests performed. Benjamini-Hochberg at FDR 5%.\n")
    print(d[["factor", "universe", "days", "gross_ann", "cost_ann", "ann_return",
             "sharpe", "gross_t", "t_stat", "p_value", "survives_bh"]].to_string(
        index=False, float_format=lambda x: f"{x:.4f}"))

    print(f"\nsurviving multiple-testing correction: "
          f"{int(d['survives_bh'].sum())} of {m}")
    print(f"raw p < 0.05 (uncorrected, i.e. what a sequential search would "
          f"have 'found'): {int((d['p_value'] < 0.05).sum())}")

    print("\nCONDITIONAL CHECK -- literature predicts momentum in LIQUID names "
          "and reversal in ILLIQUID ones:")
    for f in ("momentum_30d", "momentum_90d", "reversal_1d", "reversal_7d"):
        sub = d[d["factor"] == f].set_index("universe")
        if {"liquid_half", "illiquid_half"} <= set(sub.index):
            print(f"  {f:14s} liquid {sub.loc['liquid_half','ann_return']:+7.2f}%/yr "
                  f"(t={sub.loc['liquid_half','t_stat']:+.2f})   "
                  f"illiquid {sub.loc['illiquid_half','ann_return']:+7.2f}%/yr "
                  f"(t={sub.loc['illiquid_half','t_stat']:+.2f})")

    out = OUT_DIR / "sweep" / "factor_panel.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(out, index=False)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
