"""H-COST-1 Stage A + B on VALID. Blind, run ONCE against the frozen manifest."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.research import hcost1, hema2
from deltabt.research.run_hema2_train import build_features, load

OUT = Path("out/hcost1")
MAN = json.loads((OUT / "candidates.json").read_text())
TRAIN = tuple(MAN["valid"])   # VALID window; blind confirmation pass
WIDTHS = MAN["stop_widths_primary"] + MAN["stop_widths_diagnostic"]
SCEN = MAN["cost_scenarios"]
K_MAX = max(MAN["stage_b"]["k"])


def main() -> int:
    (OUT / "summaries").mkdir(parents=True, exist_ok=True)
    print("=" * 104)
    print("H-COST-1 VALID STAGE A -- timeframe x stop width x volatility, baseline cost, k=0.50")
    print(f"  prereg sha256 {MAN['preregistration_sha256'][:16]}...   "
          f"primary widths <=5%, diagnostic >5% OUT-OF-MODEL")
    print("=" * 104)
    t0 = time.time()
    bets_all = pd.read_parquet("out/hema3/bets_valid.parquet")
    sig, cmeta = hcost1.executable_signal(bets_all)
    print(f"executable signal: {cmeta['rows_in']:,} -> {len(sig):,} rows; "
          f"{cmeta['conflicting_bars']:,} conflicting bars "
          f"({cmeta['conflicting_pct']}%), {cmeta['dropped_rows']:,} rows dropped\n")
    json.dump(cmeta, open(OUT / "summaries" / "conflict_report_valid.json", "w"), indent=2)

    data = load()
    feat = build_features(data)

    # volatility regime thresholds: TRAIN only, frozen here
    vol, thr = {}, {}
    for tf in MAN["exec_timeframes"]:
        pool = []
        for s in data:
            F = feat["F"][(s, tf)]
            v = F["atr"] / F["close"]
            vol[(s, tf)] = v
            m = (F["time"] >= TRAIN[0]) & (F["time"] < TRAIN[1])
            pool.append(v[m])
        # S 7: thresholds are TRAIN-derived and frozen. VALID must not set them.
        thr[tf] = tuple(json.loads(
            (OUT / 'summaries' / 'volatility_thresholds.json').read_text())[str(tf)])
        print(f"  vol thresholds {tf:>3}m: P33 {thr[tf][0]*100:.4f}%  P67 {thr[tf][1]*100:.4f}%")
    json.dump({str(k): v for k, v in thr.items()},
              open(OUT / "summaries" / "volatility_thresholds_valid_UNUSED.json", "w"), indent=2)

    rows_a, rows_b = [], []
    for tf in MAN["exec_timeframes"]:
        for width in WIDTHS:
            parts = []
            for s, d in data.items():
                sub = sig[(sig.symbol == s) & (sig.exec_tf == tf)]
                if sub.empty:
                    continue
                n_keep = int(np.searchsorted(d["t1"], TRAIN[1], side="right"))
                ei = sub.entry_idx.to_numpy("int64")
                ent, res = hcost1.synthetic_barriers(d, ei, width, K_MAX, n_keep)
                p = sub[["symbol", "exec_tf", "bar", "side", "entry_time"]].copy()
                p["entry_px"] = ent
                for k2, v2 in res.items():
                    p[k2] = v2
                p["regime"] = hcost1.regime_of(vol[(s, tf)][sub.bar.to_numpy()], *thr[tf])
                parts.append(p)
            if not parts:
                continue
            cell = pd.concat(parts, ignore_index=True)
            taker = data["BTCUSD"]["costs"].taker_fee
            band = "diagnostic" if width > 0.05 else "primary"
            for reg in ("ALL", "LOW", "NORMAL", "HIGH"):
                sub = cell if reg == "ALL" else cell[cell.regime == reg]
                r = hcost1.cell_result(sub, 0.5, width, slippage_bps=2.0, taker=taker,
                                       exec_tf=tf, regime=reg, band=band, stage="A")
                rows_a.append(r)
            for s in MAN["universe"]:
                r = hcost1.cell_result(cell[cell.symbol == s], 0.5, width,
                                       slippage_bps=2.0, taker=taker, exec_tf=tf,
                                       regime="ALL", symbol=s, band=band, stage="A-symbol")
                rows_a.append(r)
            # Stage B over every cell meeting the sample rule -- not selected on performance
            if len(cell) >= MAN["min_n"]:
                for k in MAN["stage_b"]["k"]:
                    for nm, sc in SCEN.items():
                        r = hcost1.cell_result(cell, k, width, slippage_bps=sc["slippage_bps"],
                                               taker=taker, maker_exit=sc["exit"] == "maker",
                                               exec_tf=tf, regime="ALL", band=band,
                                               stage="B", scenario=nm)
                        rows_b.append(r)
            print(f"  tf {tf:>3}m width {width*100:>5.2f}%  n {len(cell):>7,}  "
                  f"[{band}]", flush=True)

    a = pd.DataFrame(rows_a); b = pd.DataFrame(rows_b)
    for df, nm in ((a, "feasibility_map"), (b, "cost_sensitivity")):
        df.to_csv(OUT / "summaries" / f"{nm}_valid.csv", index=False)
    json.dump(dict(stage_a=rows_a, stage_b=rows_b),
              open(OUT / "validation_results.json", "w"), indent=2, default=str)

    # gates need the low/high slippage net for the same (tf,width,k=0.5)
    prim = a[(a.stage == "A") & (a.regime == "ALL") & (a.n > 0)].copy()
    look = {(r.exec_tf, r.stop_width, r.scenario): r.signal_net_R
            for r in b.itertuples() if r.k == 0.5}
    prim["net_low_slip"] = [look.get((r.exec_tf, r.stop_width, "B_low_slip"), np.nan)
                            for r in prim.itertuples()]
    prim["net_high_slip"] = [look.get((r.exec_tf, r.stop_width, "C_high_slip"), np.nan)
                             for r in prim.itertuples()]
    prim["gate"] = [hcost1.gate(r._asdict(), r.net_low_slip, r.net_high_slip)
                    for r in prim.itertuples()]
    prim.to_csv(OUT / "summaries" / "stop_width_summary_valid.csv", index=False)

    print("\n" + "=" * 104)
    print("STAGE A -- pooled, k=0.50, baseline cost")
    print("=" * 104)
    print(f"{'tf':>5}{'width':>8}{'n':>9}{'excess_R':>11}{'t':>7}{'cost/R':>9}"
          f"{'sig_net':>10}{'edge/cost':>11}{'x needed':>10}  gate")
    for r in prim.sort_values(["exec_tf", "stop_width"]).itertuples():
        print(f"{r.exec_tf:>5}{r.stop_width*100:>7.2f}%{r.n:>9,}{r.excess_gross_R:>+11.4f}"
              f"{r.t:>+7.2f}{r.cost_R:>9.4f}{r.signal_net_R:>+10.4f}"
              f"{(r.edge_to_cost or 0):>11.2f}"
              f"{(r.required_multiple or float('nan')):>10.1f}  {r.gate}")
    print(f"\nelapsed {(time.time()-t0)/60:.1f}m -> {OUT}")
    print("TEST NOT COMPUTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
