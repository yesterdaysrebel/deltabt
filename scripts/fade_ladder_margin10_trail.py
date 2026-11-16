"""Owner 2026-11-16: "test current ladder with 10% margin, trailing stop". Rule A ladder picks (the real bot's rule):
Delta rule A picks 2025-01-15..2026-10-27, real-bot fills, stop 2x ATR14% (<= 32.5%), 24 h, funding (fade_profit_ladder).
Two sizings, both stepping down 10 -> 5 -> 3% at 20% / 35% under the high-water mark, from $3,184, no new trades under
the $1,000 floor:
  TODAY   : RISK 10% of the balance per trade (lever_plan: notional = risk / stop, <= 60%, margin <= 30%, 1-3x)
  MARGIN  : 10% of the balance as MARGIN per trade, at the most leverage (1-3x) that keeps liquidation >= 2x the stop
            away (the ladder's own rule); loss never more than that margin
Exits: hold (today) and trailing stops (from +act R the stop follows the lowest price at trail R above it).
The trailing exit is computed twice (fade_profit_ladder.trailing, loop; trail2 here, vectorised) and asserted equal;
"hold" is also checked as trail2 that never activates == fade_profit_ladder.scale_out with no plan.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/fade_ladder_margin10_trail.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as P  # noqa: E402
import fade_rr12 as R12  # noqa: E402
from live.fade import LIQ_BUFFER, MAX_LEV, PAPER_MM_PCT, lever_plan  # noqa: E402

FEE, EXTRA = P.FEE, P.EXTRA
START, FLOOR = 3184.0, 1000.0
TRAILS = [(0.25, 0.25), (0.5, 0.25), (0.5, 0.5), (1.0, 0.5), (0.0, 1.0), (0.5, 1.0), (1.0, 1.0)]


def trail2(d, opens, ps, act, trail):
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
        base = e * (1 + dist)
        best = np.minimum.accumulate(np.concatenate([[e], lo[3:1440]]))   # best[k] = lowest low before minute 3+k
        on = (e - best) / e >= act * dist
        stops = np.where(on, np.minimum(base, best * (1 + trail * dist)), base)[:-1]
        hit = np.nonzero(hi[3:1440] >= stops)[0]
        m_exit, xp = (3 + hit[0], max(stops[hit[0]], cl[3 + hit[0]])) if len(hit) else (1439, cl[-1])
        ret = -(xp / e - 1) - FEE - FEE * xp / e - EXTRA
        out.append(ret / dist + fund_r * (m_exit + 1) / 1440)
    return np.array(out)


def top_lev(dist):
    mm = PAPER_MM_PCT / 100
    ok = [L for L in range(1, MAX_LEV + 1) if 1.0 / L - mm >= LIQ_BUFFER * dist]
    return max(ok) if ok else 0


def account(d, r, sizing):
    eq = hw = START
    below, path, n, dollars = None, {}, 0, []
    for t, idx in d.groupby("date").groups.items():
        if eq < FLOOR:
            path[t] = eq
            continue
        under = 1 - eq / hw
        step = 0.03 if under >= 0.35 else 0.05 if under >= 0.20 else 0.10
        pnl = 0.0
        for i in idx:
            if not np.isfinite(r[i]):
                continue
            dist = d.dist.iat[i]
            if sizing == "risk":
                share = lever_plan(step, dist, PAPER_MM_PCT)[0]
                cap = share                       # 1x-3x with liquidation >= 2x the stop: the stop fires first
            else:
                L = top_lev(dist)
                share, cap = step * L, step
            if share <= 0:
                continue
            got = max(eq * share * dist * r[i], -eq * cap)
            pnl += got
            n += 1
            dollars.append(got / eq * START)      # the same trade at the starting size
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), below, n, np.array(dollars)


def main() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    yrs = d.date.dt.year.to_numpy()
    hold = P.scale_out(d, opens, ps, [])
    h2 = trail2(d, opens, ps, np.inf, 1.0)
    assert np.allclose(hold, h2, equal_nan=True, atol=1e-9), "hold differs"
    exits = [("hold 24 h (today)", hold)]
    for act, tr in TRAILS:
        a, b = P.trailing(d, opens, ps, act, tr), trail2(d, opens, ps, act, tr)
        assert np.allclose(a, b, equal_nan=True, atol=1e-5), (act, tr, np.nanmax(np.abs(a - b)))
        tiny = int(np.nansum(np.abs(a - b) > 1e-9))
        if tiny:
            print(f"  (trail {tr}R from +{act}R: {tiny} trades differ by at most {np.nanmax(np.abs(a - b)):.1e} R -- float rounding)")
        exits.append((f"trail {tr}R behind, from +{act}R", a))
    lev = d.dist.map(top_lev)
    print(f"Rule A ladder picks {d.date.min().date()} .. {d.date.max().date()}: {len(d)} trades, stop 2x ATR "
          f"(median {d.dist.median():.0%}); at 10% margin the rule allows 3x on {(lev == 3).mean():.0%}, 2x on "
          f"{(lev == 2).mean():.0%}, 1x on {(lev == 1).mean():.0%}, none on {(lev == 0).mean():.0%}. From ${START:,.0f}, ladder 10/5/3.\n")
    for sizing, lab in (("risk", "TODAY: 10% RISK per trade (notional ~risk/stop, median ~40% of the balance)"),
                        ("margin", "10% MARGIN per trade (notional 10-30% of the balance)")):
        print(lab)
        print(f"  {'exit':32s} | R/trade  win  | 2025 R   2026 R | end balance  worst drop  months up  floor        | avg $ per trade at $3,184")
        for nm, r in exits:
            ok = np.isfinite(r)
            s, below, n, dol = account(d, r, sizing)
            m = s.groupby(s.index.to_period("M")).last()
            mret = m / m.shift(1).fillna(START) - 1
            print(f"  {nm:32s} | {np.nanmean(r):+.3f}  {100 * (r[ok] > 0).mean():3.0f}% | {np.nanmean(r[ok & (yrs == 2025)]):+.3f}  "
                  f"{np.nanmean(r[ok & (yrs == 2026)]):+.3f} | ${s.iloc[-1]:>9,.0f}  {100 * (1 - s / s.cummax()).max():6.0f}%   "
                  f"{(mret > 0).sum():2d}/{len(mret)}     {'never     ' if below is None else below.date()} | ${dol.mean():+7.2f} ({n} trades)")
        print()
    print("CHECK PASSED: two trailing implementations agree (to 1e-5 R) on every trade of every exit; hold matches the no-plan exit.")


if __name__ == "__main__":
    main()
