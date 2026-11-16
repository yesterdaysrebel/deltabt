"""Owner 2026-11-14: "it's still possible with 1600 capital, we can stop the bot when we make 2000$". Start $1,600,
every trade uses the WHOLE balance as isolated margin at leverage L (position = L x balance), takes profit at +$500
(the owner's cap), stop 4x ATR(14) on 15m; liquidation at 1/L - 0.5% (Delta maintenance on large coins).
Entries: %R(140) band cross with SuperTrend(10, 2) aligned, 15m, the 10 large coins pooled in time order (one
trade at a time). A run ends at +$2,000 (balance >= $3,600: GOAL) or when the balance is under $50 (BUST).
Replayed from 300 start dates spread over 2021-2026.

    PYTHONPATH=. python scripts/wpr_goal.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import trend_data as T  # noqa: E402
import wpr_band as W  # noqa: E402

COST, START, GOAL = W.COST, 1600.0, 2000.0
TAKE = float(sys.argv[sys.argv.index("--take") + 1]) if "--take" in sys.argv else 500.0


def paths():
    out = []
    for s in T.COINS:
        d = W.load(s, "15m")
        h, l, c = d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float)
        for r in W.trades(d, "4atr", "aligned", None):       # entries and stop only; exits recomputed below
            out.append((r[7], s, r[8], r[1], r[5]))
    return sorted(out), {s: W.load(s, "15m") for s in T.COINS}


def play(sig, data, lev, k0):
    bal, n, busy_until = START, 0, 0
    for t, s, i, side, dist in sig[k0:]:
        if t < busy_until:
            continue
        d = data[s]
        h, l, c, tm = d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float), d.time.to_numpy()
        e = c[i]
        pos = lev * bal
        tk = TAKE / pos                                      # +$500 as a price move
        liq = 1 / lev - 0.005
        sp = min(dist, liq * 0.95)                           # the stop sits before liquidation
        end = min(i + W.N, len(c) - 1)
        xp, j = c[end], end
        for k in range(i + 1, end + 1):
            hs = h[k] >= e * (1 + sp) if side < 0 else l[k] <= e * (1 - sp)
            ht = l[k] <= e * (1 - tk) * 0.9995 if side < 0 else h[k] >= e * (1 + tk) * 1.0005
            if hs:
                xp = max(e * (1 + sp), c[k]) if side < 0 else min(e * (1 - sp), c[k])
                j = k
                break
            if ht:
                xp, j = e * (1 - tk) if side < 0 else e * (1 + tk), k
                break
        ret = side * (xp / e - 1) - COST
        bal = max(bal + max(pos * ret, -bal), 0.0)           # isolated: never lose more than the margin
        n += 1
        busy_until = tm[j]
        if bal >= START + GOAL:
            return "GOAL", n, bal, t
        if bal < 50:
            return "BUST", n, bal, t
    return "END", n, bal, None


def main() -> None:
    sig, data = paths()
    starts = np.linspace(0, len(sig) - 400, 300).astype(int)
    print(f"GOAL +${GOAL:,.0f} FROM ${START:,.0f}, take +${TAKE:.0f} a trade, whole balance as margin. 15m %R(140) cross + "
          f"SuperTrend aligned, 10 large coins, {len(sig):,} signals 2021-2026; 300 start dates\n")
    for lev in (5, 10, 20):
        res = [play(sig, data, lev, k) for k in starts]
        g = [r for r in res if r[0] == "GOAL"]
        b = [r for r in res if r[0] == "BUST"]
        ev = np.mean([r[2] for r in res]) - START
        print(f"  {lev:2d}x (position ${lev * START:,.0f} at the start; +$500 = {100 * TAKE / (lev * START):.2f}% move): "
              f"reached +$2,000 in {100 * len(g) / len(res):3.0f}% of runs (median {np.median([r[1] for r in g]) if g else 0:.0f} trades) | "
              f"lost the account in {100 * len(b) / len(res):3.0f}% (median {np.median([r[1] for r in b]) if b else 0:.0f} trades) | "
              f"average result per run ${ev:+,.0f}")




def monthly(sig, data, lev, k0, withdraw):
    """Owner 2026-11-14: "making 500 per trade and stop when account has made 2000 per month". Months in a row from
    signal k0: trade until the month is +$2,000, then wait for the 1st. withdraw=True: at each month end anything
    above $1,600 is taken out (income); False: it stays in. Ends when the balance is under $50."""
    bal, month, m_start, busy_until, done = START, None, START, 0, False
    goals, months, out_usd = 0, 0, 0.0
    for t, s, i, side, dist in sig[k0:]:
        m = pd.Timestamp(t, unit="s").strftime("%Y-%m")
        if m != month:
            if month is not None:
                months += 1
                if withdraw and bal > START:
                    out_usd += bal - START
                    bal = START
            month, m_start, done = m, bal, False
        if done or t < busy_until:
            continue
        d = data[s]
        h, l, c, tm = d.high.to_numpy(float), d.low.to_numpy(float), d.close.to_numpy(float), d.time.to_numpy()
        e = c[i]
        pos = lev * bal
        tk, liq = TAKE / pos, 1 / lev - 0.005
        sp = min(dist, liq * 0.95)
        end = min(i + W.N, len(c) - 1)
        xp, j = c[end], end
        for k in range(i + 1, end + 1):
            hs = h[k] >= e * (1 + sp) if side < 0 else l[k] <= e * (1 - sp)
            ht = l[k] <= e * (1 - tk) * 0.9995 if side < 0 else h[k] >= e * (1 + tk) * 1.0005
            if hs:
                xp, j = (max(e * (1 + sp), c[k]) if side < 0 else min(e * (1 - sp), c[k])), k
                break
            if ht:
                xp, j = (e * (1 - tk) if side < 0 else e * (1 + tk)), k
                break
        ret = side * (xp / e - 1) - COST
        bal = max(bal + max(pos * ret, -bal), 0.0)
        busy_until = tm[j]
        if bal - m_start >= GOAL:
            goals += 1
            done = True
        if bal < 50:
            return months + 1, goals, out_usd, 0.0, True
    return months, goals, out_usd, bal, False


def main_monthly() -> None:
    sig, data = paths()
    t0 = np.array([x[0] for x in sig])
    firsts = sorted({pd.Timestamp(t, unit="s").strftime("%Y-%m") for t in t0})[:-6]
    starts = [int(np.searchsorted(t0, pd.Timestamp(m + "-01").timestamp())) for m in firsts]
    print(f"MONTHLY GOAL +${GOAL:,.0f}, +${TAKE:.0f} a trade, whole balance as margin, from ${START:,.0f}. 15m %R(140) cross + "
          f"SuperTrend, 10 large coins. One run per start month ({len(starts)} runs, 2021-01..), until bust or data end\n")
    for withdraw in (False, True):
        print("PROFITS WITHDRAWN EACH MONTH (always trades from $1,600)" if withdraw else "PROFITS STAY IN THE ACCOUNT")
        for lev in (10, 20):
            res = [monthly(sig, data, lev, k, withdraw) for k in starts]
            months = np.array([r[0] for r in res]); goals = np.array([r[1] for r in res]); bust = np.array([r[4] for r in res])
            got = np.array([r[2] + r[3] for r in res])
            surv3 = np.mean([not (r[4] and r[0] <= 3) for r in res])
            print(f"  {lev:2d}x: busted in {100 * bust.mean():3.0f}% of runs, median {np.median(months[bust]) if bust.any() else 0:.0f} months in | "
                  f"survived 3+ months {100 * surv3:3.0f}% | months that hit +$2,000: median {np.median(goals):.0f} per run "
                  f"(of {np.median(months):.0f} months traded) | money out + left, median ${np.median(got):,.0f}, mean ${got.mean():,.0f} "
                  f"(put in $1,600)")
        print()


if __name__ == "__main__":
    main_monthly() if "--monthly" in sys.argv else main()
