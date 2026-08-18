"""H-EMA-3 VALID. Blind, run ONCE against the frozen estimator.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hema3_valid
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.research import hema2, hema3
from deltabt.research.run_hema2_train import (MANIFEST, arm_signals,
                                              build_features, load)

OUT = Path("out/hema3")
CFG = json.loads((OUT / "config.json").read_text())
TRAIN = tuple(CFG["valid"])   # VALID window; this file is the blind run
K_MAX = max(hema3.BARRIERS)
ROUND_TRIP = CFG["round_trip_bps"] / 10_000.0


def collect(data, feat):
    """Barrier outcomes for every bar any arm signals, plus arm attribution."""
    bets, arm_map = {}, {}
    tfs = MANIFEST["exec_timeframes_minutes"]
    for sym, d in data.items():
        for tf in tfs:
            need = np.zeros(len(feat["F"][(sym, tf)]["time"]), bool)
            for arm in MANIFEST["arms"]:
                if arm["exec_tf"] != tf:
                    continue
                _, _, _, lo, sh, _ = arm_signals(feat, arm, sym)
                need |= (lo | sh)
                arm_map.setdefault(arm["arm_id"], {})[(sym, tf)] = (
                    np.flatnonzero(lo), np.flatnonzero(sh))
            bars = np.flatnonzero(need)
            if bars.size == 0:
                continue
            b = hema3.build_bets(d, feat["F"][(sym, tf)], bars, TRAIN, K_MAX)
            if len(b):
                bets[(sym, tf)] = b.set_index("bar")
            print(f"  {sym:8} {tf:>3}m  bars signalled {bars.size:>7,}"
                  f"  bets built {len(b):>7,}", flush=True)
    return bets, arm_map


def assemble(bets, arm_map, arm_ids=None):
    """(bar, side) rows for the given arms, joined to their barrier outcomes."""
    rows = []
    for aid, per in arm_map.items():
        if arm_ids is not None and aid not in arm_ids:
            continue
        for (sym, tf), (lo_bars, sh_bars) in per.items():
            b = bets.get((sym, tf))
            if b is None:
                continue
            for side, arr in ((1, lo_bars), (-1, sh_bars)):
                take = b.index.intersection(arr)
                if not len(take):
                    continue
                sub = b.loc[take].reset_index()
                sub["side"] = side
                sub["arm_id"] = aid
                rows.append(sub)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def dedup(df):
    """S 8: one bet per (symbol, exec_tf, bar, side), however many arms fired."""
    return df.drop_duplicates(subset=["symbol", "exec_tf", "bar", "side"])


def cost_floor(df):
    """Round-trip cost expressed in R, from the bets' own stop widths."""
    if df.empty:
        return None
    sp = np.where(df.side > 0, df.stop_pct_L, df.stop_pct_S)
    return float(ROUND_TRIP / np.median(sp))


def report(df, label, **extra):
    out = []
    cf = cost_floor(df)
    for k in hema3.BARRIERS:
        r = hema3.paired_statistic(df, k)
        r.update(label=label, cost_floor_R=cf, **extra)
        if r.get("n"):
            r["beats_cost"] = bool(r["excess_gross_R"] > (cf or np.inf))
            r["multiple_of_cost"] = r["excess_gross_R"] / cf if cf else None
        out.append(r)
    return out


def main() -> int:
    print("=" * 100)
    print("H-EMA-3 VALID -- paired mirror-direction barrier test")
    print(f"  prereg sha256 {CFG['preregistration_sha256'][:16]}...   "
          f"barriers {list(hema3.BARRIERS)}")
    print(f"  TRAIN {pd.Timestamp(TRAIN[0],unit='s'):%Y-%m-%d} -> "
          f"{pd.Timestamp(TRAIN[1],unit='s'):%Y-%m-%d}   blind run")
    print("=" * 100)
    t0 = time.time()
    data = load()
    feat = build_features(data)
    print("\nbuilding barrier outcomes...")
    bets, arm_map = collect(data, feat)
    print(f"  done in {(time.time()-t0)/60:.1f}m\n")

    allb = assemble(bets, arm_map)
    pooled = dedup(allb)
    print(f"bets: {len(allb):,} arm-rows -> {len(pooled):,} unique "
          f"(symbol, tf, bar, side)\n")

    rows = report(pooled, "POOLED")
    print("=" * 100)
    print("PRIMARY -- pooled, deduplicated, cluster-robust on symbol-day")
    print("=" * 100)
    print(f"{'k':>5}{'n':>10}{'clusters':>10}{'excess_R':>11}{'se':>9}"
          f"{'t':>8}{'95% CI':>22}{'cost/R':>9}{'x cost':>8}")
    for r in rows:
        if not r.get("n"):
            continue
        print(f"{r['k']:>5}{r['n']:>10,}{r['clusters']:>10,}"
              f"{r['excess_gross_R']:>+11.4f}{r['se_gross_R']:>9.4f}"
              f"{r['t']:>+8.2f}"
              f"   [{r['ci_low_R']:+.4f},{r['ci_high_R']:+.4f}]"
              f"{r['cost_floor_R']:>9.3f}{r['multiple_of_cost']:>8.2f}")

    for tf in MANIFEST["exec_timeframes_minutes"]:
        rows += report(pooled[pooled.exec_tf == tf], f"tf={tf}m", exec_tf=tf)
    for sym in CFG["universe"]:
        rows += report(pooled[pooled.symbol == sym], f"symbol={sym}", symbol=sym)
    mid = (TRAIN[0] + TRAIN[1]) // 2
    rows += report(pooled[pooled.entry_time < mid], "VALID-H1", half="H1")
    rows += report(pooled[pooled.entry_time >= mid], "VALID-H2", half="H2")

    mech_of = {a["arm_id"]: a["mechanism"] for a in MANIFEST["arms"]}
    allb["mechanism"] = allb.arm_id.map(mech_of)
    for m in sorted(set(mech_of.values())):
        rows += report(dedup(allb[allb.mechanism == m]), f"mech={m}", mechanism=m)

    capped = pooled[np.where(pooled.side > 0, pooled.stop_pct_L,
                             pooled.stop_pct_S) <= hema2.MAX_STOP_PCT]
    rows += report(capped, "POOLED-capped-5pct", cap="0.05")

    res = pd.DataFrame(rows)
    res.to_csv(OUT / "valid_results.csv", index=False)
    json.dump(rows, open(OUT / "valid_results.json", "w"), indent=2, default=str)
    pooled.to_parquet(OUT / "bets_valid.parquet", index=False)

    print("\n" + "=" * 100)
    print("BREAKDOWNS (excess gross R, cluster-robust t)")
    print("=" * 100)
    for k in hema3.BARRIERS:
        sub = res[(res.k == k) & res.n.notna() & (res.n > 0)]
        line = " | ".join(f"{r.label}:{r.excess_gross_R:+.4f}(t{r.t:+.1f})"
                          for r in sub.itertuples() if r.label != "POOLED")
        print(f"\nk={k}\n  {line}")
    print(f"\nelapsed {(time.time()-t0)/60:.1f}m -> {OUT}")
    print("TEST NOT COMPUTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
