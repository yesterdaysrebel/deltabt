"""Are the history numbers trustworthy? The history uses Delta's pick list but BINANCE 1m candles as the price path
(Delta's own 1m candles exist only for the 20 real picks). This runs the final run-4 rules on the same 20 real picks
twice -- Delta 1m vs Binance 1m -- and compares trade by trade. Also counts Delta picks dropped for no Binance match.

    PYTHONPATH=. python scripts/run4_delta_vs_binance.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import fade_families_data as FD  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402
import run4_trailing as T  # noqa: E402

bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
have = set(bd.symbol)
to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731


def bin_path(sym, day):
    b = to_b(sym)
    if b is None:
        return None, None
    a = FD.path_day(b, int(pd.Timestamp(day).timestamp()))
    if not np.isfinite(a[2]).any():
        return b, None
    hi = np.where(np.isfinite(a[0]), a[0], -np.inf).astype(float)
    lo = np.where(np.isfinite(a[1]), a[1], np.inf).astype(float)
    cl = pd.Series(a[2].astype(float)).ffill().bfill().to_numpy()
    return b, (hi, lo, cl)


def main():
    C.checks()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    mg, ex = 0.49 * C.START, 0.25 * C.START
    print("Final run-4 rules, fixed $3,184 per trade, same 20 real picks: Delta 1m vs Binance 1m price path\n")
    for le, nm in ((False, "STRICT"), (True, "LENIENT")):
        td = tb = 0.0
        same = n = 0
        print(f"  {nm}")
        for day, sym, hi, lo, cl, k0 in real:
            a = T.short(sym, hi, lo, cl, k0, mg, 500.0, 200.0, ex, le, 0.008, None)
            bsym, bp = bin_path(sym, day)
            b = None if bp is None else T.short(sym, *bp, k0, mg, 500.0, 200.0, ex, le, 0.008, None)
            ra = "no fill" if a is None else f"{a[0]:6s} {a[1]:+8.0f}"
            rb = ("no Binance data" if bp is None else "no fill") if b is None else f"{b[0]:6s} {b[1]:+8.0f}"
            n += 1
            same += (a is None and b is None) or (a is not None and b is not None and a[0] == b[0])
            td += 0 if a is None else a[1]
            tb += 0 if b is None else b[1]
            print(f"    {pd.Timestamp(day).date()} {sym:14s} {str(bsym):18s} Delta {ra:16s} | Binance {rb}")
        print(f"    same outcome {same}/{n}; total Delta ${td:+,.0f} vs Binance ${tb:+,.0f}\n")
    g = G.panel()
    g = g[g.eligible & np.isfinite(g.atrp) & (g.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False])
    g = g.groupby("time").head(2)
    g = g[2.0 * g.atrp <= 0.325]
    m = g.symbol.map(to_b).notna()
    print(f"History Delta picks 2025-01..2026-10: {len(g)}; with a Binance match {int(m.sum())}; DROPPED {int((~m).sum())} "
          f"({(~m).mean():.0%})")


if __name__ == "__main__":
    main()
