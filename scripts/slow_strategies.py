"""Does trading less often rescue anything? Rebalance frequency as a dimension.

THE QUESTION THIS ANSWERS
    scripts/factor_panel.py found short-term reversal at a GROSS t-statistic of
    4.01 and a daily-rebalancing cost of 41%/yr that removed all of it. Cost
    scales with turnover and turnover scales with rebalance frequency, so the
    obvious move is to hold longer. Every factor in that panel was rebalanced
    DAILY; none was ever tested at weekly, monthly or quarterly.

    That is not a variation on something already documented -- it is the one
    dimension the panel held fixed, and it is the dimension the cost problem
    lives in.

TIME-SERIES MOMENTUM IS ALSO NEW HERE
    out/experiments.jsonl contains H-XSec-1, CROSS-SECTIONAL momentum: rank
    symbols against each other, long the winners, short the losers. Nothing in
    this repository has tested TIME-SERIES momentum -- each symbol judged
    against its own past, long if it is up over the lookback and short if it is
    down, which is the canonical managed-futures rule and a different bet. A
    cross-sectional book is market-neutral by construction; a time-series one
    takes a net direction, and in a market that trended hard over this sample
    those are not the same test.

WHAT KEEPS THIS HONEST
    Every configuration is run in a single pass and Benjamini-Hochberg is
    applied across all of them at once. Running four rebalance frequencies is
    four more chances to find a survivor, and the correction is what stops that
    from becoming a discovery.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR

_spec = importlib.util.spec_from_file_location(
    "factor_panel", pathlib.Path(__file__).with_name("factor_panel.py"))
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)

LEG_COST = fp.LEG_COST
FRACTION = fp.FRACTION


def backtest(score: pd.DataFrame, ret: pd.DataFrame, universe: pd.DataFrame,
             rebalance_days: int) -> pd.DataFrame | None:
    """Tercile spread rebalanced every ``rebalance_days``, held in between."""
    s = score.shift(1).where(universe.shift(1))
    daily, prev, held = [], pd.Series(dtype=float), pd.Series(dtype=float)
    for i, day in enumerate(s.index):
        if i % rebalance_days == 0:
            row = s.loc[day].dropna()
            if len(row) >= 6:
                n = max(1, int(len(row) * FRACTION))
                w = pd.Series(0.0, index=row.index)
                w[row.nlargest(n).index] = 0.5 / n
                w[row.nsmallest(n).index] = -0.5 / n
                held = w
            else:
                held = pd.Series(dtype=float)
            cost = held.subtract(prev, fill_value=0.0).abs().sum() * LEG_COST
            prev = held
        else:
            cost = 0.0
        if not len(held):
            daily.append((np.nan, np.nan))
            continue
        r = ret.loc[day].reindex(held.index).fillna(0.0)
        daily.append((float((held * r).sum()), float(cost)))
    out = pd.DataFrame(daily, index=s.index, columns=["gross", "cost"]).dropna()
    return out if len(out) > 60 else None


def tsmom(ret: pd.DataFrame, universe: pd.DataFrame, lookback: int,
          rebalance_days: int) -> pd.DataFrame | None:
    """Time-series momentum: each symbol against its OWN past, not its peers.

    Equal risk per name via inverse trailing vol, then scaled so gross exposure
    is 1.0 -- otherwise a 100-name panel is a 100x leveraged bet and the
    returns are uninterpretable.
    """
    signal = np.sign(ret.rolling(lookback).sum()).shift(1).where(universe.shift(1))
    sigma = ret.rolling(30).std().shift(1)
    daily, prev, held = [], pd.Series(dtype=float), pd.Series(dtype=float)
    for i, day in enumerate(signal.index):
        if i % rebalance_days == 0:
            sig = signal.loc[day].dropna()
            sg = sigma.loc[day].reindex(sig.index)
            ok = sig[(sig != 0) & np.isfinite(sg) & (sg > 0)]
            if len(ok) >= 6:
                w = ok * (1.0 / sg.reindex(ok.index))
                held = w / w.abs().sum()
            else:
                held = pd.Series(dtype=float)
            cost = held.subtract(prev, fill_value=0.0).abs().sum() * LEG_COST
            prev = held
        else:
            cost = 0.0
        if not len(held):
            daily.append((np.nan, np.nan))
            continue
        r = ret.loc[day].reindex(held.index).fillna(0.0)
        daily.append((float((held * r).sum()), float(cost)))
    out = pd.DataFrame(daily, index=signal.index,
                       columns=["gross", "cost"]).dropna()
    return out if len(out) > 60 else None


def main() -> None:
    px, turn, fund = fp.load_panel()
    ret = px.pct_change()
    liq = turn.rolling(30).mean()
    universes = {
        "all": px.notna(),
        # The lesson from the funding book and the reversal factor: a result on
        # names you cannot exit is not a result. This is the only universe
        # whose numbers mean anything operationally.
        "tradeable_1m": liq.ge(1_000_000) & px.notna(),
    }
    print(f"panel: {px.shape[1]} symbols, {px.shape[0]} days")

    rows = []
    for fname, score in fp.factors(px, turn, fund).items():
        for uname, mask in universes.items():
            for rb in (1, 7, 30, 90):
                r = backtest(score, ret, mask, rb)
                if r is None:
                    continue
                rows.append(dict(strategy=fname, universe=uname,
                                 rebalance_d=rb, **fp.stats(r)))
    for lb in (30, 60, 120, 250):
        for uname, mask in universes.items():
            for rb in (7, 30):
                r = tsmom(ret, mask, lb, rb)
                if r is None:
                    continue
                rows.append(dict(strategy=f"tsmom_{lb}d", universe=uname,
                                 rebalance_d=rb, **fp.stats(r)))

    d = pd.DataFrame(rows).sort_values("p_value").reset_index(drop=True)
    m = len(d)
    d["bh_threshold"] = 0.05 * (np.arange(1, m + 1)) / m
    passes = d["p_value"].values <= d["bh_threshold"].values
    cut = np.where(passes)[0].max() if passes.any() else -1
    d["survives_bh"] = [i <= cut for i in range(m)]

    pd.set_option("display.width", 220)
    print(f"\n{m} configurations. Benjamini-Hochberg at FDR 5%.\n")
    print(d.head(20)[["strategy", "universe", "rebalance_d", "days", "gross_ann",
                      "cost_ann", "ann_return", "sharpe", "gross_t", "t_stat",
                      "p_value", "survives_bh"]].to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\nsurviving correction: {int(d['survives_bh'].sum())} of {m}"
          f"   |   raw p<0.05: {int((d['p_value'] < 0.05).sum())}")

    print("\nDOES HOLDING LONGER CUT THE COST? (tradeable universe)")
    t = d[d["universe"] == "tradeable_1m"]
    piv = t.pivot_table(index="strategy", columns="rebalance_d",
                        values=["cost_ann", "ann_return"])
    print(piv.to_string(float_format=lambda x: f"{x:.2f}"))

    out = OUT_DIR / "sweep" / "slow_strategies.csv"
    d.to_csv(out, index=False)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
