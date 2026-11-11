"""Rule A + Williams %R / stop-distance filters, and stopped trades that came back (docs/fade_wpr_stop_filter_prereg.md).

    PYTHONPATH=. python scripts/fade_wpr_filter.py              # judged: Binance 2021-2024
    PYTHONPATH=. python scripts/fade_wpr_filter.py --control    # reported only: Binance 2025-2026

Trades (exact 1m stop/exit, net R) come from scripts/fade_binance_test.py's fetch, run here if not cached.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B  # noqa: E402  (reads --control from sys.argv)

WPR_N, WPR_MIN, S_MAX, Z_BAR = 14, -20.0, 0.20, 2.4


def z(v) -> float:
    v = np.asarray(v, float)
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 1 else float("nan")


def daily_readings() -> pd.DataFrame:
    d = pd.read_parquet(B.OUT / "daily.parquet")
    d = d.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"]).reset_index(drop=True)
    g = d.groupby("symbol")
    hh = g.high.transform(lambda s: s.rolling(WPR_N).max())
    ll = g.low.transform(lambda s: s.rolling(WPR_N).min())
    d["wpr"] = -100.0 * (hh - d.close) / (hh - ll).replace(0, np.nan)
    prev = d[["symbol", "time", "wpr"]].copy()
    prev["time"] += B.DAY                                            # D-1's reading, known at D's open
    day = d[["symbol", "time", "open", "close"]].rename(columns={"open": "d_open", "close": "d_close"})
    return prev.merge(day, on=["symbol", "time"], how="right")


def main() -> None:
    if not (B.OUT / "trades.parquet").exists():
        B.cmd_fetch()
    tr = pd.read_parquet(B.OUT / "trades.parquet")
    tr = tr[tr.dist <= B.MAX_DIST].merge(daily_readings(), on=["symbol", "time"], how="left")
    print(f"Binance {'2025-2026 (CONTROL, reported only)' if '--control' in sys.argv else '2021-2024 (JUDGED)'}: "
          f"{len(tr)} rule A trades, %R known for {int(tr.wpr.notna().sum())}")

    # ---- stopped trades that came back (descriptive)
    tr["nostop"] = ((1 - tr.d_close / tr.d_open) - B.FEE - B.FEE * tr.d_close / tr.d_open) / tr.dist
    st = tr[tr.A_how == "stop"]
    print("\nSTOPPED TRADES (rule A): did D's last close come back?")
    print(f"  {'year':6s} {'trades':>6s} {'stopped':>8s} {'back<stop':>9s} {'back<entry':>10s} "
          f"{'R stopped':>9s} {'R no stop':>9s} | {'all: A':>7s} {'all: no stop':>12s}")
    for y in B.YEARS + ["pooled"]:
        a = tr if y == "pooled" else tr[tr.year == y]
        s = st if y == "pooled" else st[st.year == y]
        stop_px = s.d_open * (1 + s.dist)
        print(f"  {y:6s} {len(a):6d} {len(s):4d} {100 * len(s) / len(a):2.0f}% {100 * (s.d_close < stop_px).mean():8.0f}% "
              f"{100 * (s.d_close < s.d_open).mean():9.0f}% {s.A.mean():+9.3f} {s.nostop.mean():+9.3f} | "
              f"{a.A.mean():+7.3f} {a.nostop.mean():+12.3f}")

    # ---- filters (judged on 2021-2024)
    filters = {"W  %R(14) >= -20": tr.wpr >= WPR_MIN,
               "S  stop <= 20%": tr.dist <= S_MAX,
               "WS both": (tr.wpr >= WPR_MIN) & (tr.dist <= S_MAX)}
    print("\nFILTERS: net R per TAKEN trade (rule A), vs all trades")
    base = {y: tr[tr.year == y].A.mean() for y in B.YEARS}
    print("  " + f"{'all trades':18s}" + "".join(f" {y} {base[y]:+.3f} (n {int((tr.year == y).sum())})" for y in B.YEARS)
          + f" | pooled {tr.A.mean():+.3f} z {z(tr.A):+.2f}")
    need = 3 if len(B.YEARS) == 4 else len(B.YEARS)
    for name, keep in filters.items():
        k = tr[keep.fillna(False)]
        per = {y: k[k.year == y].A for y in B.YEARS}
        pos = sum(v.mean() > 0 for v in per.values())
        beat = sum(per[y].mean() > base[y] for y in B.YEARS)
        zz = z(k.A)
        ok = k.A.mean() > 0 and zz >= Z_BAR and pos >= need and beat >= need
        print(f"  {name:18s}" + "".join(f" {y} {per[y].mean():+.3f} (n {len(per[y])})" for y in B.YEARS)
              + f" | pooled {k.A.mean():+.3f} z {zz:+.2f}, kept {100 * len(k) / len(tr):.0f}%, >0 {pos}/{len(B.YEARS)}, "
              f"beats all {beat}/{len(B.YEARS)} -> {'PASS' if ok else 'FAIL'}"
              + ("" if "--control" not in sys.argv else " (control: not a verdict)"))


if __name__ == "__main__":
    main()
