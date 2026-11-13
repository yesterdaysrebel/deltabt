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


def monthly() -> None:
    """Owner 2026-11-13: "with 10% risk, max leverage, filter on, 0.5R target simulate bot in year 2025 and 2026 and
    give me numbers each month". Funding >= 0.05% filter, close all at +0.5R, else 2x-ATR stop or 24 h, risk 10% of the
    balance at the stop, leverage = the most that keeps liquidation >= 1.2x the stop away (margin 25% a trade).
    Exit class from R: target (R > +0.4), stop (R < -0.9), else 24 h."""
    d = L5.trades().reset_index(drop=True)
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    d["f_prev"] = [F.at[t - pd.Timedelta(days=1), s] if (t - pd.Timedelta(days=1)) in F.index and s in F.columns else np.nan
                   for t, s in zip(d.date, d.symbol)]
    opens, ps = R12.paths(d)
    r = np.where((d.f_prev >= 0.0005).to_numpy(), PL.scale_out(d, opens, ps, [(1.0, 0.5)]), np.nan)
    eq, rows = START, []
    for t, idx in d.groupby("date").groups.items():
        pnl = 0.0
        for i in idx:
            if not np.isfinite(r[i]):
                continue
            sd = d.dist.iat[i]
            lev = 1 / (1.2 * sd + 0.005)
            notional = min(0.10 * eq / sd, lev * 0.25 * eq)
            x = notional * sd * r[i]
            pnl += x
            rows.append(dict(month=t.strftime("%Y-%m"), pnl=x, r=r[i], lev=notional / (0.25 * eq), notional=notional,
                             start=eq))
        eq = max(eq + pnl, 0.0)
        rows and rows[-1].update(end=eq)
    df = pd.DataFrame(rows)
    df["end"] = df.end.ffill()
    print("BOT SIMULATION: funding >= 0.05% filter, +0.5R target, 10% risk, max safe leverage (liq >= 1.2x stop)")
    print(f"Delta, real-bot fills, from ${START:,.0f}. Leverage = notional / margin (25% of balance per trade).\n")
    print(f"{'month':8s} {'start':>8s} {'trades':>6s} {'won':>4s} {'lost':>4s} {'target':>6s} {'stop':>5s} {'24h':>4s} "
          f"{'avg lev':>7s} {'P&L':>9s} {'end':>8s} {'month':>7s}")
    bal = START
    allm = pd.period_range("2025-01", "2026-10", freq="M").strftime("%Y-%m")
    for m in allm:
        g = df[df.month == m]
        if g.empty:
            print(f"{m:8s} {bal:8,.0f} {0:6d} {'':>4s} {'':>4s} {'':>6s} {'':>5s} {'':>4s} {'':>7s} {0:+9.2f} {bal:8,.0f} {0:+6.1f}%")
            continue
        p = g.pnl.sum()
        print(f"{m:8s} {bal:8,.0f} {len(g):6d} {(g.pnl > 0).sum():4d} {(g.pnl <= 0).sum():4d} {(g.r > 0.4).sum():6d} "
              f"{(g.r < -0.9).sum():5d} {((g.r >= -0.9) & (g.r <= 0.4)).sum():4d} {g.lev.mean():6.1f}x {p:+9.2f} "
              f"{bal + p:8,.0f} {100 * p / bal:+6.1f}%")
        bal += p
    for y in ("2025", "2026"):
        g = df[df.month.str.startswith(y)]
        print(f"\n{y}: {len(g)} trades, won {(g.pnl > 0).sum()} ({100 * (g.pnl > 0).mean():.0f}%), P&L ${g.pnl.sum():+,.2f}, "
              f"best trade ${g.pnl.max():+,.0f}, worst ${g.pnl.min():+,.0f}")
    print(f"\nTOTAL: {len(df)} trades, ${START:,.0f} -> ${bal:,.0f}")


def max_leverage() -> None:
    """Owner 2026-11-13: "if I check on delta with max leverage and 50% captial per trade I can buy much more and make
    much more, test it". Funding filter on, target +0.5R (0.5 x the 2x-ATR distance below entry), 24 h, real-bot fills.
    Each trade: ISOLATED margin = 50% of the balance (two picks on a day = 100%), notional = leverage x margin.
    Leverage = Delta's max for the coin (1 / initial margin, table 2026-11-12; 20x if unknown) or a fixed cap.
    Liquidation when the high reaches entry x (1 + 1/lev - maintenance): the whole margin is lost.
    Variants: no stop (liquidation is the stop) / a stop at 80% of the way to liquidation / the normal 2x-ATR stop
    (only possible when liquidation is beyond it)."""
    import json
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))
    d = L5.trades().reset_index(drop=True)
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    d["f_prev"] = [F.at[t - pd.Timedelta(days=1), s] if (t - pd.Timedelta(days=1)) in F.index and s in F.columns else np.nan
                   for t, s in zip(d.date, d.symbol)]
    keep = (d.f_prev >= 0.0005).to_numpy()
    opens, ps = R12.paths(d)
    im = np.array([tab[s][0] / 100 if s in tab else 0.05 for s in d.symbol])
    mm = np.array([tab[s][1] / 100 if s in tab else 0.025 for s in d.symbol])
    FEE_, EX_, THR_ = PL.FEE, PL.EXTRA, PL.THR

    def one(i, lev, mode):
        """Return (return on margin, how). how: T target, S stop, L liquidated, D 24 h."""
        a = ps[i]
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not (np.isfinite(cl[2]) and np.isfinite(cl[-1])):
            return np.nan, "-"
        e, dist = cl[2], d.dist.iat[i]
        liq_d = 1 / lev - mm[i]
        tp = e * (1 - 0.5 * dist)
        stop_d = {"none": None, "80% to liq": 0.8 * liq_d, "2x ATR": dist}[mode]
        if stop_d is not None and stop_d >= liq_d:
            return np.nan, "x"                                         # stop beyond liquidation: not placeable
        def first(m):
            k = np.nonzero(m[3:])[0]
            return 3 + k[0] if len(k) else 10 ** 9
        ml = first(hi >= e * (1 + liq_d))
        ms = first(hi >= e * (1 + stop_d)) if stop_d is not None else 10 ** 9
        mt = first(lo <= tp * (1 - THR_))
        m0 = min(ml, ms, mt)
        if m0 == 10 ** 9:
            xp, how = cl[-1], "D"
        elif ms == m0 or ml == m0:
            if ms <= ml:
                xp, how = max(e * (1 + stop_d), cl[ms]), "S"
                if xp >= e * (1 + liq_d):
                    return -1.0 - FEE_ * lev, "L"
            else:
                return -1.0 - FEE_ * lev, "L"
        else:
            xp, how = tp, "T"
        ret = -(xp / e - 1) - FEE_ - FEE_ * xp / e - EX_
        return max(ret * lev, -1.0), how

    print("MAX LEVERAGE, 50% OF THE BALANCE AS MARGIN PER TRADE. Delta picks with the funding filter (324), +0.5R target,"
          f" real-bot fills, from ${START:,.0f}. Median Delta max leverage on these picks {np.median(1 / im[keep]):.0f}x\n")
    print(f"  {'leverage':10s} {'stop':11s} {'trades':>6s} {'target':>6s} {'stop':>5s} {'LIQ':>4s} {'24h':>4s} {'win':>4s}  "
          f"{'avg $/trade':>11s}  {'2025 end':>9s} {'end':>9s}  {'worst drop':>10s}  first time under $500")
    for levs, lname in (("max", "Delta max"), (20, "20x"), (10, "10x"), (5, "5x"), (3, "3x")):
        for mode in ("none", "80% to liq", "2x ATR"):
            eq, hw, dd, pn, hows, under, rms = START, START, 0.0, [], [], None, []
            for t, idx in d.groupby("date").groups.items():
                pnl = 0.0
                for i in idx:
                    if not keep[i]:
                        continue
                    lev = 1 / im[i] if levs == "max" else min(float(levs), 1 / im[i])
                    rm, how = one(i, lev, mode)
                    if not np.isfinite(rm):
                        continue
                    x = 0.5 * eq * rm
                    pnl += x
                    pn.append(x)
                    hows.append(how)
                    rms.append(rm)
                eq = max(eq + pnl, 0.0)
                hw = max(hw, eq)
                dd = max(dd, 1 - eq / hw if hw > 0 else 1)
                if under is None and eq < 500:
                    under = t
                if t.year == 2025:
                    e25 = eq
            h = np.array(hows)
            pn = np.array(pn)
            if not len(pn):
                print(f"  {lname:10s} {mode:11s} no placeable trades (stop beyond liquidation)")
                continue
            print(f"  {lname:10s} {mode:11s} {len(pn):6d} {(h == 'T').sum():6d} {(h == 'S').sum():5d} {(h == 'L').sum():4d} "
                  f"{(h == 'D').sum():4d} {100 * (np.array(rms) > 0).mean():3.0f}%  {pn.mean():+11.2f}  ${e25:>8,.0f} ${eq:>8,.0f}  "
                  f"{100 * dd:9.0f}%  {'never' if under is None else under.date()}")
    print("\nREFERENCE (the package): 10% risk at the 2x-ATR stop, same picks and target -> $5,766, worst drop 46%")


def real_check() -> None:
    """Owner 2026-11-13: "check previous real trades, they would have won in this test" -- 50% margin at 10x vs the
    package, replayed on the 30 picks the bots actually took 2026-10-29..11-12 (out/fade_quick_full/quick_paper.json:
    the bots' own entry and stop prices; the real account traded the same rule A picks). Delta 1m trade-price candles
    for target/stop, 1m MARK candles for liquidation (cached by scripts/fade_quick_full_replay). Target = +0.5R
    (entry - 0.5 x (stop - entry)). Funding filter from Delta's FUNDING history. From $1,640, compounding."""
    import json
    import explore_delta_basis as EB
    import fade_quick_full_replay as QR
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))
    picks = [x for x in json.load(open("out/fade_quick_full/quick_paper.json")) if x.get("entry_price")]
    cat = EB.ProductCatalog()
    rows = []
    for pk in picks:
        t0 = QR.ts(pk["day"])
        ltp, mark = QR.candles(pk["symbol"], t0), QR.candles("MARK:" + pk["symbol"], t0)
        if not ltp:
            continue
        try:
            iv = int(cat.get(pk["symbol"])["funding_interval_seconds"])
        except Exception:                                                 # noqa: BLE001
            iv = 8 * 3600
        fs = EB.funding_daily(pk["symbol"], iv, t0 - 2 * 86400, t0)
        fprev = float(fs.get(t0 - 86400, np.nan)) if len(fs) else np.nan
        e, st = pk["entry_price"], pk["stop_price"]
        dist = st / e - 1
        mm = tab.get(pk["symbol"], [5, 2.5])[1] / 100
        mk = {c["time"]: c for c in mark}

        def play(stop, liq):
            tgt = e - 0.5 * (st - e)
            for c in ltp:
                m = mk.get(c["time"])
                if liq and m and m["high"] >= liq:
                    return "LIQUIDATED", liq
                if stop and c["high"] >= stop:
                    return "STOP", max(stop, c["close"])
                if c["low"] <= tgt * (1 - PL.THR):
                    return "TARGET", tgt
            return "24h", ltp[-1]["close"]
        ret = lambda xp: -(xp / e - 1) - PL.FEE - PL.FEE * xp / e - PL.EXTRA           # noqa: E731
        h10, x10 = play(None, e * (1 + 0.1 - mm))
        hp, xp_ = play(st, None)
        rows.append(dict(day=pk["day"][:10], sym=pk["symbol"], dist=dist, fprev=fprev, how10=h10,
                         r10=-1.0 if h10 == "LIQUIDATED" else max(10 * ret(x10), -1.0), howp=hp, rp=ret(xp_)))
    df = pd.DataFrame(rows).sort_values("day")
    print(f"REAL PICKS 2026-10-29..11-12 ({len(df)} trades the bots took), Delta 1m + MARK candles, from ${START:,.0f}\n")
    print(f"  {'day':10s} {'coin':12s} {'stop':>5s} {'funding':>8s} | {'10x, 50% margin':>22s} | {'package (10% risk)':>22s}")
    for filt in (False, True):
        g = df[df.fprev >= 0.0005] if filt else df
        eq10 = eqp = START
        print(f"\n  {'FUNDING FILTER ON' if filt else 'ALL PICKS'} ({len(g)} trades)")
        for day, dd in g.groupby("day"):
            p10 = pp = 0.0
            for y in dd.itertuples():
                a = 0.5 * eq10 * y.r10
                notional = min(0.10 * eqp / y.dist, 1 / (1.2 * y.dist + 0.005) * 0.25 * eqp)
                b = notional * y.rp
                p10 += a
                pp += b
                print(f"  {y.day:10s} {y.sym:12s} {100 * y.dist:4.0f}% {100 * y.fprev:+7.3f}% | {y.how10:10s} {a:+10.2f} | "
                      f"{y.howp:10s} {b:+10.2f}")
            eq10, eqp = max(eq10 + p10, 0), max(eqp + pp, 0)
        print(f"  {'END':10s} {'':12s} {'':5s} {'':8s} | {'':10s} ${eq10:>9,.2f} | {'':10s} ${eqp:>9,.2f}")


if __name__ == "__main__":
    if "--realcheck" in sys.argv:
        real_check()
    elif "--maxlev" in sys.argv:
        max_leverage()
    elif "--monthly" in sys.argv:
        monthly()
    elif "--target1r" in sys.argv:
        target_1r()
    else:
        main()
