"""QUICK at full leverage, with the stops and exits tested so far combined (owner 2026-11-12: "run quick test with
full leverage (could vary coin to coin), combine the stops you testes").

    PYTHONPATH=. python scripts/fade_quick_leverage.py

Picks, paths and fee as scripts/fade_owner_exits.py (Binance rule A picks 2021-26, 1m paths, 0.05% a side, before
funding). Each day's two picks each get HALF the balance as margin; notional = leverage x margin.
LEVERAGE per coin from Delta's own table (out/delta_margin/perp_margins_2026-11-12.json, public /v2/products):
max = 100 / initial margin %, liquidation when the price moves (1/L - maintenance) against the short. Coins not on
Delta get the commonest alt setting (5% / 2.5%: 20x, liquidation 2.5% away). A liquidation loses the whole margin.
Levels: 1x, 2x, 3x, 5x, 10x and FULL (the coin's max).
Exits combined (each a real option already tested at 1x):
  stop    2x ATR14% (rule A) | 1x ATR14% | none (liquidation / 24 h only)
  target  QUICK 0.2 x the 2x-ATR distance | 2% | 5% | none
  stuck   none | 4 h inside 3% while in profit (the best of the 36 band rules)
Whichever comes first; a liquidation in the same minute as anything else counts first, a stop before a target.
The account compounds per day; it is dead at 0. Open exploration of 72 x 6 cells: read the tables, not the best cell.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_owner_exits as X  # noqa: E402

T, FEE = X.T, X.FEE
START = 1500.0
LEVELS = (1, 2, 3, 5, 10, "full")


def margins(p):
    tab = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))

    def one(s):
        base = s[:-4] if s.endswith("USDT") else s
        for c in (base + "USD", base.replace("1000", "", 1) + "USD", "1000" + base + "USD"):
            if c in tab:
                return tab[c][0] / 100, tab[c][1] / 100, True
        return 0.05, 0.025, False
    m = [one(s) for s in p.symbol]
    return np.array([x[0] for x in m]), np.array([x[1] for x in m]), np.array([x[2] for x in m])


def first_at(mask):
    return X.first(mask)


def run(p, hi, lo, cl, im, mm, lev, stop_k, target, stuck, through=0.0, extra=0.0):
    n = len(p)
    entry, dist = p.open.to_numpy(), p.dist.to_numpy()                 # dist = 2 x ATR14%
    L = 1 / im if lev == "full" else np.minimum(float(lev), 1 / im)
    liq = 1 / L - mm
    m_liq = first_at(hi >= (entry * (1 + liq))[:, None])
    cand = [(m_liq, entry * (1 + liq), 0)]                              # (minute, price, priority)
    if stop_k:
        sp = entry * (1 + dist * stop_k / 2)
        ms = first_at(hi >= sp[:, None])
        cand.append((ms, np.maximum(sp, cl[np.arange(n), np.minimum(ms, T - 1)]), 1))
    if target is not None:
        tp = entry * (1 - (0.2 * dist if target == "quick" else target))
        cand.append((first_at(lo <= (tp * (1 - through))[:, None]), tp, 2))
    if stuck:
        cand.append((*X.stall(p, cl, 4, 0.03, "profit"), 3))
    cand.append((np.full(n, T - 1), cl[:, T - 1], 4))
    M = np.stack([c[0] for c in cand]) * 10 + np.array([c[2] for c in cand])[:, None]
    k = M.argmin(axis=0)
    xp = np.stack([c[1] for c in cand])[k, np.arange(n)]
    liquidated = k == 0
    ret = -(xp / entry - 1) - FEE - FEE * xp / entry - extra           # per unit notional
    rb = np.where(liquidated, -0.5, 0.5 * L * ret)                     # share of the balance
    return rb, liquidated, L


def account(p, rb):
    eq, path, days = START, [], []
    for t, g in pd.DataFrame({"t": p.time.to_numpy(), "r": rb}).groupby("t", sort=True):
        eq = max(eq * (1 + g.r.sum()), 0.0)
        path.append(eq)
        days.append(t)
    path = np.array(path)
    peak = np.maximum.accumulate(path)
    dd = (1 - path / np.where(peak > 0, peak, 1)).max()
    s = pd.Series(path, index=pd.to_datetime(days, unit="s"))
    return s, dd


def main() -> None:
    p, hi, lo, cl = X.load()
    im, mm, on_delta = margins(p)
    yrs = p.year.to_numpy()
    print(f"n {len(p)} picks 2021-26; {on_delta.mean():.0%} found in Delta's table; max leverage: "
          + ", ".join(f"{int(v)}x {c}" for v, c in zip(*np.unique(np.round(1 / im), return_counts=True))))
    print(f"median 2x-ATR stop {100 * np.median(p.dist):.1f}% away; liquidation at full leverage is "
          f"{100 * np.median(1 / (1 / im) - mm):.1f}% away (median)\n")
    rows = []
    for stop_k in (2, 1, 0):
        for target in ("quick", 0.02, 0.05, None):
            for stuck in (False, True):
                for lev in LEVELS:
                    rb, liq, L = run(p, hi, lo, cl, im, mm, lev, stop_k, target, stuck)
                    s, dd = account(p, rb)
                    ye = s.groupby(s.index.year).last()
                    rows.append(dict(stop={2: "2x ATR", 1: "1x ATR", 0: "none"}[stop_k],
                                     target=f"{100 * target:.0f}%" if isinstance(target, float) else {"quick": "QUICK 0.2x", None: "none"}[target],
                                     stuck="4h/3%" if stuck else "-", lev=str(lev), end=s.iloc[-1], dd=dd,
                                     liq=liq.mean(), win=(rb > 0).mean(), mean=rb.mean(),
                                     y25_26=s[s.index.year >= 2025].iloc[-1] / s[s.index.year < 2025].iloc[-1] if s[s.index.year < 2025].iloc[-1] > 0 else np.nan,
                                     **{f"y{y}": v for y, v in ye.items()}))
    d = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 500)
    print("ALL CELLS: $1,500 -> end; worst drop; share of trades liquidated; win rate; year-end balances")
    for (st, tg, sk), g in d.groupby(["stop", "target", "stuck"], sort=False):
        print(f"\n  stop {st:6s} target {tg:10s} stuck {sk}")
        for r in g.itertuples():
            ys = " ".join(f"{getattr(r, f'y{y}'):>9,.0f}" for y in (2021, 2022, 2023, 2024, 2025, 2026))
            print(f"    {r.lev:>4s}  -> ${r.end:>11,.0f}  drop {100 * r.dd:3.0f}%  liquidated {100 * r.liq:4.1f}%  win {100 * r.win:3.0f}%"
                  f"  mean {100 * r.mean:+6.2f}% of bal | {ys}")
    print("\nSUMMARY")
    for lev in map(str, LEVELS):
        g = d[d.lev == lev]
        print(f"  {lev:>4s}: cells ending above $1,500: {(g.end > START).sum()} of {len(g)}; ending at $0: {(g.end < 1).sum()}; "
              f"best ${g.end.max():,.0f} ({g.loc[g.end.idxmax(), ['stop', 'target', 'stuck']].tolist()}, drop {100 * g.loc[g.end.idxmax(), 'dd']:.0f}%); "
              f"median ${g.end.median():,.0f}")


def robustness(p, hi, lo, cl, im, mm) -> None:
    """The standout cell (1x-2x, 2x-ATR stop, small fixed target) under real-bot conditions, and its neighbours."""
    print("\nROBUSTNESS of the small fixed target (2x-ATR stop; $1,500 -> end, worst drop, years up of 6)")
    print("  real bot: enters ~3 min after 00:00 at the bid; a resting buy fills only when traded THROUGH; spread costs")
    variants = [("as above (open, touch fills, no spread)", 0, 0.0, 0.0),
                ("enter at minute 3 close", 3, 0.0, 0.0),
                ("+ target must trade 0.2% through", 3, 0.002, 0.0),
                ("+ 0.10% spread round trip", 3, 0.002, 0.001),
                ("+ 0.20% spread round trip", 3, 0.002, 0.002)]
    for tgt in (0.01, 0.015, 0.02, 0.025, 0.03, 0.04):
        for lev in (1, 2):
            print(f"  target {100 * tgt:.1f}%  {lev}x")
            for nm, e, thr, ex in variants:
                if e:
                    q = p.copy()
                    q["open"] = cl[:, e - 1]
                    h2, l2 = hi.copy(), lo.copy()
                    h2[:, :e], l2[:, :e] = -np.inf, np.inf
                else:
                    q, h2, l2 = p, hi, lo
                ok = np.isfinite(q.open.to_numpy()) & (q.open.to_numpy() > 0)
                rb, _, _ = run(q[ok].reset_index(drop=True), h2[ok], l2[ok], cl[ok], im[ok], mm[ok], lev, 2, tgt, False, thr, ex)
                s, dd = account(q[ok].reset_index(drop=True), rb)
                ye = s.groupby(s.index.year).last()
                start = np.r_[START, ye.to_numpy()[:-1]]
                up = int((ye.to_numpy() > start).sum())
                print(f"    {nm:42s} -> ${s.iloc[-1]:>11,.0f}  drop {100 * dd:3.0f}%  years up {up}/6  mean {100 * rb.mean():+.3f}% of bal")


if __name__ == "__main__":
    if "--robust" in sys.argv:
        p, hi, lo, cl = X.load()
        im, mm, _ = margins(p)
        robustness(p, hi, lo, cl, im, mm)
    else:
        main()
