"""Basis (premium index: perp price vs index) as a cross-sectional predictor, Binance perps 2021-2026, and on
the coins Delta had listed at the time (2025-26). Open exploration, not a result.

    PYTHONPATH=. python scripts/explore_basis.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import explore_wide as W  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402

YEARS = W.YEARS


def rep(name, s, extra=""):
    s = pd.Series(s).dropna()
    if len(s) < 30:
        print(f"  {name:50s} (too few)")
        return
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    per = [s[s.index.year == int(y)].mean() if (s.index.year == int(y)).sum() >= 8 else np.nan for y in YEARS]
    cells = " ".join(f"{100 * m:+5.2f}" if np.isfinite(m) else "  n/a" for m in per)
    print(f"  {name:50s} n {len(s):4d}  {100 * s.mean():+6.3f}%  t {t:+5.2f}{extra} | {cells}{W.flag(per, t)}")


def main() -> None:
    O, H, L, C, V = W.panel()
    C = C.ffill(limit=7)
    hist = C.notna().cumsum()
    v30 = V.rolling(30).mean()
    pr = pd.read_parquet("out/binance_paths/premium.parquet")
    pr["date"] = pd.to_datetime(pr.time, unit="s")
    P = pr.pivot_table(index="date", columns="symbol", values="prem_mean").reindex(index=C.index, columns=C.columns)
    PC = pr.pivot_table(index="date", columns="symbol", values="prem_close").reindex(index=C.index, columns=C.columns)
    print(f"premium rows {len(pr)}; coins {P.notna().any().sum()}; median daily premium {1e4 * P.stack().median():+.2f} bp")
    sig = {"LOW basis minus high (yesterday's mean premium)": -P, "LOW basis minus high (7-day mean premium)": -P.rolling(7).mean(),
           "LOW basis minus high (30-day mean premium)": -P.rolling(30).mean(), "LOW basis at the close minus high": -PC,
           "basis FELL most (1d vs 7d mean) minus rose": -(P - P.rolling(7).mean())}
    dd = G.daily()
    DC = dd.assign(date=pd.to_datetime(dd.time, unit="s")).pivot(index="date", columns="symbol", values="close")
    first = DC.notna().idxmax()
    dmap = {s: c for s in C.columns for c in (s[:-4] + "USD", s[:-4].replace("1000", "", 1) + "USD") if c in DC.columns}
    on_delta = pd.DataFrame({s: (C.index - first[dmap[s]]).days >= 30 for s in dmap}, index=C.index).reindex(columns=C.columns, fill_value=False)

    def run(x, h, mask, frac=5, floor=5e6):
        liquid = (v30 >= floor) & (hist >= 60) & mask
        fwd = C.shift(-h) / C - 1
        rows, turn, prev = {}, [], None
        for t in C.index[90::h]:
            u = liquid.loc[t]
            a, f = x.loc[t][u], fwd.loc[t][u]
            ok = a.notna() & f.notna()
            if ok.sum() < 20:
                continue
            a, f = a[ok], f[ok]
            k = max(len(a) // frac, 3)
            o = a.sort_values()
            Ls, Ss = set(o.index[-k:]), set(o.index[:k])
            rows[t] = f[list(Ls)].mean() - f[list(Ss)].mean()
            if prev:
                turn.append((2 - len(Ls & prev[0]) / k - len(Ss & prev[1]) / k) / 2)
            prev = (Ls, Ss)
        return pd.Series(rows), float(np.mean(turn)) if turn else np.nan

    everything = pd.DataFrame(True, index=C.index, columns=C.columns)
    for h in (1, 7):
        print(f"\nALL BINANCE PERPS, hold {h} day(s): long top fifth minus short bottom fifth, % per rebalance, gross")
        for nm, x in sig.items():
            s, tn = run(x, h, everything)
            rep(nm, s, f"  turn {100 * tn:3.0f}%")
    print("\nCOINS DELTA HAD LISTED AT THE TIME (2025-26), gross")
    for h in (1, 7):
        for nm, x in sig.items():
            s, tn = run(x, h, on_delta)
            rep(f"hold {h}d: {nm[:40]}", s, f"  turn {100 * tn:3.0f}%")


if __name__ == "__main__":
    main()
