"""Holding the PAID side across a Delta funding settlement (owner 2026-11-11: "focus on futures").

    PYTHONPATH=. python scripts/explore_settle.py      (needs scripts/explore_settle_data.py's cache)

Event: a settlement whose rate ONE HOUR BEFORE was >= 0.25% in size (known in time). Rate > 0: longs pay shorts ->
SHORT; rate < 0 -> LONG. Enter `k` minutes before the stamp, exit `j` minutes after, at the MARK price's 1m close.
Net = price move on our side + funding actually settled - cost (Delta taker 0.079% a side + the coin's live spread
from out/delta_spreads_2026-11-11.json, 0.10% if unknown). t-statistics are on DAILY means (events cluster).
Open exploration, not a pre-registered result.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

PRE = 65
FEE_RT = 0.00158


def main() -> None:
    e = pd.read_parquet("out/binance_paths/settle_events.parquet")
    a = np.load("out/binance_paths/settle_paths.npz")["paths"].astype(float)
    cl = pd.DataFrame(a[:, 2]).ffill(axis=1).to_numpy()
    spread = {r["symbol"]: r["spread"] for r in json.load(open("out/delta_spreads_2026-11-11.json"))["rows"]}
    e["spread"] = e.symbol.map(spread).fillna(0.0010)
    e["side"] = -np.sign(e.rate_before)
    e["fund"] = -e.side * e.rate_at / 100.0                           # what our side receives at the stamp
    e["day"] = pd.to_datetime(e.stamp // 86400 * 86400, unit="s")
    e["year"] = e.day.dt.year
    px = lambda off: cl[:, PRE + off - 1]                             # noqa: E731  close of the minute ending at stamp+off
    ok = np.isfinite(px(-60)) & np.isfinite(px(120))
    print(f"{len(e)} events, {int(ok.sum())} with a full price path; {e.symbol.nunique()} coins; "
          f"{100 * (e.side > 0).mean():.0f}% are LONGS (negative funding); spread median {100 * e.spread.median():.3f}%")
    print(f"funding received by our side: mean {100 * e.fund.mean():+.3f}%, median {100 * e.fund.median():+.3f}%, "
          f"wrong-sign at the stamp {100 * (e.fund < 0).mean():.1f}%")

    def stat(mask, k, j, stress=1.0):
        m = mask & ok
        price = e.side.to_numpy() * (px(j) / px(-k) - 1)
        net = price + e.fund.to_numpy() - FEE_RT - stress * e.spread.to_numpy()
        d = pd.DataFrame({"day": e.day[m], "net": net[m], "price": price[m], "fund": e.fund.to_numpy()[m], "year": e.year[m]})
        g = d.groupby("day").net.mean()
        t = g.mean() / (g.std(ddof=1) / np.sqrt(len(g))) if len(g) > 2 else np.nan
        yr = " ".join(f"{y} {100 * d[d.year == y].net.mean():+.3f}" for y in sorted(d.year.unique()))
        return (f"n {len(d):5d} days {len(g):3d}  price {100 * d.price.mean():+.3f}%  funding {100 * d.fund.mean():+.3f}%  "
                f"NET {100 * d.net.mean():+.3f}%  t(days) {t:+5.2f}  win {100 * (d.net > 0).mean():3.0f}%  worst {100 * d.net.min():+6.1f}% | {yr}")

    every = np.ones(len(e), bool)
    r = e.rate_before.abs().to_numpy()
    print("\nA. WHEN TO ENTER AND EXIT (all events >= 0.25%)")
    for k in (1, 5, 15, 60):
        for j in (1, 5, 15, 60, 120):
            print(f"  in {k:2d} min before, out {j:3d} min after: {stat(every, k, j)}")
    print("\nB. BY SIZE OF THE RATE (in 5 before, out 5 after)")
    for lo, hi in ((0.25, 0.35), (0.35, 0.50), (0.50, 0.75), (0.75, 1.0), (1.0, 9)):
        print(f"  {lo:.2f}-{hi:.2f}%: {stat((r >= lo) & (r < hi), 5, 5)}")
    print("\nC. BY SIDE (in 5 before, out 5 after)")
    print(f"  LONG (negative funding):  {stat((e.side > 0).to_numpy(), 5, 5)}")
    print(f"  SHORT (positive funding): {stat((e.side < 0).to_numpy(), 5, 5)}")
    print("\nD. STRESS (in 5 before, out 5 after, >= 0.50%)")
    big = r >= 0.50
    for s in (1.0, 3.0, 5.0):
        print(f"  spreads x{s:.0f}: {stat(big, 5, 5, stress=s)}")
    print("\nE. WHAT PRICE DOES AROUND THE STAMP, on our side, % (no funding, no cost), events >= 0.50%")
    for a_, b_ in ((-60, -15), (-15, -5), (-5, -1), (-1, 1), (1, 5), (5, 15), (15, 60), (60, 120)):
        v = (e.side.to_numpy() * (px(b_) / px(a_) - 1))[big & ok]
        print(f"  minute {a_:+4d} to {b_:+4d}: mean {100 * v.mean():+.3f}%  median {100 * np.median(v):+.3f}%")


if __name__ == "__main__":
    main()
