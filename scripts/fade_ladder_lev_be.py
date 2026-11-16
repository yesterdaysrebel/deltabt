"""Owner 2026-11-16: "how about 25% margin with 5-8x leverage with SL moved to break even". Rule A ladder picks as
scripts/fade_ladder_be.py (1,022, real-bot fills, 24 h, funding). Each trade: MARGIN 25% of the balance (ladder 25 ->
12.5 -> 7.5% at 20% / 35% under the high-water mark), notional = L x margin, isolated. Liquidation when a 1m high reaches
entry x (1 + 1/L - 2.5%): the margin is lost (Delta's liquidation fee, ~0.6% of the notional on the real SOPH
liquidation, is NOT added -- lenient). Stops: A = the rule's 2x ATR (liquidation fires first where it is closer);
B = the stop tightened to half the liquidation distance where 2x ATR is wider. Exit: hold, or once the price is +act R
in favour (R = the stop in use) the stop moves once to break-even (entry less round-trip fees). Stop fills at the worse of
the stop and the minute's close. From $3,184, no new trades under $1,000. Loop and vectorised versions asserted equal.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/fade_ladder_lev_be.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_ladder_be as BE  # noqa: E402
import fade_rr12 as R12  # noqa: E402

FEE, EXTRA, MM = BE.FEE, BE.EXTRA, 0.025
START, FLOOR = 3184.0, 1000.0


def trade1(dist0, r, o, a, L, mode, act):
    """Return (outcome, return on notional incl. funding) -- loop."""
    p = BE._prep(dist0, r, o, a)
    if p is None:
        return None
    hi, lo, cl, fund_r = p
    e = cl[2]
    liq_d = 1 / L - MM
    dist = dist0 if mode == "A" else min(dist0, liq_d / 2)
    stop, liq, moved = e * (1 + dist), e * (1 + liq_d), False
    for m in range(3, 1440):
        if hi[m] >= min(stop, liq):
            if liq <= stop or max(stop, cl[m]) >= liq:
                return "LIQ", None, m
            xp = max(stop, cl[m])
            return ("BE" if moved else "STOP"), -(xp / e - 1) - FEE - FEE * xp / e - EXTRA + fund_r * dist0 * (m + 1) / 1440, m
        if not moved and act is not None and lo[m] <= e * (1 - act * dist):
            stop, moved = min(stop, e * (1 - (2 * FEE + EXTRA))), True
    xp = cl[-1]
    return "24h", -(xp / e - 1) - FEE - FEE * xp / e - EXTRA + fund_r * dist0, 1439


def trade2(dist0, r, o, a, L, mode, act):
    """Same, vectorised."""
    p = BE._prep(dist0, r, o, a)
    if p is None:
        return None
    hi, lo, cl, fund_r = p
    e = cl[2]
    liq_d = 1 / L - MM
    dist = dist0 if mode == "A" else min(dist0, liq_d / 2)
    h, l, c = hi[3:1440], lo[3:1440], cl[3:1440]
    reach = np.nonzero(l <= e * (1 - act * dist))[0] if act is not None else np.array([], int)
    mv = reach[0] if len(reach) else len(l)
    idx = np.arange(len(h))
    stops = np.where(idx > mv, min(e * (1 + dist), e * (1 - (2 * FEE + EXTRA))), e * (1 + dist))
    liq = e * (1 + liq_d)
    hit = np.nonzero(h >= np.minimum(stops, liq))[0]
    if not len(hit):
        xp = cl[-1]
        return "24h", -(xp / e - 1) - FEE - FEE * xp / e - EXTRA + fund_r * dist0, 1439
    k = hit[0]
    xp = max(stops[k], c[k])
    if liq <= stops[k] or xp >= liq:
        return "LIQ", None, 3 + k
    return ("BE" if k > mv else "STOP"), -(xp / e - 1) - FEE - FEE * xp / e - EXTRA + fund_r * dist0 * (3 + k + 1) / 1440, 3 + k


def run(d, opens, ps, L, mode, act, fn, m0=0.25):
    eq = hw = START
    below, path, out = None, {}, []
    for t, idx in d.groupby("date").groups.items():
        if eq < FLOOR:
            path[t] = eq
            continue
        under = 1 - eq / hw
        m = m0 * (0.3 if under >= 0.35 else 0.5 if under >= 0.20 else 1.0)
        pnl = 0.0
        for i in idx:
            res = fn(d.dist.iat[i], d.r.iat[i], opens[i], ps[i], L, mode, act)
            if res is None:
                continue
            how, ret, _ = res
            got = -m * eq if how == "LIQ" else max(m * L * eq * ret, -m * eq)
            pnl += got
            out.append((how, got / eq * START))
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), below, out


def main() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    print(f"Rule A ladder picks {d.date.min().date()} .. {d.date.max().date()} ({len(d)}); stop 2x ATR median {d.dist.median():.0%}; "
          f"25% margin a trade, from ${START:,.0f}.\n")
    print(f"  {'L':>2}  {'stop':34s} {'exit':24s}| trades  liquidated  full stop  break-even  24 h | avg $/trade at $3,184 | end balance  worst drop  floor")
    for L in (3, 5, 8):
        for mode, mlab in (("A", "rule's 2x ATR (liq. first if closer)"), ("B", "tightened to half the liq. distance")):
            if L == 3 and mode == "B":
                continue
            for act, alab in ((None, "hold 24 h"), (0.25, "at +0.25R: to break-even"), (0.5, "at +0.5R: to break-even")):
                s, below, out = run(d, opens, ps, L, mode, act, trade1)
                s2, below2, out2 = run(d, opens, ps, L, mode, act, trade2)
                assert len(out) == len(out2) and all(x[0] == y[0] and abs(x[1] - y[1]) < 1e-6 for x, y in zip(out, out2)), (L, mode, act)
                n = len(out)
                c = pd.Series([x[0] for x in out]).value_counts()
                dol = np.mean([x[1] for x in out])
                print(f"  {L}x  {mlab:34s} {alab:24s}| {n:5d}   {100 * c.get('LIQ', 0) / n:6.0f}%    {100 * c.get('STOP', 0) / n:6.0f}%    "
                      f"{100 * c.get('BE', 0) / n:6.0f}%   {100 * c.get('24h', 0) / n:4.0f}% | {dol:+10.2f}           | ${s.iloc[-1]:>9,.0f}  "
                      f"{100 * (1 - s / s.cummax()).max():6.0f}%   {'never' if below is None else below.date()}")
        print()
    print("CHECK PASSED: loop and vectorised trades agree on every trade of every account.")


def ten_ten() -> None:
    """Owner 2026-11-16: "why not 10% margin at 10x and break even SL?" -- same trades, 10% margin (ladder 10/5/3)."""
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    print(f"10% margin a trade (ladder 10 -> 5 -> 3%), from ${START:,.0f}; position = L x margin.\n")
    print(f"  {'L':>3}  {'stop':34s} {'exit':24s}| trades  liquidated  full stop  break-even  24 h | avg $/trade at $3,184 | end balance  worst drop  floor")
    for L in (3, 10):
        for mode, mlab in (("A", "rule's 2x ATR (liq. first if closer)"), ("B", "tightened to half the liq. distance")):
            if L == 3 and mode == "B":
                continue
            for act, alab in ((None, "hold 24 h"), (0.25, "at +0.25R: to break-even"), (0.5, "at +0.5R: to break-even"), (1.0, "at +1R: to break-even")):
                s, below, out = run(d, opens, ps, L, mode, act, trade1, 0.10)
                s2, below2, out2 = run(d, opens, ps, L, mode, act, trade2, 0.10)
                assert len(out) == len(out2) and all(x[0] == y[0] and abs(x[1] - y[1]) < 1e-6 for x, y in zip(out, out2)), (L, mode, act)
                n = len(out)
                c = pd.Series([x[0] for x in out]).value_counts()
                print(f"  {L:>2}x  {mlab:34s} {alab:24s}| {n:5d}   {100 * c.get('LIQ', 0) / n:6.0f}%    {100 * c.get('STOP', 0) / n:6.0f}%    "
                      f"{100 * c.get('BE', 0) / n:6.0f}%   {100 * c.get('24h', 0) / n:4.0f}% | {np.mean([x[1] for x in out]):+10.2f}           | "
                      f"${s.iloc[-1]:>9,.0f}  {100 * (1 - s / s.cummax()).max():6.0f}%   {'never' if below is None else below.date()}")
        print()
    print("CHECK PASSED: loop and vectorised trades agree on every trade of every account.")


if __name__ == "__main__":
    ten_ten() if "--ten" in sys.argv else main()
