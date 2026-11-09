"""More picks a day and a wider target (owner 2026-11-09: "test option 3 and 4, give me numbers").

Option 3: take-profit 0.3x the stop instead of QUICK's 0.2x (rows from scripts/fade_target_compare.py).
Option 4: short the top 3 or top 4 previous-day gainers instead of the top 2.
Same picks, stop, fills and fees as scripts/fade_target_compare.py; ranks 3-4 are fetched here.
Account: $1,200 at 3% risk, compounding, trades in time order; only stops a 1x account can take (<= 32.5%).

Decision rules, written before the run:
  option 3: 0.3x replaces 0.2x only if it is above 0.2x in BOTH years and the pooled paired difference
            is >= 2 standard errors (already measured: 2025 -0.001R, pooled z +1.29 -> NO; repeated here).
  option 4: rank 3 (then rank 4) is added only if ITS OWN trades under QUICK have mean R > 0 in BOTH years
            and pooled mean >= 2 standard errors above zero.

    PYTHONPATH=. .venv/bin/python scripts/fade_more_picks.py
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_target_compare as T  # noqa: E402
import gainer_fade_2025_lab as L  # noqa: E402

RISK, START = 0.03, 1200.0
COLS = ("0.2x QUICK", "0.3x")


def account(v: pd.Series) -> tuple[float, float]:
    eq, peak, dd = START, START, 0.0
    for x in v:
        eq += RISK * eq * x
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq, dd


def z(v: pd.Series) -> float:
    return v.mean() / (v.std() / np.sqrt(len(v)))


def main() -> None:
    cache = L.OUT / "fade_more_picks_rows.parquet"
    if cache.exists():
        res = pd.read_parquet(cache)
    else:
        p = L.panel()
        ranked = (p[p.eligible & np.isfinite(p.atrp) & (p.atrp > 0)]
                  .sort_values(["time", "prev_change"], ascending=[True, False]))
        ranked["rank"] = ranked.groupby("time").cumcount() + 1
        top4 = ranked[ranked["rank"] <= 4]
        old = pd.read_parquet(L.OUT / "fade_target_compare_rows.parquet")
        old = old.merge(top4[["symbol", "time", "rank"]], on=["symbol", "time"])
        new = top4[top4["rank"] >= 3]
        print(f"{len(new)} rank-3/4 pick-days to fetch ...", flush=True)
        rows = []
        with ThreadPoolExecutor(3) as ex:
            for k, ((rec, err), rk) in enumerate(zip(ex.map(T.one, new.itertuples()), new["rank"]), 1):
                if rec:
                    rows.append(dict(rec, rank=rk))
                if k % 200 == 0:
                    print(f"  {k}/{len(new)}", flush=True)
        res = pd.concat([old, pd.DataFrame(rows)], ignore_index=True).sort_values("time")
        res.to_parquet(cache, index=False)
    res = res[res.dist <= T.MAX_DIST]
    days = {w: res[res.window == w].time.nunique() for w in ("2025", "2026")}

    print("\n=== by rank, exit QUICK (0.2x), mean R per trade ===")
    for rk in (1, 2, 3, 4):
        d = res[res["rank"] == rk]["0.2x QUICK"]
        per = {w: res[(res["rank"] == rk) & (res.window == w)]["0.2x QUICK"] for w in ("2025", "2026")}
        print(f"  rank {rk}: 2025 {per['2025'].mean():+.3f} (n {len(per['2025'])}), 2026 {per['2026'].mean():+.3f} "
              f"(n {len(per['2026'])}), pooled {d.mean():+.3f} z {z(d):+.2f}")
    for rk in (3, 4):
        d = res[res["rank"] == rk]["0.2x QUICK"]
        both = all(res[(res["rank"] == rk) & (res.window == w)]["0.2x QUICK"].mean() > 0 for w in ("2025", "2026"))
        print(f"  RULE option 4, rank {rk}: add -> {'YES' if both and z(d) >= 2 else 'NO'}")

    print("\n=== account: $1,200 at 3% risk ===")
    print(f"  {'picks':6s} {'exit':11s} {'year':5s} {'trades/mo':>9s} {'win':>5s} {'mean R':>7s} {'$/mo':>6s} "
          f"{'per stop':>8s} {'per win':>7s} {'end $':>7s} {'max drop':>8s}")
    for top in (2, 3, 4):
        for col in COLS:
            for w in ("2025", "2026"):
                d = res[(res["rank"] <= top) & (res.window == w)][col]
                end, dd = account(d)
                months = days[w] / 30.4
                print(f"  top {top:<2d} {col:11s} {w:5s} {len(d) / months:9.1f} {100 * (d > 0).mean():4.0f}% "
                      f"{d.mean():+7.3f} {(end - START) / months:6.0f} {-RISK * START * 1.0:8.0f} "
                      f"{RISK * START * d[d > 0].mean():+7.1f} {end:7,.0f} {100 * dd:7.0f}%")

    two = res[res["rank"] <= 2]
    x = two["0.3x"] - two["0.2x QUICK"]
    by = {w: x[two.window == w].mean() for w in ("2025", "2026")}
    print(f"\n  RULE option 3: 0.3x minus 0.2x: 2025 {by['2025']:+.3f}R, 2026 {by['2026']:+.3f}R, pooled z {z(x):+.2f}"
          f" -> {'YES' if min(by.values()) > 0 and z(x) >= 2 else 'NO'}")


if __name__ == "__main__":
    main()
