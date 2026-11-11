"""Rule A ladder (as the real account runs) vs rule A ladder + skip-negative-funding vs funding collection
(every 3 days, fifths), SAME dates, each from $1,500 (owner 2026-11-11: "compare them with ladder").

    PYTHONPATH=. python scripts/compare_ladder_funding.py

Delta's picks / prices / real funding, fee + live spread (see scripts/backtest_survivors.py). Open exploration.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import backtest_survivors as B  # noqa: E402

START = 1500.0


def main() -> None:
    C, V, F, cost = B.delta_panel()
    liquid = (V.rolling(30).mean() >= 2.5e5) & (C.notna().cumsum() >= 60)
    fper, fpath = B.portfolio(C, F, cost, -F.rolling(3, min_periods=2).mean(), liquid, 3, 0.2)
    fper, fpath = pd.Series(fper), pd.Series(fpath)
    t0, t1 = fpath.index[0], fpath.index[-1] + pd.Timedelta(days=3)

    d = B.rule_a_trades()
    d = d[(d.date >= t0) & (d.date < t1)]
    accts = {}
    for nm, k in (("Rule A ladder (real account)", d), ("Rule A ladder + skip negative funding", d[d.f_prev >= 0])):
        per, path = B.account_rule_a(k, "ladder")
        accts[nm] = (pd.Series(per), pd.Series(path), k)
    accts["Funding collection (3-day)"] = (fper, fpath, None)

    print(f"SAME WINDOW {t0.date()} .. {t1.date()}, each from $1,500\n")
    print(f"  {'':40s} {'end $':>8s} {'return':>7s} {'worst drop':>10s} {'win rate':>22s} {'green months':>13s} {'best/worst month':>18s} {'longest losing run':>20s}")
    monthly = {}
    for nm, (per, path, k) in accts.items():
        path = pd.concat([pd.Series({t0 - pd.Timedelta(days=1): START}), path])
        dd = (1 - path / path.cummax()).max()
        m = path.groupby(path.index.to_period("M")).last()
        mret = m.pct_change().dropna()
        first = m.index[0]
        mret = pd.concat([pd.Series({first: m.iloc[0] / START - 1}), mret]) if first != (t0 - pd.Timedelta(days=1)).to_period("M") else mret
        mret = mret[mret.index >= t0.to_period("M")]
        monthly[nm] = (mret, m)
        if k is not None:
            wr = f"{100 * (k.r > 0).mean():.0f}% of {len(k)} trades"
            run = max((len(list(g)) for kk, g in itertools.groupby(k.sort_values("time").r <= 0) if kk), default=0)
            run_s = f"{run} trades"
        else:
            wr = f"{100 * (per > 0).mean():.0f}% of {len(per)} periods"
            run = max((len(list(g)) for kk, g in itertools.groupby(per <= 0) if kk), default=0)
            run_s = f"{run} periods ({3 * run} days)"
        print(f"  {nm:40s} ${path.iloc[-1]:7,.0f} {100 * (path.iloc[-1] / START - 1):+6.0f}% {100 * dd:9.0f}% {wr:>22s} "
              f"{(mret > 0).sum():>5d} of {len(mret):<3d}   {100 * mret.max():+6.1f}% / {100 * mret.min():+6.1f}%   {run_s:>18s}")

    print("\nYEAR BY YEAR (account at year end; return in the year)")
    for nm, (mret, m) in monthly.items():
        cells = []
        prev = START
        for y in (2025, 2026):
            e = m[m.index.year == y].iloc[-1]
            cells.append(f"{y}: ${prev:,.0f} -> ${e:,.0f} ({100 * (e / prev - 1):+.0f}%)")
            prev = e
        print(f"  {nm:40s} " + "   ".join(cells))

    print("\nMONTH BY MONTH (% return in the month)")
    names = list(monthly)
    print(f"  {'month':8s} " + "".join(f"{n[:30]:>32s}" for n in names))
    months = sorted(set().union(*[set(monthly[n][0].index) for n in names]))
    both_red = 0
    for p in months:
        vals = [monthly[n][0].get(p, np.nan) for n in names]
        both_red += int(vals[0] < 0 and vals[2] < 0)
        print(f"  {str(p):8s} " + "".join(f"{100 * v:+31.1f}%" if np.isfinite(v) else f"{'':>32s}" for v in vals))
    a, f = monthly[names[0]][0], monthly[names[2]][0]
    j = a.index.intersection(f.index)
    print(f"\n  months where BOTH the ladder and funding collection lost: {both_red} of {len(months)}; "
          f"correlation of their monthly returns {a[j].corr(f[j]):+.2f}")
    # half and half
    comb = 0.5 * a[j] + 0.5 * f[j]
    eq = START * (1 + comb).cumprod()
    print(f"  HALF IN EACH (ladder + funding, rebalanced monthly): ${eq.iloc[-1]:,.0f}; green months {(comb > 0).sum()} of {len(comb)}; "
          f"worst month {100 * comb.min():+.1f}%; worst drop {100 * (1 - eq / eq.cummax()).max():.0f}% (month-end)")


if __name__ == "__main__":
    main()
