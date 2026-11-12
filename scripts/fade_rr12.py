"""Rule A with reward:risk 1:2, outcome counts and money (owner 2026-11-12: "run tests with 1:2 R&R what is win rate,
how much do we make, how many SL, how many 24h stop (+ve and -ve)").

    PYTHONPATH=. python scripts/fade_rr12.py

Trades: Delta's own rule A picks 2025-01-15..2026-10-27 (scripts/fade_ladder_5x.trades: top 2 gainers, skip stops >
32.5%, Delta funding) on the coins' Binance 1m paths, REAL-BOT fills (entry at the 00:03 close, target 0.1% through,
stop at the worse of the stop and that minute's close, 0.1% spread, 0.05% fee a side), out at 24 h.
Stops: 2x ATR14% (today's), 1x ATR, 0.5x ATR; target = 2 x the stop distance (and, for reference, no target = today).
Money: $1,640, live sizing (lever_plan: notional = risk / stop, <= 60% of the balance, 1-3x, liquidation >= 2x away),
ladder 10/5/3 and fixed 3%; the $1,000 floor is marked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_data as FD  # noqa: E402
import fade_ladder_5x as L5  # noqa: E402
from live.fade import PAPER_MM_PCT, lever_plan  # noqa: E402

FEE, EXTRA, THR = 0.0005, 0.001, 0.001
START, FLOOR = 1640.0, 1000.0


def paths(d):
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)
    to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731
    bsym = d.symbol.map(to_b)
    t = d.date.map(lambda x: int(pd.Timestamp(x).timestamp()))
    opens = bd.set_index(["symbol", "time"]).open
    ps = list(FD.POOL.map(lambda x: FD.path_day(x[0], int(x[1])), zip(bsym, t)))
    return [opens.get((b, tt), np.nan) for b, tt in zip(bsym, t)], ps


def simulate(d, opens, ps, stop_mult, rr):
    """Per trade: (R in units of THIS stop, outcome, stop distance). Outcome: STOP / TARGET / 24H+ / 24H-."""
    out = []
    for (dist, r), o, a in zip(zip(d.dist, d.r), opens, ps):
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not (np.isfinite(o) and np.isfinite(cl[2]) and np.isfinite(cl[-1])):
            out.append((np.nan, "NA", np.nan))
            continue
        s0 = np.nonzero(hi >= o * (1 + dist))[0]                      # Delta funding, as fade_sizing_options.realistic
        xp0 = max(o * (1 + dist), cl[s0[0]]) if len(s0) else cl[-1]
        fund_ret = (r - (-(xp0 / o - 1) - FEE - FEE * xp0 / o) / dist) * dist   # funding as a return on notional
        sd = stop_mult * dist / 2                                     # dist = 2x ATR
        e = cl[2]
        stop = e * (1 + sd)
        tgt = e * (1 - rr * sd) if rr else None
        s = np.nonzero(hi[3:] >= stop)[0]
        k = np.nonzero(lo[3:] <= tgt * (1 - THR))[0] if rr else np.array([], int)
        if len(k) and (not len(s) or k[0] < s[0]):
            xp, how, held = tgt, "TARGET", (k[0] + 3) / 1440
        elif len(s):
            xp, how, held = max(stop, cl[3 + s[0]]), "STOP", (s[0] + 3) / 1440
        else:
            xp, held = cl[-1], 1.0
            how = None
        ret = -(xp / e - 1) - FEE - FEE * xp / e - EXTRA + fund_ret * held
        if how is None:
            how = "24H+" if ret > 0 else "24H-"
        out.append((ret / sd, how, sd))
    return out


def account(d, res, rule):
    eq = hw = START
    below, path = None, {}
    for t, idx in d.groupby("date").groups.items():
        under = 1 - eq / hw
        risk = (0.10 if under < 0.20 else 0.05 if under < 0.35 else 0.03) if rule == "ladder" else rule
        pnl = 0.0
        for i in idx:
            r, how, sd = res[i]
            if not np.isfinite(r):
                continue
            pnl += eq * lever_plan(risk, sd, PAPER_MM_PCT)[0] * sd * r
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    s = pd.Series(path)
    return s, below


def main() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = paths(d)
    yrs = d.date.dt.year.to_numpy()
    print(f"Delta rule A picks {d.date.min().date()} .. {d.date.max().date()}, {len(d)} trades, real-bot fills, out at 24 h")
    for sm, rr, label in ((2, None, "TODAY: stop 2x ATR, no target"), (2, 2, "1:2  stop 2x ATR (~20%), target 2x"),
                          (1, 2, "1:2  stop 1x ATR (~10%), target 2x"), (0.5, 2, "1:2  stop 0.5x ATR (~5%), target 2x")):
        res = simulate(d, opens, ps, sm, rr)
        r = np.array([x[0] for x in res])
        how = np.array([x[1] for x in res])
        ok = np.isfinite(r)
        n = ok.sum()
        cnt = {k: int((how[ok] == k).sum()) for k in ("STOP", "TARGET", "24H+", "24H-")}
        w = r[ok] > 0
        print(f"\n{label}   (median stop {100 * np.nanmedian([x[2] for x in res]):.1f}%)")
        print(f"  trades {n}: win {100 * w.mean():.0f}% | stop {cnt['STOP']} ({100 * cnt['STOP'] / n:.0f}%)  target {cnt['TARGET']} "
              f"({100 * cnt['TARGET'] / n:.0f}%)  24h in profit {cnt['24H+']} ({100 * cnt['24H+'] / n:.0f}%)  24h at a loss "
              f"{cnt['24H-']} ({100 * cnt['24H-'] / n:.0f}%)")
        for k in ("STOP", "TARGET", "24H+", "24H-"):
            m = ok & (how == k)
            if m.any():
                print(f"    {k:7s} average {r[m].mean():+.2f}R")
        print(f"  per trade {r[ok].mean():+.3f}R | 2025 {np.nanmean(r[ok & (yrs == 2025)]):+.3f}R  2026 {np.nanmean(r[ok & (yrs == 2026)]):+.3f}R")
        for rule, nm in (("ladder", "ladder 10/5/3"), (0.03, "fixed 3%")):
            s, below = account(d, res, rule)
            m = s.groupby(s.index.to_period("M")).last()
            mret = m / m.shift(1).fillna(START) - 1
            y25 = s[s.index.year == 2025].iloc[-1]
            print(f"  ${START:,.0f} at {nm:13s} -> 2025 end ${y25:>7,.0f}, end ${s.iloc[-1]:>7,.0f} ({100 * (s.iloc[-1] / START - 1):+.0f}%)  "
                  f"worst drop {100 * (1 - s / s.cummax()).max():.0f}%  green months {(mret > 0).sum()}/{len(mret)}  "
                  f"floor {'never' if below is None else below.date()}")


if __name__ == "__main__":
    main()
