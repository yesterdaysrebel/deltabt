"""The best exit style and risk per trade for the real account when the owner accepts more risk (owner 2026-11-12: "I am
ready to take more risk get me a optimal R&R, position size").

    PYTHONPATH=. python scripts/fade_optimal_size.py

Trades: Delta's rule A picks 2025-01-15..2026-10-27 (1,022), real-bot fills (scripts/fade_profit_ladder.scale_out).
Exit styles: rule A (stop 2x ATR, 24 h) ; QUICK (all at +0.2R) ; HALF (half at +0.2R, the rest runs).
Sizing: risk r of the balance per trade, notional = r / stop distance, leverage only as high as keeps liquidation
>= 2x the stop away ((1/L - 2.5%) >= 2 x stop, up to 3x), at most 25% of the balance as margin per pick (50% a day).
So a wide stop may not reach the risk asked for; the table shows the risk actually taken.
Kelly: f* = mean R / variance of R per trade (the risk that maximises long-run growth if the past repeats); half
Kelly is the usual practical ceiling because the mean is uncertain. Shown for all trades and for 2025 / 2026.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as PL  # noqa: E402
import fade_rr12 as R12  # noqa: E402

START, FLOOR, MM = 1640.0, 1000.0, 0.025


def run(d, r, risk):
    eq = hw = START
    below, path, used = None, {}, []
    for t, idx in d.groupby("date").groups.items():
        pnl = 0.0
        for i in idx:
            if not np.isfinite(r[i]):
                continue
            dist = d.dist.iat[i]
            lmax = max(1, min(3, int(1 / (2 * dist + MM)))) if 1 / 1 - MM >= 2 * dist else 0
            if lmax == 0:
                continue
            notional = min(risk / dist, 0.25 * lmax)
            used.append(notional * dist)
            pnl += eq * notional * dist * r[i]
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), below, np.mean(used)


def main() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    yrs = d.date.dt.year.to_numpy()
    styles = {"RULE A (24 h)": PL.scale_out(d, opens, ps, []),
              "QUICK (all at +0.2R)": PL.scale_out(d, opens, ps, [(1.0, 0.2)]),
              "HALF at +0.2R": PL.scale_out(d, opens, ps, [(0.5, 0.2)])}
    for nm, r in styles.items():
        ok = np.isfinite(r)
        rr = r[ok]
        k_all = rr.mean() / rr.var()
        k_y = {y: r[ok & (yrs == y)].mean() / r[ok & (yrs == y)].var() for y in (2025, 2026)}
        w, l_ = rr[rr > 0], rr[rr <= 0]
        print(f"\n{nm}: {rr.mean():+.3f}R per trade, win {100 * (rr > 0).mean():.0f}%, avg win {w.mean():+.2f}R / avg loss "
              f"{l_.mean():+.2f}R (realised reward:risk {w.mean() / -l_.mean():.2f}:1)")
        print(f"  Kelly risk per trade: all {100 * k_all:.1f}%  (2025 {100 * k_y[2025]:+.1f}%, 2026 {100 * k_y[2026]:+.1f}%); "
              f"half Kelly {100 * k_all / 2:.1f}%")
        for risk in (0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15):
            s, below, used = run(d, r, risk)
            m = s.groupby(s.index.to_period("M")).last()
            mret = m / m.shift(1).fillna(START) - 1
            ye = s.groupby(s.index.year).last()
            print(f"    risk {100 * risk:4.0f}% (taken {100 * used:4.1f}%) -> ${s.iloc[-1]:>7,.0f}  2025 end ${ye.get(2025):>7,.0f}  "
                  f"worst drop {100 * (1 - s / s.cummax()).max():3.0f}%  worst month {100 * mret.min():+4.0f}%  "
                  f"green {(mret > 0).sum()}/{len(mret)}  floor {'never' if below is None else below.date()}")


def more_risk_12() -> None:
    """Owner 2026-11-13: "run ladder with more risk i.e have more contract with more leverage and 1:2 R&R". Risk 10/15/20%,
    leverage up to 5x (no liquidation buffer: liquidation at 1/L - 2.5% loses the margin), 50% of the balance as margin
    a day, target 2x the stop; stops 2x ATR and 1x ATR. Real-bot fills (scripts/fade_rr12.simulate)."""
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    for sm, nm in ((2, "1:2, stop 2x ATR (~19%), target ~39%"), (1, "1:2, stop 1x ATR (~10%), target ~19%")):
        res = R12.simulate(d, opens, ps, sm, 2)
        r = np.array([x[0] for x in res])
        sd = np.array([x[2] for x in res])
        print(f"\n{nm}: {np.nanmean(r):+.3f}R per trade, win {100 * np.nanmean(r > 0):.0f}%")
        for risk in (0.05, 0.10, 0.15, 0.20):
            for lev in (3, 5):
                eq = hw = START
                below, path, liq = None, {}, 0
                for t, idx in d.groupby("date").groups.items():
                    idx = [i for i in idx if np.isfinite(r[i])]
                    pnl = 0.0
                    for i in idx:
                        margin = 0.5 * eq / len(idx)
                        notional = min(risk / sd[i], lev * margin)
                        liq_d = 1 / lev - MM
                        if sd[i] >= liq_d:                                 # liquidation sits inside the stop
                            a = ps[i]
                            hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
                            lo = np.where(np.isfinite(a[1]), a[1], np.inf)
                            e = pd.Series(a[2].astype(float)).ffill().to_numpy()[2]
                            ml = np.nonzero(hi[3:] >= e * (1 + liq_d))[0]
                            mt = np.nonzero(lo[3:] <= e * (1 - 2 * sd[i]) * (1 - 0.001))[0]
                            if len(ml) and (not len(mt) or ml[0] <= mt[0]):
                                liq += 1
                                pnl -= eq * notional / lev                 # the margin is lost
                                continue
                        pnl += eq * notional * sd[i] * r[i]
                    eq = max(eq + pnl, 0.0)
                    hw = max(hw, eq)
                    path[t] = eq
                    if below is None and eq < FLOOR:
                        below = t
                s = pd.Series(path)
                dead = s[s < 1]
                print(f"  risk {100 * risk:3.0f}%, up to {lev}x -> ${s.iloc[-1]:>8,.0f}  worst drop {100 * (1 - s / s.cummax()).max():3.0f}%  "
                      f"liquidated {liq}  under $1,000 {below.date() if below is not None else 'never'}"
                      + (f"  $0 on {dead.index[0].date()}" if len(dead) else ""))


def fixed_usd() -> None:
    """Owner 2026-11-13: "run tests with risk of 300$ and profite of 600$". Every trade risks $300 (notional = $300 /
    stop distance) for a $600 target (1:2), no compounding, from $1,640; trading stops when the balance cannot cover the
    margin. Stops 2x / 1x / 0.5x ATR; leverage needed is shown (liquidation >= 2x the stop needs 1/L - 2.5% >= 2 x stop)."""
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    for sm, nm in ((2, "stop 2x ATR"), (1, "stop 1x ATR"), (0.5, "stop 0.5x ATR")):
        res = R12.simulate(d, opens, ps, sm, 2)
        r = np.array([x[0] for x in res])
        sd = np.array([x[2] for x in res])
        how = np.array([x[1] for x in res])
        ok = np.isfinite(r)
        notional = 300 / sd[ok]
        bal, low, low_at, path, broke = START, START, None, {}, None
        for t, idx in d.groupby("date").groups.items():
            for i in idx:
                if not np.isfinite(r[i]) or broke is not None:
                    continue
                bal += 300 * r[i]
                if bal < low:
                    low, low_at = bal, t
                if bal <= 300:
                    broke = t
            path[t] = bal
        sr = pd.Series(path)
        m = sr.groupby(sr.index.to_period("M")).last()
        mchg = m.diff().fillna(m.iloc[0] - START)
        cnt = {k: int((how[ok] == k).sum()) for k in ("STOP", "TARGET", "24H+", "24H-")}
        print(f"\n$300 RISK / $600 TARGET, {nm} (median stop {100 * np.nanmedian(sd):.1f}%): {np.nanmean(r):+.3f}R = "
              f"${300 * np.nanmean(r):+.2f} a trade on average")
        print(f"  position ${np.median(notional):,.0f} median (${np.percentile(notional, 90):,.0f} at the 90th pct) = "
              f"{np.median(notional) / START:.1f}x the $1,640 wallet")
        print(f"  outcomes: stop {cnt['STOP']} (-$300+), target {cnt['TARGET']} (+$600), 24 h up {cnt['24H+']}, 24 h down {cnt['24H-']}")
        print(f"  $1,640 -> ${sr.iloc[-1]:,.0f}; lowest ${low:,.0f} on {low_at.date() if low_at is not None else '-'}; "
              + (f"BROKE (<= $300) on {broke.date()}" if broke is not None else "never broke")
              + f" | worst month ${mchg.min():+,.0f}, best ${mchg.max():+,.0f}, green {(mchg > 0).sum()}/{len(mchg)}")


def fixed_usd_capital() -> None:
    """The $300 / $600 plan on the 2x-ATR stop with unlimited money: the deepest dollar hole, so the capital needed."""
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    res = R12.simulate(d, opens, ps, 2, 2)
    pnl = pd.Series([300 * x[0] for x in res], index=d.date).dropna()
    cum = pnl.groupby(level=0).sum().cumsum()
    hole = (cum - cum.cummax()).min()
    print(f"$300 / $600 on the 2x-ATR stop, unlimited money: total ${cum.iloc[-1]:+,.0f} over {len(pnl)} trades; deepest fall "
          f"from a high ${hole:,.0f}; lowest point vs the start ${cum.min():+,.0f} on {cum.idxmin().date()}")
    print("  by year: " + "  ".join(f"{y} ${v:+,.0f}" for y, v in pnl.groupby(pnl.index.year).sum().items()))


if __name__ == "__main__":
    fixed_usd_capital() if "--usdcap" in sys.argv else fixed_usd() if "--usd" in sys.argv else more_risk_12() if "--r12" in sys.argv else main()
