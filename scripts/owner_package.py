"""The owner's style made as close to workable as the data allows (2026-11-13: "you have pushed back alot, without
finding solution to my needs"). Delta rule A picks 2025-01..2026-10, real-bot fills (scripts/fade_rr12.paths /
fade_profit_ladder.scale_out), from $1,640.

  CUT LOSERS     before entry: short only when Delta's previous-day funding sum >= 0.05% (crowded longs paying);
                 the picks without it are where most of the stop-outs come from.
  QUICK PROFIT   close 2/3 (or all) at +0.2R, the rest runs to the 2x-ATR stop or 24 h.
  MORE SIZE      fixed risk 5-15% of the balance; margin 25% of the balance per trade; leverage as high as keeps
                 liquidation >= 1.2x the stop distance away (lev <= 1 / (1.2 x stop + 0.5%)), so a stop always fires
                 before liquidation. Notional = min(risk / stop, lev x margin).

    PYTHONPATH=. python scripts/owner_package.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as PL  # noqa: E402
import fade_rr12 as R12  # noqa: E402

START = 1640.0


def account(d, r, risk, margin=0.25):
    eq, hw, path, pnls = START, START, {}, []
    dd = 0.0
    for t, idx in d.groupby("date").groups.items():
        pnl = 0.0
        for i in idx:
            if not np.isfinite(r[i]):
                continue
            sd = d.dist.iat[i]
            lev = 1 / (1.2 * sd + 0.005)
            notional = min(risk * eq / sd, lev * margin * eq)
            x = notional * sd * r[i]
            pnl += x
            pnls.append(x)
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        dd = max(dd, 1 - eq / hw)
        path[t] = eq
    s = pd.Series(path)
    return s, dd, np.array(pnls)


def main() -> None:
    d = L5.trades().reset_index(drop=True)
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    d["f_prev"] = [F.at[t - pd.Timedelta(days=1), s] if (t - pd.Timedelta(days=1)) in F.index and s in F.columns else np.nan
                   for t, s in zip(d.date, d.symbol)]
    opens, ps = R12.paths(d)
    plans = {"hold 24 h (rule A)": np.array([x[0] for x in R12.simulate(d, opens, ps, 2, None)]),
             "2/3 at +0.2R, rest runs": PL.scale_out(d, opens, ps, [(2 / 3, 0.2)]),
             "all at +0.2R (QUICK)": PL.scale_out(d, opens, ps, [(1.0, 0.2)])}
    yrs = d.date.dt.year.to_numpy()
    print(f"DELTA rule A picks {d.date.min().date()}..{d.date.max().date()}, real-bot fills, from ${START:,.0f}")
    for fname, m in (("ALL PICKS (today)", np.ones(len(d), bool)), ("FUNDING >= 0.05% ONLY", (d.f_prev >= 0.0005).to_numpy())):
        print(f"\n{fname}: {m.sum()} trades ({m.sum() / 21.5:.0f}/month)")
        for pn, r in plans.items():
            rr = np.where(m, r, np.nan)
            ok = np.isfinite(rr)
            print(f"  {pn:26s} {np.nanmean(rr):+.3f}R  win {100 * (rr[ok] > 0).mean():3.0f}% | 2025 {np.nanmean(rr[yrs == 2025]):+.3f}  2026 {np.nanmean(rr[yrs == 2026]):+.3f}")
            for risk in (0.05, 0.08, 0.10, 0.15):
                s, dd, p = account(d, rr, risk)
                ye = s.groupby(s.index.year).last()
                print(f"      risk {100 * risk:3.0f}%: avg ${p.mean():+7.2f}/trade (win ${p[p > 0].mean():6.0f} / loss ${p[p < 0].mean():6.0f})"
                      f"  2025 end ${ye.get(2025, np.nan):>7,.0f}  end ${s.iloc[-1]:>7,.0f}  worst drop {100 * dd:3.0f}%")


def target_1r() -> None:
    """Owner 2026-11-13: "I prefer if we reach more than 1R we cut the trade". Close at +1R (a fall of 1x the stop
    distance, ~19%), else 2x-ATR stop or 24 h. Neighbours shown for context, plus 2/3 at +0.2R with the rest at +1R."""
    d = L5.trades().reset_index(drop=True)
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    d["f_prev"] = [F.at[t - pd.Timedelta(days=1), s] if (t - pd.Timedelta(days=1)) in F.index and s in F.columns else np.nan
                   for t, s in zip(d.date, d.symbol)]
    opens, ps = R12.paths(d)
    plans = {"hold 24 h (no target)": np.array([x[0] for x in R12.simulate(d, opens, ps, 2, None)]),
             "close all at +0.5R": PL.scale_out(d, opens, ps, [(1.0, 0.5)]),
             "close all at +1R  <- owner": PL.scale_out(d, opens, ps, [(1.0, 1.0)]),
             "close all at +1.5R": PL.scale_out(d, opens, ps, [(1.0, 1.5)]),
             "2/3 at +0.2R, last 1/3 at +1R": PL.scale_out(d, opens, ps, [(2 / 3, 0.2), (1 / 3, 1.0)])}
    r1 = plans["close all at +1R  <- owner"]
    yrs = d.date.dt.year.to_numpy()
    print(f"TARGET +1R. DELTA rule A picks {d.date.min().date()}..{d.date.max().date()}, real-bot fills, from ${START:,.0f}")
    for fname, m in (("ALL PICKS", np.ones(len(d), bool)), ("FUNDING >= 0.05% ONLY", (d.f_prev >= 0.0005).to_numpy())):
        print(f"\n{fname}: {m.sum()} trades")
        for pn, r in plans.items():
            rr = np.where(m, r, np.nan)
            ok = np.isfinite(rr)
            print(f"  {pn:32s} {np.nanmean(rr):+.3f}R  win {100 * (rr[ok] > 0).mean():3.0f}% | 2025 {np.nanmean(rr[yrs == 2025]):+.3f}  2026 {np.nanmean(rr[yrs == 2026]):+.3f}")
            for risk in (0.05, 0.10):
                s, dd, p = account(d, rr, risk)
                ye = s.groupby(s.index.year).last()
                print(f"      risk {100 * risk:3.0f}%: avg ${p.mean():+7.2f}/trade (win ${p[p > 0].mean():6.0f} / loss ${p[p < 0].mean():6.0f})"
                      f"  2025 end ${ye.get(2025, np.nan):>7,.0f}  end ${s.iloc[-1]:>7,.0f}  worst drop {100 * dd:3.0f}%")
        # how often +1R is reached, and how the trades end
        e = cl_end = None
        hit = np.isclose(r1, r1)  # placeholder to keep shapes
        print(f"    +1R target: trades reaching it {100 * np.nanmean((r1 > 0.9)[m]):.0f}%  |  ending at the stop "
              f"{100 * np.nanmean((r1 < -0.9)[m]):.0f}%  |  ending at 24 h {100 * np.nanmean(((r1 >= -0.9) & (r1 <= 0.9))[m]):.0f}%")


if __name__ == "__main__":
    if "--target1r" in sys.argv:
        target_1r()
    else:
        main()
