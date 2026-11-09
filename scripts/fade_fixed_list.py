"""A fixed list of coins that faded well before (owner 2026-11-09: "yes a fixed list").

Rows: scripts/fade_more_picks.py (top-4 previous-day gainers, each simulated under QUICK 0.2x and rule A;
stops a 1x account can take only, <= 32.5%). Same fills and fees as there. Coins listed today only.

Rule, written before the run:
  LIST    = coins picked (rank <= 4) at least MIN_PICKS times in the BUILD period with mean QUICK R > 0.
  TEST    = in the later period, picks (rank <= 4) of LIST coins vs all other picks.
  PASS    = LIST picks' mean QUICK R > 0 AND above the others by z >= 2 (Welch).
  Check 1: build 2025, test 2026.  Check 2: build Jan-May 2026, test Jun-Oct 2026.
  Rule A numbers are reported beside QUICK; the verdict is on QUICK.

    PYTHONPATH=. .venv/bin/python scripts/fade_fixed_list.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as L  # noqa: E402

MIN_PICKS = 5
Q, A = "0.2x QUICK", "A (24 h)"


def welch(a: pd.Series, b: pd.Series) -> float:
    return (a.mean() - b.mean()) / np.sqrt(a.var() / len(a) + b.var() / len(b))


def check(res: pd.DataFrame, build: pd.Series, test: pd.Series, label: str) -> None:
    b = res[build]
    g = b.groupby("symbol")[Q].agg(["count", "mean"])
    lst = sorted(g[(g["count"] >= MIN_PICKS) & (g["mean"] > 0)].index)
    t = res[test]
    on, off = t[t.symbol.isin(lst)], t[~t.symbol.isin(lst)]
    print(f"\n=== {label} ===")
    print(f"list ({len(lst)} coins of {b.symbol.nunique()} picked in the build period): {', '.join(lst)}")
    print(f"  build period, list coins: {int(g.loc[lst, 'count'].sum())} picks, mean QUICK {b[b.symbol.isin(lst)][Q].mean():+.3f}R")
    months = t.time.nunique() / 30.4
    for name, col in (("QUICK", Q), ("rule A", A)):
        print(f"  test {name:6s}: list {len(on):4d} picks ({len(on) / months:4.1f}/mo) mean {on[col].mean():+.3f}R win "
              f"{100 * (on[col] > 0).mean():.0f}% | others {len(off):4d} mean {off[col].mean():+.3f}R | "
              f"diff z {welch(on[col], off[col]):+.2f} | $1,500 at 5% fixed: {75 * on[col].sum() / months:+.0f} $/mo")
    hits = on.groupby("symbol")[Q].agg(["count", "mean"]).sort_values("count", ascending=False)
    print("  list coins in the test period (picks, mean QUICK R): "
          + ", ".join(f"{s} {int(r['count'])} {r['mean']:+.2f}" for s, r in hits.iterrows()))
    passed = len(on) > 1 and on[Q].mean() > 0 and welch(on[Q], off[Q]) >= 2
    print(f"  RULE: {'PASS' if passed else 'FAIL'}")


def main() -> None:
    res = pd.read_parquet(L.OUT / "fade_more_picks_rows.parquet")
    res = res[res.dist <= 0.325].copy()
    ts = pd.to_datetime(res.time, unit="s")
    check(res, res.window == "2025", res.window == "2026", "Check 1: list from 2025, tested on 2026")
    check(res, (res.window == "2026") & (ts < "2026-06-01"), (res.window == "2026") & (ts >= "2026-06-01"),
          "Check 2: list from Jan-May 2026, tested on Jun-Oct 2026")


if __name__ == "__main__":
    main()
