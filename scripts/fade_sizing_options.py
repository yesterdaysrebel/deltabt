"""Position-sizing options for the REAL account, rule A vs QUICK, on Delta's own 2025-26 picks (owner 2026-11-12: "The
stratergy is working fine but it has major problems around risk reward and position sizing").

    PYTHONPATH=. python scripts/fade_sizing_options.py

Trades: Delta rule A picks 2025-01-15..2026-10-27 as scripts/fade_ladder_5x.trades (stop 2x ATR14%, skip > 32.5%,
24 h, Delta fee + funding, Binance 1m path). QUICK = the same trades with a target 0.2 x the stop distance below the
entry, filled when the 1m low touches it (as paper QUICK). Sizing as the live bot (live/fade.lever_plan: notional =
risk / stop, capped at 60% of the balance and 30% margin x 1-3x, liquidation >= 2x the stop away): no liquidations.
Options: fixed 2 / 3 / 5 / 10% risk; ladder 10/5/3 (live); ladder 5/3/2 (same steps, half the risk).
From $1,640 (today's wallet); the run's $1,000 floor is marked, the account keeps going so the whole period shows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_data as FD  # noqa: E402
import fade_ladder_5x as L5  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402
from live.fade import PAPER_MM_PCT, lever_plan  # noqa: E402

START, FLOOR = 1640.0, 1000.0


def quick_r(d: pd.DataFrame) -> np.ndarray:
    """R of each trade with QUICK's 0.2x target (touch on the 1m low), stop first in a tie; funding kept pro rata."""
    g = G.panel()
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)
    to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731
    bsym = d.symbol.map(to_b)
    t = d.date.map(lambda x: int(pd.Timestamp(x).timestamp()))          # unit-safe (dates may be datetime64[us])
    opens = bd.set_index(["symbol", "time"]).open
    paths = list(FD.POOL.map(lambda x: FD.path_day(x[0], int(x[1])), zip(bsym, t)))
    out, missing = [], 0
    for (bs, tt, dist, r), a in zip(zip(bsym, t, d.dist, d.r), paths):
        o = opens.get((bs, tt), np.nan)
        if not np.isfinite(o) or not np.isfinite(a[1]).any():
            missing += 1
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        stop, tgt = o * (1 + dist), o * (1 - 0.2 * dist)
        s = np.nonzero(hi >= stop)[0]
        k = np.nonzero(lo <= tgt)[0]
        if len(k) and (not len(s) or k[0] < s[0]):
            out.append((0.2 * dist - 0.0005 - 0.0005 * (1 - 0.2 * dist)) / dist)
        else:
            out.append(r)                                                  # stop or 24 h: as rule A
    if missing:
        print(f"  (QUICK: {missing} trades without a Binance open or 1m lows kept as rule A)")
    return np.array(out)


def realistic(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Rule A and QUICK R with the REAL bot's fills: entry at the 00:03 close (stop/target re-based on it), target must
    trade 0.1% through, 0.1% spread round trip, fee 0.05% a side; Delta funding kept as in `r` (r minus its price part)."""
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)
    to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731
    bsym = d.symbol.map(to_b)
    t = d.date.map(lambda x: int(pd.Timestamp(x).timestamp()))
    opens = bd.set_index(["symbol", "time"]).open
    paths = list(FD.POOL.map(lambda x: FD.path_day(x[0], int(x[1])), zip(bsym, t)))
    fee, extra, thr = 0.0005, 0.001, 0.001
    ra, rq = [], []
    for (bs, tt, dist, r), a in zip(zip(bsym, t, d.dist, d.r), paths):
        o = opens.get((bs, tt), np.nan)
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not (np.isfinite(o) and np.isfinite(cl[2]) and np.isfinite(cl[-1])):
            ra.append(np.nan)
            rq.append(np.nan)
            continue
        s0 = np.nonzero(hi >= o * (1 + dist))[0]                      # the ideal price-only R, to isolate funding
        xp0 = max(o * (1 + dist), cl[s0[0]]) if len(s0) else cl[-1]
        fund = r - (-(xp0 / o - 1) - fee - fee * xp0 / o) / dist
        e = cl[2]
        stop, tgt = e * (1 + dist), e * (1 - 0.2 * dist)
        s = np.nonzero(hi[3:] >= stop)[0]
        k = np.nonzero(lo[3:] <= tgt * (1 - thr))[0]
        xs = max(stop, cl[3 + s[0]]) if len(s) else cl[-1]
        ret_a = -(xs / e - 1) - fee - fee * xs / e - extra
        ra.append(ret_a / dist + fund)
        if len(k) and (not len(s) or k[0] < s[0]):
            rq.append((-(tgt / e - 1) - fee - fee * tgt / e - extra) / dist + fund * (k[0] + 3) / 1440)
        else:
            rq.append(ret_a / dist + fund)
    return np.array(ra), np.array(rq)


def account(d, rcol, rule):
    eq = hw = START
    path, below, wins, losses = {}, None, [], []
    for t, g in d.groupby("date"):
        if rule == "ladder 10/5/3":
            under = 1 - eq / hw
            risk = 0.10 if under < 0.20 else 0.05 if under < 0.35 else 0.03
        elif rule == "ladder 5/3/2":
            under = 1 - eq / hw
            risk = 0.05 if under < 0.20 else 0.03 if under < 0.35 else 0.02
        else:
            risk = rule
        pnl = 0.0
        for dist, r in zip(g.dist, g[rcol]):
            x = eq * lever_plan(risk, dist, PAPER_MM_PCT)[0] * dist * r
            (wins if x > 0 else losses).append(x)
            pnl += x
        eq = max(eq + pnl, 0.0)
        hw = max(hw, eq)
        path[t] = eq
        if below is None and eq < FLOOR:
            below = t
    return pd.Series(path), below, np.array(wins), np.array(losses)


def main() -> None:
    d = L5.trades()
    d["quick"] = quick_r(d)
    d["r_real"], d["quick_real"] = realistic(d)
    d = d[np.isfinite(d.r_real) & np.isfinite(d.quick_real)].reset_index(drop=True)
    yrs = d.date.dt.year
    print(f"Delta rule A picks {d.date.min().date()} .. {d.date.max().date()}: {len(d)} trades")
    for col, nm in (("r", "RULE A (24 h)"), ("quick", "QUICK (0.2x target)"), ("r_real", "RULE A, real fills"), ("quick_real", "QUICK, real fills")):
        r = d[col]
        print(f"  {nm:20s} mean {r.mean():+.3f}R  win {100 * (r > 0).mean():.0f}%  avg win {r[r > 0].mean():+.2f}R  "
              f"avg loss {r[r <= 0].mean():+.2f}R | 2025 {r[yrs == 2025].mean():+.3f}  2026 {r[yrs == 2026].mean():+.3f}")
    print(f"\nFROM ${START:,.0f}: end balance, worst drop, first day under the $1,000 floor, worst month, green months, "
          f"median $ win / $ loss per trade")
    for col, nm in (("r", "RULE A (ideal fills)"), ("quick", "QUICK (ideal fills)"), ("r_real", "RULE A (REAL fills)"), ("quick_real", "QUICK (REAL fills)")):
        print(f"\n  {nm}")
        for rule in (0.02, 0.03, 0.05, 0.10, "ladder 10/5/3", "ladder 5/3/2"):
            s, below, w, l_ = account(d, col, rule)
            m = s.groupby(s.index.to_period("M")).last()
            mret = m / m.shift(1).fillna(START) - 1
            dd = (1 - s / s.cummax()).max()
            y25 = s[s.index.year == 2025].iloc[-1]
            lab = f"{100 * rule:.0f}% fixed" if isinstance(rule, float) else rule
            print(f"    {lab:14s} -> ${s.iloc[-1]:>8,.0f}  (2025 end ${y25:>7,.0f})  drop {100 * dd:3.0f}%  "
                  f"floor {'never' if below is None else below.date()}  worst month {100 * mret.min():+4.0f}%  "
                  f"green {(mret > 0).sum()}/{len(mret)}  win ${np.median(w):>5.0f} / loss ${np.median(l_):>6.0f}")


if __name__ == "__main__":
    main()
