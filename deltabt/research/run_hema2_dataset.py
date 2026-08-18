"""Build the H-EMA-2 UNCHAINED dataset: every signal becomes a trade.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hema2_dataset

PARALLEL TRACK, NOT A REPLACEMENT. The frozen H-EMA-2 experiment keeps its
one-position-at-a-time semantics and its TRAIN numbers are unchanged. This
recovers the 58.5% of eligible setups the position lock discarded, so the
dataset describes what the mechanisms actually signalled rather than what a
single-position account could have taken.

TRAIN window only. VALID is not constructed here.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.research import hema2
from deltabt.research.hema2_dataset import simulate_unchained
from deltabt.research.run_hema2_train import (MANIFEST, OUT, SEEDS, TRAIN,
                                              arm_signals, build_features, load)
from deltabt.research.stats import trade_design_effect

DS = OUT / "dataset"


def summarise(f: pd.DataFrame, **extra) -> dict:
    if f is None or f.empty:
        return dict(trades=0, **extra)
    de = trade_design_effect(f)
    n = f.r_net.to_numpy("float64"); g = f.r_gross.to_numpy("float64")
    w, l = n[n > 0], n[n <= 0]
    return dict(
        trades=int(len(f)), effective_n=round(de["n_eff"], 1),
        deff=round(de["deff"], 3),
        gross_expectancy=float(g.mean()), net_expectancy=float(n.mean()),
        fee_R=float(f.fee_r.mean()), slippage_R=float(f.slip_r.mean()),
        funding_R=float(f.funding_r.mean()), cost_per_R=float(f.cost_r.mean()),
        win_rate=float((n > 0).mean()),
        profit_factor=round(float(w.sum() / -l.sum()), 3) if l.sum() < 0 else None,
        median_stop_pct=float(f.stop_pct.median() * 100),
        median_hold_min=float(f.bars_held.median()),
        pct_target=float((f.exit_reason == "target").mean() * 100),
        median_concurrency=float(f.concurrency.median()),
        max_concurrency=int(f.concurrency.max()),
        **extra)


def main() -> int:
    DS.mkdir(parents=True, exist_ok=True)
    print("=" * 96)
    print("H-EMA-2 UNCHAINED DATASET -- overlapping positions allowed")
    print(f"  arms {MANIFEST['n_arms']}   TRAIN {pd.Timestamp(TRAIN[0],unit='s'):%Y-%m-%d}"
          f" -> {pd.Timestamp(TRAIN[1],unit='s'):%Y-%m-%d}   VALID not constructed")
    print("=" * 96)
    data = load()
    feat = build_features(data)
    print("features built\n")

    rows, chunks = [], []
    t0 = time.time()
    n_arms = len(MANIFEST["arms"])
    for i, arm in enumerate(MANIFEST["arms"], 1):
        frames, cb_frames = [], []
        for sym, d in data.items():
            F, rl, rs, lo, sh, w = arm_signals(feat, arm, sym)
            lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, d["t1"], len(d["t1"]))
            f = simulate_unchained(d, lo1, sh1, sl1, ss1, window=TRAIN,
                                   label=arm["arm_id"])
            if len(f):
                frames.append(f)
            # the width-matched control, evaluated the same unchained way
            chained = hema2.simulate(d, lo1, sh1, sl1, ss1, window=TRAIN,
                                     label=arm["arm_id"]).to_frame()
            if len(chained):
                for seed in SEEDS:
                    b = hema2.control_cb(chained.stop_pct.to_numpy(), F, d,
                                         TRAIN, w, seed)
                    cb = simulate_unchained(d, b[0], b[1], b[2], b[3],
                                            window=TRAIN, label="C_b")
                    if len(cb):
                        cb_frames.append(cb.assign(seed=seed))
        af = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        cf = pd.concat(cb_frames, ignore_index=True) if cb_frames else pd.DataFrame()
        row = summarise(af, candidate_id=arm["arm_id"], mechanism=arm["mechanism"],
                        exec_tf=arm["exec_tf"], pair=f"{arm['fast']}/{arm['slow']}")
        if len(cf):
            per = cf.groupby("seed").agg(g=("r_gross", "mean"), n=("r_net", "mean")).reset_index()
            row.update(cb_gross=float(per.g.mean()), cb_net=float(per.n.mean()),
                       cb_trades=int(len(cf)), cb_seed_sd=float(per.n.std(ddof=1)))
            if row.get("trades"):
                row["excess_gross_cb"] = row["gross_expectancy"] - row["cb_gross"]
                row["excess_net_cb"] = row["net_expectancy"] - row["cb_net"]
        rows.append(row)
        if len(af):
            keep = af.copy()
            keep["mechanism"] = arm["mechanism"]
            keep["exec_tf"] = arm["exec_tf"]
            chunks.append(keep)
        if i % 10 == 0 or i == n_arms:
            el = time.time() - t0
            print(f"  [{i:>3}/{n_arms}] {arm['arm_id']:<26} n={row.get('trades',0):>7,} "
                  f"eff_n={row.get('effective_n',0):>8,.0f} "
                  f"gross={row.get('gross_expectancy',0):+.4f} "
                  f"exc_g={(row.get('excess_gross_cb') or 0):+.4f} "
                  f"({el/60:.1f}m, eta {el/i*(n_arms-i)/60:.1f}m)", flush=True)

    s = pd.DataFrame(rows)
    s.to_csv(DS / "dataset_summary.csv", index=False)
    all_tr = pd.concat(chunks, ignore_index=True)
    all_tr.to_parquet(DS / "trades_train_unchained.parquet", index=False)
    json.dump(rows, open(DS / "dataset_summary.json", "w"), indent=2, default=str)
    print(f"\ntotal unchained trades: {len(all_tr):,}")
    print(f"elapsed {(time.time()-t0)/60:.1f}m -> {DS}")
    print("VALID NOT COMPUTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
