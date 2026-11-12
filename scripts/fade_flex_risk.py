"""Flexible risk per trade, full SAFE leverage, 50% of capital (owner 2026-11-12: "you would avoid liquidations
right? how about flexible risk per trade can you test that with full leverage and 50% capital?").

    PYTHONPATH=. python scripts/fade_flex_risk.py

Rule A picks on Binance 2021-26 (scripts/fade_owner_exits.load), REALISTIC fills: enter at the 00:03 close, a target
must trade 0.1% through, a stop fills at the worse of the stop and that minute's close, 0.1% spread round trip,
fee 0.05% a side. Exits: stop 2x ATR14% (rule A) or a fixed 2% / 4%; target QUICK (0.2x the stop) or none (24 h).

NO LIQUIDATIONS BY DESIGN, as the live bot (live/fade.lever_plan): per coin, the highest leverage up to Delta's max
(out/delta_margin) whose liquidation (1/L - maintenance) sits >= 2x the stop away. Margin = 50% of the balance,
split over the day's picks (25% each). Size = risk / stop, capped at what that margin and leverage carry.
A stop fill past liquidation (a gap) would still lose the whole margin -- counted, none expected.

Risk rules (state updated once a day, from results known before the next day's entries):
  fixed 2% / 5% / 10%
  ladder        10% / 5% / 3% at drawdown < 10% / 10-25% / > 25% (run 3's ladder)
  after-win-up  start 5%; +1% after a green day, -1% after a red day; 2..10%
  after-loss-up start 5%; +1% after a red day, -1% after a green day; 2..10% (martingale-style, for contrast)
  adaptive      Kelly from the last 200 trades' R (mean / variance), half of it, 0..10%; 2% until 200 trades
Open exploration: 3 stops x 2 targets x 7 rules.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_owner_exits as X  # noqa: E402
import fade_quick_leverage as Q  # noqa: E402

T, FEE = X.T, X.FEE
START = 1500.0
MARGIN_SHARE = 0.50
LIQ_BUFFER = 2.0


def trades(p, hi, lo, cl, im, mm, stop_of, tk, through=0.001, extra=0.001):
    """Per pick: return per unit notional, stop distance, safe leverage, gapped-past-liquidation flag."""
    n = len(p)
    entry = p.open.to_numpy()
    stop = stop_of(p)
    lmax = 1 / im
    # highest L <= max with 1/L - mm >= LIQ_BUFFER * stop
    L = np.floor(np.minimum(lmax, 1 / (LIQ_BUFFER * stop + mm)))
    L = np.where(L >= 1, L, 0)
    liq = np.where(L > 0, 1 / np.maximum(L, 1) - mm, 0)
    sp = entry * (1 + stop)
    ms = X.first(hi >= sp[:, None])
    cand = [(ms, np.maximum(sp, cl[np.arange(n), np.minimum(ms, T - 1)]), 0)]
    if tk:
        tp = entry * (1 - tk * stop)
        cand.append((X.first(lo <= (tp * (1 - through))[:, None]), tp, 1))
    cand.append((np.full(n, T - 1), cl[:, T - 1], 2))
    M = np.stack([c[0] for c in cand]) * 10 + np.array([c[2] for c in cand])[:, None]
    k = M.argmin(axis=0)
    xp = np.stack([c[1] for c in cand])[k, np.arange(n)]
    ret = -(xp / entry - 1) - FEE - FEE * xp / entry - extra
    gap = (L > 0) & (xp / entry - 1 >= liq)
    return ret, stop, L, gap


def simulate(p, ret, stop, L, gap, rule):
    df = pd.DataFrame({"t": p.time.to_numpy(), "ret": ret, "stop": stop, "L": L, "gap": gap})
    eq, peak, step, hist = START, START, 0.05, []
    path, days, used, capped, gaps = [], [], [], 0, 0
    for t, g in df.groupby("t", sort=True):
        dd = 1 - eq / peak
        if rule == "ladder":
            risk = 0.10 if dd < 0.10 else 0.05 if dd < 0.25 else 0.03
        elif rule in ("after-win-up", "after-loss-up"):
            risk = step
        elif rule == "adaptive":
            if len(hist) < 200:
                risk = 0.02
            else:
                h = np.array(hist[-200:])
                risk = float(np.clip(0.5 * h.mean() / h.var(), 0.0, 0.10)) if h.var() > 0 else 0.0
        else:
            risk = float(rule)
        margin_each = MARGIN_SHARE / len(g)
        pnl = 0.0
        for x in g.itertuples():
            if x.L <= 0:
                continue
            want = risk / x.stop
            can = x.L * margin_each
            size = min(want, can)
            capped += want > can
            used.append(size * x.stop)
            if x.gap:
                gaps += 1
                pnl -= margin_each
            else:
                pnl += size * x.ret
            hist.append(x.ret / x.stop)
        eq = max(eq * (1 + pnl), 0.0)
        peak = max(peak, eq)
        if rule == "after-win-up":
            step = float(np.clip(step + (0.01 if pnl > 0 else -0.01 if pnl < 0 else 0), 0.02, 0.10))
        elif rule == "after-loss-up":
            step = float(np.clip(step + (0.01 if pnl < 0 else -0.01 if pnl > 0 else 0), 0.02, 0.10))
        path.append(eq)
        days.append(t)
    s = pd.Series(path, index=pd.to_datetime(days, unit="s"))
    pk = np.maximum.accumulate(s.to_numpy())
    dd = float((1 - s.to_numpy() / np.where(pk > 0, pk, 1)).max())
    return s, dd, float(np.mean(used)) if used else 0.0, capped / max(len(used), 1), gaps


def main() -> None:
    p0, hi0, lo0, cl = X.load()
    im0, mm0, _ = Q.margins(p0)
    e = 3
    p = p0.copy()
    p["open"] = cl[:, e - 1]
    hi, lo = hi0.copy(), lo0.copy()
    hi[:, :e], lo[:, :e] = -np.inf, np.inf
    ok = np.isfinite(p.open.to_numpy()) & (p.open.to_numpy() > 0)
    p, hi, lo, c, im, mm = p[ok].reset_index(drop=True), hi[ok], lo[ok], cl[ok], im0[ok], mm0[ok]
    rules = [0.02, 0.05, 0.10, "ladder", "after-win-up", "after-loss-up", "adaptive"]
    stops = {"2x ATR (rule A)": lambda q: q.dist.to_numpy(), "fixed 2%": lambda q: np.full(len(q), 0.02),
             "fixed 4%": lambda q: np.full(len(q), 0.04)}
    print("REALISTIC fills, 50% of capital as margin, safe leverage (liquidation >= 2x the stop away), from $1,500")
    for sn, sf in stops.items():
        for tk, tn in ((0.2, "QUICK target 0.2x the stop"), (0, "no target (24 h)")):
            ret, stop, L, gap = trades(p, hi, lo, c, im, mm, sf, tk)
            r = ret / stop
            yrs = p.year.to_numpy()
            print(f"\n  stop {sn}, {tn}: mean {r.mean():+.3f}R per trade, win {100 * (r > 0).mean():.0f}%, "
                  f"by year " + " ".join(f"{y} {r[yrs == y].mean():+.3f}" for y in X.YEARS)
                  + f" | median leverage {int(np.median(L[L > 0]))}x")
            for rule in rules:
                s, dd, avg_risk, cap, gaps = simulate(p, ret, stop, L, gap, rule)
                ye = s.groupby(s.index.year).last()
                up = int((ye.to_numpy() > np.r_[START, ye.to_numpy()[:-1]]).sum())
                nm = f"{100 * rule:.0f}% fixed" if isinstance(rule, float) else rule
                ys = " ".join(f"{v:>9,.0f}" for v in ye.to_numpy())
                print(f"    {nm:14s} -> ${s.iloc[-1]:>10,.0f}  drop {100 * dd:3.0f}%  up {up}/6  avg risk {100 * avg_risk:4.1f}%  "
                      f"capped {100 * cap:3.0f}%  gaps {gaps} | {ys}")


def targets() -> None:
    """Owner 2026-11-12: "test diffrent target %". Fixed % targets x stops, same realistic fills and safe leverage."""
    p0, hi0, lo0, cl = X.load()
    im0, mm0, _ = Q.margins(p0)
    p = p0.copy()
    p["open"] = cl[:, 2]
    hi, lo = hi0.copy(), lo0.copy()
    hi[:, :3], lo[:, :3] = -np.inf, np.inf
    ok = np.isfinite(p.open.to_numpy()) & (p.open.to_numpy() > 0)
    p, hi, lo, c, im, mm = p[ok].reset_index(drop=True), hi[ok], lo[ok], cl[ok], im0[ok], mm0[ok]
    yrs = p.year.to_numpy()
    stops = {"2x ATR": lambda q: q.dist.to_numpy(), "1x ATR": lambda q: q.dist.to_numpy() / 2,
             "fixed 4%": lambda q: np.full(len(q), 0.04), "fixed 8%": lambda q: np.full(len(q), 0.08),
             "fixed 12%": lambda q: np.full(len(q), 0.12)}
    print("FIXED % TARGETS, realistic fills, safe leverage, 50% of capital; mean R, win, years with mean R > 0, "
          "and $1,500 -> end at 2% / 5% fixed risk and the ladder")
    for sn, sf in stops.items():
        print(f"\n  stop {sn}")
        for tgt in (None, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30):
            stop = sf(p)
            tk = 0 if tgt is None else tgt / stop                      # target as a multiple of each pick's stop
            ret, st, L, gap = trades(p, hi, lo, c, im, mm, sf, 0)
            if tgt is not None:
                ret, st, L, gap = trades_k(p, hi, lo, c, im, mm, sf, tk)
            r = ret / st
            pos = sum(r[yrs == y].mean() > 0 for y in X.YEARS)
            ends = [simulate(p, ret, st, L, gap, rule)[0].iloc[-1] for rule in (0.02, 0.05, "ladder")]
            nm = "none (24 h)" if tgt is None else f"{100 * tgt:.0f}%"
            print(f"    target {nm:11s} mean {r.mean():+.3f}R  win {100 * (r > 0).mean():3.0f}%  years + {pos}/6 | "
                  + " ".join(f"{y[2:]} {r[yrs == y].mean():+.3f}" for y in X.YEARS)
                  + f" | 2%: ${ends[0]:>8,.0f}  5%: ${ends[1]:>8,.0f}  ladder: ${ends[2]:>8,.0f}")


def trades_k(p, hi, lo, cl, im, mm, stop_of, tk, through=0.001, extra=0.001):
    """trades() with a per-pick target multiple tk (array)."""
    n = len(p)
    entry = p.open.to_numpy()
    stop = stop_of(p)
    L = np.floor(np.minimum(1 / im, 1 / (LIQ_BUFFER * stop + mm)))
    L = np.where(L >= 1, L, 0)
    liq = np.where(L > 0, 1 / np.maximum(L, 1) - mm, 0)
    sp = entry * (1 + stop)
    ms = X.first(hi >= sp[:, None])
    tp = entry * (1 - tk * stop)
    cand = [(ms, np.maximum(sp, cl[np.arange(n), np.minimum(ms, T - 1)]), 0),
            (X.first(lo <= (tp * (1 - through))[:, None]), tp, 1), (np.full(n, T - 1), cl[:, T - 1], 2)]
    M = np.stack([c[0] for c in cand]) * 10 + np.array([c[2] for c in cand])[:, None]
    k = M.argmin(axis=0)
    xp = np.stack([c[1] for c in cand])[k, np.arange(n)]
    ret = -(xp / entry - 1) - FEE - FEE * xp / entry - extra
    return ret, stop, L, (L > 0) & (xp / entry - 1 >= liq)


if __name__ == "__main__":
    targets() if "--targets" in sys.argv else main()
