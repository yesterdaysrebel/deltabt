"""Owner 2026-11-16: "how about 25% margin without trailing stop but we move SL to break even or make a small money?"
Rule A ladder picks as scripts/fade_ladder_margin10_trail.py (1,022, real-bot fills, stop 2x ATR, 24 h, funding).
Sizing: MARGIN m of the balance per trade at the most leverage (1-3x) keeping liquidation >= 2x the stop away, notional
capped at 60% of the balance (the bot's NOTIONAL_CAP); ladder m -> m/2 -> 0.3m at 20% / 35% under the high-water mark;
from $3,184, no new trades under $1,000. Exit: once the price has fallen `act` R below the entry (best 1m low), the stop
moves ONCE to lock `lock` R (lock 'be' = entry less round-trip fees, net ~0); otherwise the 2x-ATR stop or 24 h. Stop
fills at the worse of the stop and the minute's close. Two implementations (loop / vectorised) asserted equal.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/fade_ladder_be.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_ladder_margin10_trail as M10  # noqa: E402
import fade_profit_ladder as P  # noqa: E402
import fade_rr12 as R12  # noqa: E402

FEE, EXTRA = P.FEE, P.EXTRA
START, FLOOR, NOTIONAL_CAP = 3184.0, 1000.0, 0.60
PLANS = [(0.25, "be"), (0.5, "be"), (1.0, "be"), (0.5, 0.1), (1.0, 0.1), (0.5, 0.25), (1.0, 0.25)]


def _prep(dist, r, o, a):
    hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
    lo = np.where(np.isfinite(a[1]), a[1], np.inf)
    cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
    if not (np.isfinite(o) and np.isfinite(cl[2]) and np.isfinite(cl[-1])):
        return None
    s0 = np.nonzero(hi >= o * (1 + dist))[0]
    xp0 = max(o * (1 + dist), cl[s0[0]]) if len(s0) else cl[-1]
    fund_r = r - (-(xp0 / o - 1) - FEE - FEE * xp0 / o) / dist
    return hi, lo, cl, fund_r


def lock_px(e, dist, lock):
    return e * (1 - (2 * FEE + EXTRA)) if lock == "be" else e * (1 - lock * dist)


def be1(d, opens, ps, act, lock):
    out = []
    for (dist, r), o, a in zip(zip(d.dist, d.r), opens, ps):
        p = _prep(dist, r, o, a)
        if p is None:
            out.append(np.nan)
            continue
        hi, lo, cl, fund_r = p
        e = cl[2]
        stop, moved, m_exit, xp = e * (1 + dist), False, 1439, cl[-1]
        for m in range(3, 1440):
            if hi[m] >= stop:
                m_exit, xp = m, max(stop, cl[m])
                break
            if not moved and lo[m] <= e * (1 - act * dist):
                stop, moved = min(stop, lock_px(e, dist, lock)), True
        ret = -(xp / e - 1) - FEE - FEE * xp / e - EXTRA
        out.append(ret / dist + fund_r * (m_exit + 1) / 1440)
    return np.array(out)


def be2(d, opens, ps, act, lock):
    out = []
    for (dist, r), o, a in zip(zip(d.dist, d.r), opens, ps):
        p = _prep(dist, r, o, a)
        if p is None:
            out.append(np.nan)
            continue
        hi, lo, cl, fund_r = p
        e = cl[2]
        h, l, c = hi[3:1440], lo[3:1440], cl[3:1440]
        base, k = e * (1 + dist), lock_px(e, dist, lock)
        reach = np.nonzero(l <= e * (1 - act * dist))[0]
        mv = reach[0] if len(reach) else len(l)              # the stop is moved after minute mv
        stops = np.where(np.arange(len(h)) > mv, min(base, k), base)
        hit = np.nonzero(h >= stops)[0]
        m_exit, xp = (3 + hit[0], max(stops[hit[0]], c[hit[0]])) if len(hit) else (1439, cl[-1])
        ret = -(xp / e - 1) - FEE - FEE * xp / e - EXTRA
        out.append(ret / dist + fund_r * (m_exit + 1) / 1440)
    return np.array(out)


def account(d, r, m0):
    eq = hw = START
    below, path, n, wins = None, {}, 0, 0
    for t, idx in d.groupby("date").groups.items():
        if eq < FLOOR:
            path[t] = eq
            continue
        under = 1 - eq / hw
        step = m0 * (0.3 if under >= 0.35 else 0.5 if under >= 0.20 else 1.0)
        pnl = 0.0
        for i in idx:
            if not np.isfinite(r[i]):
                continue
            dist, L = d.dist.iat[i], M10.top_lev(d.dist.iat[i])
            if L == 0:
                continue
            share = min(step * L, NOTIONAL_CAP)
            pnl += max(eq * share * dist * r[i], -eq * share / L)
            n += 1
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), below, n


def main() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    yrs = d.date.dt.year.to_numpy()
    hold = P.scale_out(d, opens, ps, [])
    assert np.allclose(hold, be2(d, opens, ps, np.inf, "be"), equal_nan=True, atol=1e-9)
    exits = [("hold 24 h (today)", hold)]
    for act, lock in PLANS:
        a, b = be1(d, opens, ps, act, lock), be2(d, opens, ps, act, lock)
        assert np.allclose(a, b, equal_nan=True, atol=1e-9), (act, lock, np.nanmax(np.abs(a - b)))
        exits.append((f"at +{act}R: stop to {'break-even' if lock == 'be' else f'+{lock}R'}", a))
    print(f"Rule A ladder picks {d.date.min().date()} .. {d.date.max().date()}: {len(d)} trades; from ${START:,.0f}; ladder margin m -> m/2 -> 0.3m.\n")
    print(f"  {'exit':30s} | R/trade  win  stopped at -1R | 2025 R   2026 R | 25% margin: end   worst drop  months up  floor      | 10% margin: end  worst drop")
    for nm, r in exits:
        ok = np.isfinite(r)
        full = (r[ok] < -0.9).mean()
        cells = []
        for m0 in (0.25, 0.10):
            s, below, n = account(d, r, m0)
            mo = s.groupby(s.index.to_period("M")).last()
            up = ((mo / mo.shift(1).fillna(START) - 1) > 0).sum()
            cells.append((s.iloc[-1], 100 * (1 - s / s.cummax()).max(), up, len(mo), below))
        a, b = cells
        print(f"  {nm:30s} | {np.nanmean(r):+.3f}  {100 * (r[ok] > 0).mean():3.0f}%  {100 * full:5.0f}%        | "
              f"{np.nanmean(r[ok & (yrs == 2025)]):+.3f}  {np.nanmean(r[ok & (yrs == 2026)]):+.3f} | ${a[0]:>9,.0f}  {a[1]:6.0f}%    "
              f"{a[2]:2d}/{a[3]}    {'never     ' if a[4] is None else a[4].date()} | ${b[0]:>8,.0f}  {b[1]:6.0f}%")
    print("\nCHECK PASSED: loop and vectorised break-even exits agree on every trade; hold matches the no-plan exit.")


if __name__ == "__main__":
    main()
