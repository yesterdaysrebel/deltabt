"""Funding collection: the outside reviewers' ideas 1, 3 and 5 (SYNTHESIS.md, owner approved 2026-11-11),
plus a TIMING CHECK of the baseline itself.

    PYTHONPATH=. python scripts/funding_ideas.py

TIMING. Delta daily candles are stamped at their OPEN, so C.loc[t] is the price at t+1 00:00 -- the entry.
F.loc[t] is the funding settled during day t (00:00, 08:00, 16:00 UTC), i.e. BEFORE that entry. The holding
funding is therefore F.loc[t+1 .. t2], and the score may use F up to and including t.
backtest_survivors.portfolio() books F.loc[t .. t2-1]: one day early, and that day is also inside the score's
3-day window. "lag0" below reproduces it; "lag1" is the honest timing. Everything else uses lag1.

Ideas (all on the baseline: 3-day mean funding, fifths, every 3 days, 1x gross, Delta fee + live spread):
  1  squeeze filter: no short in a coin up > 35% over the last 3 days, no long in a coin down > 35%
  3a rank by the MEDIAN of the last 3 daily funding values (daily data; ~9 settlements for 8h coins)
  3b inverse-volatility weights within each side (20-day std of daily returns)
  3c both
  5  hysteresis: keep a held coin while it stays in its side's top/bottom 35%; skip the rebalance entirely
     when the new picks' expected 3-day funding spread is under 2x the cost of making the trades

PASS BAR, written before the run: mean net return per period beats the baseline in 2025 AND in 2026, and the
paired t-statistic of (idea - baseline) per period over the whole sample is >= 2. Exploration, not a
pre-registered test: a pass is a lead for the paper account, not proof.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import backtest_survivors as B  # noqa: E402

START = 1500.0
H = 3
FRAC = 0.2


def run(C, F, cost, liquid, *, lag=1, score="mean", squeeze=None, invvol=False, buffer=None, min_edge=None):
    ret1 = C.pct_change()
    vol = ret1.rolling(20, min_periods=10).std()
    mom3 = C / C.shift(3) - 1
    if score == "mean":
        S = -F.rolling(3, min_periods=2).mean()
    else:
        S = -F.rolling(3, min_periods=2).median()
    exp3 = F.rolling(3, min_periods=2).mean() * H                    # expected funding per coin over a hold
    per, prev = {}, None
    for t in C.index[60::H]:
        t2 = t + pd.Timedelta(days=H)
        if t2 not in C.index:
            break
        u = liquid.loc[t]
        a = S.loc[t][u].replace([np.inf, -np.inf], np.nan).dropna()
        f = (C.loc[t2] / C.loc[t] - 1)[a.index]
        a = a[f.notna()]
        if len(a) < 20:
            continue
        k = max(int(len(a) * FRAC), 3)
        o = a.sort_values()                                            # low score = high funding = short side
        longs_pool, shorts_pool = list(o.index[::-1]), list(o.index)
        if squeeze is not None:
            m = mom3.loc[t]
            shorts_pool = [s for s in shorts_pool if not (m.get(s, 0) > squeeze)]
            longs_pool = [s for s in longs_pool if not (m.get(s, 0) < -squeeze)]
        Ls, Ss = longs_pool[:k], shorts_pool[:k]
        if buffer is not None and prev is not None:
            kb = max(int(len(a) * buffer), k)
            keepL = [s for s in prev[0] if s in longs_pool[:kb]]
            keepS = [s for s in prev[1] if s in shorts_pool[:kb]]
            Ls = keepL + [s for s in longs_pool if s not in keepL][:max(k - len(keepL), 0)]
            Ss = keepS + [s for s in shorts_pool if s not in keepS][:max(k - len(keepS), 0)]
        if min_edge is not None and prev is not None:
            new = (set(Ls) - set(prev[0])) | (set(Ss) - set(prev[1]))
            if new:
                e = exp3.loc[t]
                gain = 0.5 * (np.nanmean([e.get(s, np.nan) for s in Ss]) - np.nanmean([e.get(s, np.nan) for s in Ls])) \
                    - 0.5 * (np.nanmean([e.get(s, np.nan) for s in prev[1]]) - np.nanmean([e.get(s, np.nan) for s in prev[0]]))
                c = 0.5 * 2 * sum(cost[s] for s in new) / k
                if not (gain >= min_edge * c):
                    Ls, Ss = list(prev[0]), list(prev[1])
                    Ls = [s for s in Ls if s in f.index] or Ls
                    Ss = [s for s in Ss if s in f.index] or Ss
        if invvol:
            def w(names):
                iv = 1 / vol.loc[t][names].clip(lower=1e-3)
                iv = iv.fillna(iv.mean())
                return iv / iv.sum()
            wl, ws = w(Ls), w(Ss)
        else:
            wl = pd.Series(1 / len(Ls), index=Ls)
            ws = pd.Series(1 / len(Ss), index=Ss)
        if lag == 1:
            fu = F.loc[t + pd.Timedelta(days=1):t2].sum()
        else:
            fu = F.loc[t:t2 - pd.Timedelta(days=1)].sum()
        fr = (C.loc[t2] / C.loc[t] - 1)
        px = 0.5 * ((wl * fr[Ls]).sum() - (ws * fr[Ss]).sum())
        fund = 0.5 * (-(wl * fu[Ls].fillna(0)).sum() + (ws * fu[Ss].fillna(0)).sum())
        if prev is None:
            turn = wl.abs().sum() + ws.abs().sum()
            traded = {s: wl.get(s, 0) for s in Ls} | {s: ws.get(s, 0) for s in Ss}
        else:
            pl, ps = prev[2], prev[3]
            traded = {}
            for s in set(Ls) | set(pl.index):
                traded[s] = traded.get(s, 0) + abs(wl.get(s, 0) - pl.get(s, 0))
            for s in set(Ss) | set(ps.index):
                traded[s] = traded.get(s, 0) + abs(ws.get(s, 0) - ps.get(s, 0))
        c = 0.5 * sum(cost[s] * x for s, x in traded.items())
        per[t] = px + fund - c
        prev = (list(Ls), list(Ss), wl, ws)
    return pd.Series(per)


def summary(name, p, base=None):
    eq = START * (1 + p).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    y = {yr: p[p.index.year == yr] for yr in (2025, 2026)}
    s = (f"  {name:44s} {len(p):4d} {100 * p.mean():+7.3f}% {100 * y[2025].mean():+7.3f}% {100 * y[2026].mean():+7.3f}% "
         f"{100 * (p > 0).mean():5.0f}% ${eq.iloc[-1]:8,.0f} {100 * dd:5.0f}%")
    if base is not None:
        j = p.index.intersection(base.index)
        d = p[j] - base[j]
        t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if d.std() > 0 else 0.0
        dy = [d[d.index.year == yr].mean() for yr in (2025, 2026)]
        ok = dy[0] > 0 and dy[1] > 0 and t >= 2
        s += f"   diff {100 * dy[0]:+.3f}% / {100 * dy[1]:+.3f}%  t {t:+.2f}  {'PASS' if ok else 'fail'}"
    print(s)


def main() -> None:
    C, V, F, cost = B.delta_panel()
    liquid = (V.rolling(30).mean() >= 2.5e5) & (C.notna().cumsum() >= 60)
    hdr = f"  {'':44s} {'n':>4s} {'mean':>8s} {'2025':>8s} {'2026':>8s} {'win':>6s} {'$1,500->':>9s} {'drop':>6s}"

    print("TIMING CHECK (baseline)")
    print(hdr)
    old = run(C, F, cost, liquid, lag=0)
    base = run(C, F, cost, liquid, lag=1)
    summary("baseline, funding booked one day early (old)", old)
    summary("baseline, honest timing", base)
    t = base.mean() / (base.std(ddof=1) / np.sqrt(len(base)))
    print(f"  honest baseline t-stat of the mean: {t:+.2f}")
    for yr in (2025, 2026):
        b = base[base.index.year == yr]
        print(f"    {yr}: t {b.mean() / (b.std(ddof=1) / np.sqrt(len(b))):+.2f}")

    print("\nIDEAS vs honest baseline (pass: better in 2025 AND 2026, paired t >= 2)")
    print(hdr)
    summary("baseline", base)
    for name, kw in [
        ("1  squeeze filter 35%", dict(squeeze=0.35)),
        ("1  squeeze filter 25% (sensitivity)", dict(squeeze=0.25)),
        ("1  squeeze filter 50% (sensitivity)", dict(squeeze=0.50)),
        ("3a median score", dict(score="median")),
        ("3b inverse-vol weights", dict(invvol=True)),
        ("3c median + inverse-vol", dict(score="median", invvol=True)),
        ("5  keep while in 35%", dict(buffer=0.35)),
        ("5  keep while in 35% + skip if edge < 2x cost", dict(buffer=0.35, min_edge=2.0)),
    ]:
        summary(name, run(C, F, cost, liquid, **kw), base)


if __name__ == "__main__":
    main()
