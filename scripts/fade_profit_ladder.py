"""A "ladder" on the profit side for rule A (owner 2026-11-12: "why not add ladder to profits as well?"), both readings:
  1. SCALE OUT: close part of the short at small gains, the rest runs to the stop or 24 h.
  2. RISK UP WHEN AHEAD: raise risk per trade as the balance grows (today's ladder only cuts it after losses).
  3. TRAILING STOP (owner: "or trailing SL?"): from +act R, the stop follows the best price at trail R behind it.

    PYTHONPATH=. python scripts/fade_profit_ladder.py

Trades as scripts/fade_rr12.py: Delta's rule A picks 2025-01-15..2026-10-27 (1,022), Binance 1m paths, real-bot fills
(00:03 entry, 0.1% trade-through for limits, stop at the worse of the stop and the minute's close, 0.1% spread, 0.05%
fee a side), stop 2x ATR14%, 24 h, Delta funding while open. R in units of the stop.
Scale-out plans (each slice is closed when the price trades 0.1% through its level; the rest keeps the stop):
  1/2 at 0.2R ; 1/2 at 0.5R ; 1/3 at 0.2R + 1/3 at 0.5R ; 1/3 at 0.25R + 1/3 at 0.5R + 1/3 at 1R ;
  1/3 at 0.2R + 1/3 at 0.5R with the stop moved to entry after the first slice
Risk-up plans ($1,640 start; "ahead" = balance vs the start):
  ladder 10/5/3 (today) ; ladder + 12.5% at +25% and 15% at +50% ; 3% -> 5% at +25% -> 7.5% at +50% ;
  5% -> 7.5% at +25% -> 10% at +50% (each still cut to 5% / 3% when 20% / 35% under the high-water mark)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_rr12 as R12  # noqa: E402
from live.fade import PAPER_MM_PCT, lever_plan  # noqa: E402

FEE, EXTRA, THR = R12.FEE, R12.EXTRA, R12.THR
START, FLOOR = 1640.0, 1000.0


def scale_out(d, opens, ps, plan, be_after_first=False):
    """plan: list of (fraction, level in R); the remainder runs. Returns R per trade (whole position)."""
    out = []
    for (dist, r), o, a in zip(zip(d.dist, d.r), opens, ps):
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not (np.isfinite(o) and np.isfinite(cl[2]) and np.isfinite(cl[-1])):
            out.append(np.nan)
            continue
        s0 = np.nonzero(hi >= o * (1 + dist))[0]
        xp0 = max(o * (1 + dist), cl[s0[0]]) if len(s0) else cl[-1]
        fund_r = r - (-(xp0 / o - 1) - FEE - FEE * xp0 / o) / dist          # funding over a full day, in R
        e = cl[2]
        stop = e * (1 + dist)
        left, total, minute = 1.0, 0.0, 3
        levels = sorted(plan, key=lambda x: x[1])
        done = False
        for frac, lvl in levels:
            tp = e * (1 - lvl * dist)
            s = np.nonzero(hi[minute:] >= stop)[0]
            k = np.nonzero(lo[minute:] <= tp * (1 - THR))[0]
            if len(k) and (not len(s) or k[0] < s[0]):
                m = minute + k[0]
                ret = -(tp / e - 1) - FEE - FEE * tp / e - EXTRA
                total += frac * (ret / dist + fund_r * m / 1440)
                left -= frac
                minute = m + 1
                if be_after_first:
                    stop = e * (1 + FEE * 2 + EXTRA)                       # stop at entry, after costs
                    be_after_first = False
            else:
                break
        s = np.nonzero(hi[minute:] >= stop)[0]
        if len(s):
            m = minute + s[0]
            xp = max(stop, cl[m])
        else:
            m, xp = 1439, cl[-1]
        ret = -(xp / e - 1) - FEE - FEE * xp / e - EXTRA
        total += left * (ret / dist + fund_r * (m + 1) / 1440)
        out.append(total)
    return np.array(out)


def trailing(d, opens, ps, act, trail):
    """Owner 2026-11-12 "or trailing SL?": once the short is `act` R in profit, the stop follows the lowest price so far at
    `trail` R above it (never looser than the original stop); checked minute by minute (stop from the previous minute)."""
    out = []
    for (dist, r), o, a in zip(zip(d.dist, d.r), opens, ps):
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not (np.isfinite(o) and np.isfinite(cl[2]) and np.isfinite(cl[-1])):
            out.append(np.nan)
            continue
        s0 = np.nonzero(hi >= o * (1 + dist))[0]
        xp0 = max(o * (1 + dist), cl[s0[0]]) if len(s0) else cl[-1]
        fund_r = r - (-(xp0 / o - 1) - FEE - FEE * xp0 / o) / dist
        e = cl[2]
        stop, best, m_exit, xp = e * (1 + dist), e, 1439, cl[-1]
        for m in range(3, 1440):
            if hi[m] >= stop:
                m_exit, xp = m, max(stop, cl[m])
                break
            best = min(best, lo[m])
            if (e - best) / e >= act * dist:
                stop = min(stop, best * (1 + trail * dist))
        ret = -(xp / e - 1) - FEE - FEE * xp / e - EXTRA
        out.append(ret / dist + fund_r * (m_exit + 1) / 1440)
    return np.array(out)


def leveraged(d, opens, ps, lev, margin_total=0.50, start=START, take=0.5):
    """Owner 2026-11-12: "full leverage and 50% capital deployed with close half at +0.2R". Each day's picks share
    `margin_total` of the balance as margin; notional = L x margin (L = `lev` capped at the coin's Delta max, or the
    max for "full"); half closed at +0.2R (0.2 x the 2x-ATR distance), the rest to the 2x-ATR stop or 24 h. Liquidation
    when a 1m high reaches entry x (1 + 1/L - maintenance): the open part's margin is lost. Compounds daily."""
    import json
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))
    eq, peak, worst, path, below = start, start, 0.0, {}, None
    n_liq = n_half = n_stop = n = 0
    for t, idx in d.groupby("date").groups.items():
        idx = [i for i in idx if np.isfinite(opens[i])]
        if not idx:
            continue
        margin = margin_total * eq / len(idx)
        pnl = 0.0
        for i in idx:
            a = ps[i]
            dist = d.dist.iat[i]
            im, mm = (x / 100 for x in tab.get(d.symbol.iat[i], [5.0, 2.5]))
            L = 1 / im if lev == "full" else min(float(lev), 1 / im)
            hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
            lo = np.where(np.isfinite(a[1]), a[1], np.inf)
            cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
            if not (np.isfinite(cl[2]) and np.isfinite(cl[-1])):
                continue
            n += 1
            e = cl[2]
            stop, tp, liq = e * (1 + dist), e * (1 - 0.2 * dist), e * (1 + 1 / L - mm)
            left, got = 1.0, 0.0
            m0 = 3
            for m in range(3, 1440):
                if hi[m] >= min(liq, stop):
                    if liq <= stop and hi[m] >= liq:
                        n_liq += 1
                        got -= left * margin
                    else:
                        n_stop += 1
                        xp = max(stop, cl[m])
                        got += left * L * margin * (-(xp / e - 1) - FEE - FEE * xp / e - EXTRA)
                    left = 0.0
                    break
                if take > 0 and left == 1.0 and lo[m] <= tp * (1 - THR):
                    n_half += 1
                    got += take * L * margin * (-(tp / e - 1) - FEE - FEE * tp / e - EXTRA)
                    left = 1.0 - take
                    if left <= 0:
                        break
            if left > 0:
                xp = cl[-1]
                got += left * L * margin * (-(xp / e - 1) - FEE - FEE * xp / e - EXTRA)
            pnl += max(got, -margin)
        eq = max(eq + pnl, 0.0)
        peak = max(peak, eq)
        worst = max(worst, 1 - eq / peak if peak > 0 else 1)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), worst, below, n, n_liq, n_half, n_stop


def account(d, r, rule):
    eq = hw = START
    below, path = None, {}
    for t, idx in d.groupby("date").groups.items():
        under, ahead = 1 - eq / hw, eq / START - 1
        cut = 0.05 if under >= 0.20 else None
        cut = 0.03 if under >= 0.35 else cut
        if rule == "ladder":
            risk = cut or 0.10
        elif rule == "ladder+up":
            risk = cut or (0.15 if ahead >= 0.5 else 0.125 if ahead >= 0.25 else 0.10)
        elif rule == "3->7.5":
            risk = min(cut or 1, 0.075 if ahead >= 0.5 else 0.05 if ahead >= 0.25 else 0.03)
        elif rule == "5->10":
            risk = min(cut or 1, 0.10 if ahead >= 0.5 else 0.075 if ahead >= 0.25 else 0.05)
        else:
            risk = rule
        pnl = sum(eq * lever_plan(risk, d.dist.iat[i], PAPER_MM_PCT)[0] * d.dist.iat[i] * r[i] for i in idx if np.isfinite(r[i]))
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), below


def show(nm, d, r, rules):
    ok = np.isfinite(r)
    yrs = d.date.dt.year.to_numpy()
    print(f"\n{nm}: {r[ok].mean():+.3f}R per trade, win {100 * (r[ok] > 0).mean():.0f}% | 2025 {np.nanmean(r[ok & (yrs == 2025)]):+.3f}  "
          f"2026 {np.nanmean(r[ok & (yrs == 2026)]):+.3f}")
    for rule, lab in rules:
        s, below = account(d, r, rule)
        m = s.groupby(s.index.to_period("M")).last()
        mret = m / m.shift(1).fillna(START) - 1
        print(f"  {lab:34s} -> ${s.iloc[-1]:>7,.0f} ({100 * (s.iloc[-1] / START - 1):+4.0f}%)  worst drop {100 * (1 - s / s.cummax()).max():3.0f}%  "
              f"green {(mret > 0).sum()}/{len(mret)}  floor {'never' if below is None else below.date()}")


def main() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    print(f"Delta rule A picks {d.date.min().date()} .. {d.date.max().date()}, {len(d)} trades, real-bot fills, stop 2x ATR, 24 h")
    base = scale_out(d, opens, ps, [])
    rules = [("ladder", "ladder 10/5/3 (today)"), (0.03, "fixed 3%")]
    show("TODAY (no scale-out)", d, base, rules + [("ladder+up", "ladder + up to 12.5/15% when ahead"),
                                                   ("3->7.5", "3% -> 5% -> 7.5% when ahead"), ("5->10", "5% -> 7.5% -> 10% when ahead")])
    print("\n=== 1. SCALE OUT (take profit in steps) ===")
    for plan, be, lab in (([(0.5, 0.2)], False, "1/2 at 0.2R, rest runs"), ([(0.5, 0.5)], False, "1/2 at 0.5R, rest runs"),
                          ([(1 / 3, 0.2), (1 / 3, 0.5)], False, "1/3 at 0.2R + 1/3 at 0.5R, rest runs"),
                          ([(1 / 3, 0.25), (1 / 3, 0.5), (1 / 3, 1.0)], False, "1/3 at 0.25R, 0.5R, 1R"),
                          ([(1 / 3, 0.2), (1 / 3, 0.5)], True, "1/3 at 0.2R + 1/3 at 0.5R, stop to entry")):
        show(lab, d, scale_out(d, opens, ps, plan, be), rules)
    print("\n=== 3. TRAILING STOP (owner: \"or trailing SL?\") ===")
    for act, trail in ((0.25, 0.25), (0.5, 0.5), (0.5, 0.25), (1.0, 0.5), (0.0, 1.0)):
        show(f"trail {trail}R behind the best price, from +{act}R", d, trailing(d, opens, ps, act, trail), rules)


def lev_main() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    print(f"Delta rule A picks {d.date.min().date()} .. {d.date.max().date()}, real-bot fills; 50% of the balance as margin "
          f"per day (split over the picks); half closed at +0.2R, rest to the 2x-ATR stop or 24 h; from ${START:,.0f}")
    for lev in (1, 2, 3, 5, 10, "full"):
        s, dd, below, n, nl, nh, ns = leveraged(d, opens, ps, lev)
        ye = s.groupby(s.index.year).last()
        dead = s[s < 1]
        print(f"  {str(lev):>4s}{'x' if lev != 'full' else ''}: -> ${s.iloc[-1]:>9,.0f}  worst drop {100 * dd:3.0f}%  "
              f"2025 end ${ye.get(2025, np.nan):>8,.0f}  | of {n}: half taken {nh} ({100 * nh / n:.0f}%), liquidated {nl} "
              f"({100 * nl / n:.0f}%), stopped {ns} ({100 * ns / n:.0f}%)  floor {'never' if below is None else below.date()}"
              + (f"  $0 on {dead.index[0].date()}" if len(dead) else ""))


def lev30() -> None:
    """Owner 2026-11-12: "how about 20% at 30% captial" (read as 20x leverage, 30% of capital as margin)."""
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    print("30% of the balance as margin per day, half closed at +0.2R, rest to the 2x-ATR stop or 24 h, from $1,640")
    for lev in (1, 2, 3, 5, 10, 20):
        s, dd, below, n, nl, nh, ns = leveraged(d, opens, ps, lev, margin_total=0.30)
        dead = s[s < 1]
        print(f"  {lev:>2d}x: -> ${s.iloc[-1]:>9,.0f}  worst drop {100 * dd:3.0f}%  liquidated {nl} of {n} ({100 * nl / n:.0f}%)  "
              f"stopped {ns}  floor {'never' if below is None else below.date()}" + (f"  $0 on {dead.index[0].date()}" if len(dead) else ""))


def as_today() -> None:
    """Owner 2026-11-12: "5% with 50% captial with ladder and quick as they are today" (read as 5x, 50% margin):
    rule A unchanged (no partial) and QUICK unchanged (all closed at +0.2R), across leverage."""
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    for take, nm in ((0.0, "LADDER / rule A as today (stop 2x ATR, 24 h)"), (1.0, "QUICK as today (all closed at +0.2R)")):
        print(f"\n{nm}: 50% of the balance as margin per day, from $1,640")
        for lev in (1, 2, 3, 5):
            s, dd, below, n, nl, nh, ns = leveraged(d, opens, ps, lev, margin_total=0.50, take=take)
            dead = s[s < 1]
            ye = s.groupby(s.index.year).last()
            print(f"  {lev}x: -> ${s.iloc[-1]:>9,.0f}  worst drop {100 * dd:3.0f}%  2025 end ${ye.get(2025, np.nan):>7,.0f}  "
                  f"liquidated {nl} ({100 * nl / n:.0f}%)  stopped {ns} ({100 * ns / n:.0f}%)"
                  + (f"  target {nh} ({100 * nh / n:.0f}%)" if take else "")
                  + f"  floor {'never' if below is None else below.date()}" + (f"  $0 on {dead.index[0].date()}" if len(dead) else ""))


if __name__ == "__main__":
    as_today() if "--today" in sys.argv else lev30() if "--lev30" in sys.argv else lev_main() if "--lev" in sys.argv else main()
