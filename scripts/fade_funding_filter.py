"""Skip a pick whose Delta funding was NEGATIVE the day before (owner 2026-11-13: "yes, run another test of real trades as
well"). Backtest on the ladder's exact sizing with real-bot fills, and the filter applied to the actual real and paper
trades.

    PYTHONPATH=. python scripts/fade_funding_filter.py out/fade_real_trades_2026-11-13.json

FILTER: the sum of Delta's funding settlements during the UTC day before entry (out/binance_paths/
delta_funding_daily.parquet for the backtest; Delta's FUNDING history for the recent trades) is < 0 -> skip. Negative
funding means shorts were already paying longs: the crowd was short the coin before we joined. Known at 00:00.
BACKTEST: Delta rule A picks 2025-01-15..2026-10-27 (scripts/fade_ladder_5x.trades), real-bot fills
(scripts/fade_rr12.simulate, stop 2x ATR, 24 h; QUICK = all closed at +0.2R), live sizing (lever_plan) ladder 10/5/3
and fixed 3% / 5%, from $1,640. This is a re-check of a filter FOUND in exploration (backtest_survivors), not a fresh
discovery; the real/paper part is new data for it (2026-10-29..11-13).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import explore_delta_basis as EB  # noqa: E402
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as PL  # noqa: E402
import fade_rr12 as R12  # noqa: E402
ProductCatalog = EB.ProductCatalog

DAY, HOUR = 86400, 3600


def backtest() -> None:
    d = L5.trades().reset_index(drop=True)
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    prev = [F.at[t - pd.Timedelta(days=1), s] if (t - pd.Timedelta(days=1)) in F.index and s in F.columns else np.nan
            for t, s in zip(d.date, d.symbol)]
    d["f_prev"] = prev
    opens, ps = R12.paths(d)
    res_a = R12.simulate(d, opens, ps, 2, None)
    r_q = PL.scale_out(d, opens, ps, [(1.0, 0.2)])
    keep = (d.f_prev >= 0).to_numpy()
    known = np.isfinite(d.f_prev.to_numpy())
    yrs = d.date.dt.year.to_numpy()
    print(f"BACKTEST: {len(d)} Delta picks; funding known for {known.mean():.0%}; the filter keeps {keep.sum()} ({keep.mean():.0%})")
    for nm, r in (("LADDER / rule A", np.array([x[0] for x in res_a])), ("QUICK", r_q)):
        print(f"\n  {nm}")
        for lab, m in (("all picks", np.ones(len(d), bool)), ("skip negative funding", keep), ("only the SKIPPED ones", known & ~keep)):
            ok = m & np.isfinite(r)
            rr = r[ok]
            t = rr.mean() / (rr.std(ddof=1) / np.sqrt(len(rr)))
            print(f"    {lab:24s} n {ok.sum():4d}  {rr.mean():+.3f}R t {t:+.2f}  win {100 * (rr > 0).mean():3.0f}% | "
                  f"2025 {np.nanmean(r[ok & (yrs == 2025)]):+.3f}  2026 {np.nanmean(r[ok & (yrs == 2026)]):+.3f}")
        for lab, m in (("all picks", np.ones(len(d), bool)), ("skip negative funding", keep)):
            sub = d[m].reset_index(drop=True)
            res = [(x, "", s) for x, s in zip(r[m], sub.dist)]
            for rule, rn in (("ladder", "ladder 10/5/3"), (0.03, "fixed 3%"), (0.05, "fixed 5%")):
                s, below = R12.account(sub, res, rule)
                mth = s.groupby(s.index.to_period("M")).last()
                mret = mth / mth.shift(1).fillna(R12.START) - 1
                ye = s.groupby(s.index.year).last()
                print(f"    {lab:24s} {rn:13s} -> ${s.iloc[-1]:>7,.0f}  2025 end ${ye.get(2025):>7,.0f}  worst drop "
                      f"{100 * (1 - s / s.cummax()).max():3.0f}%  green {(mret > 0).sum()}/{len(mret)}  "
                      f"under $1,000 {'never' if below is None else below.date()}")


def recent(path: str) -> None:
    rows = json.load(open(path))
    cat = ProductCatalog()
    cache = {}
    out = []
    for x in rows:
        day = int(pd.Timestamp(x["day"]).timestamp())
        sym = x["symbol"]
        if (sym, day) not in cache:
            try:
                iv = int(cat.get(sym)["funding_interval_seconds"])
            except Exception:                                            # noqa: BLE001
                iv = 8 * HOUR
            s = EB.funding_daily(sym, iv, day - 2 * DAY, day)
            cache[(sym, day)] = float(s.get(day - DAY, np.nan)) if len(s) else np.nan
        out.append(dict(x, f_prev=cache[(sym, day)]))
    df = pd.DataFrame(out)
    print("\nREAL AND PAPER TRADES 2026-10-29..11-13 (closed), funding the day before entry")
    for src, g in df.groupby("src"):
        known = g.f_prev.notna()
        skip = known & (g.f_prev < 0)
        print(f"\n  {src}: {len(g)} trades, funding known {known.sum()}; the filter would SKIP {skip.sum()}")
        print(f"    kept    : n {(~skip).sum():2d}  mean {g.r[~skip].mean():+.3f}R  total {g.r[~skip].sum():+.2f}R  ${g.pnl[~skip].sum():+.2f}")
        if skip.any():
            print(f"    skipped : n {skip.sum():2d}  mean {g.r[skip].mean():+.3f}R  total {g.r[skip].sum():+.2f}R  ${g.pnl[skip].sum():+.2f}")
        for y in g.itertuples():
            tag = "SKIP" if (pd.notna(y.f_prev) and y.f_prev < 0) else "keep" if pd.notna(y.f_prev) else "n/a "
            print(f"      {y.day[:10]} {y.symbol:12s} {str(y.exit):7s} {y.r:+.2f}R ${y.pnl:+8.2f}   funding day before "
                  f"{100 * y.f_prev:+.3f}%  {tag}" if pd.notna(y.f_prev) else
                  f"      {y.day[:10]} {y.symbol:12s} {str(y.exit):7s} {y.r:+.2f}R ${y.pnl:+8.2f}   funding n/a")


if __name__ == "__main__":
    backtest()
    if len(sys.argv) > 1:
        recent(sys.argv[1])
