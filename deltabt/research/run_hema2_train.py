"""H-EMA-2 TRAIN. Frozen manifest only. VALID IS NOT CONSTRUCTED IN THIS FILE.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hema2_train

The VALID boundary is deliberately absent from this module: it reads only
config["train"]. VALID cannot be computed here even by accident, which is why
TRAIN and VALID are separate files rather than a flag on one runner.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hema2
from deltabt.research import hema2_journal as J
from deltabt.research.stats import block_bootstrap_mean, trade_design_effect

OUT = Path("out/hema2")
MANIFEST = json.loads((OUT / "candidates.json").read_text())
CONFIG = json.loads((OUT / "config.json").read_text())
SYMBOLS = CONFIG["universe"]
STUDY = CONFIG["study_start"]
TRAIN = tuple(CONFIG["train"])
SEEDS = MANIFEST["control_seeds"]
REGIME_PAIR = tuple(MANIFEST["regime_pair"])
REGIME_MAP = {5: 60, 15: 240, 60: 1440}


def warmup_for(arm) -> int:
    return int(arm["slow"]) + hema2.VOL_BASELINE + hema2.ATR_PERIOD + hema2.SLOPE_LOOKBACK


def load() -> dict:
    store, cat = CandleStore(), ProductCatalog()
    data = {}
    for s in SYMBOLS:
        ltp = store.read(s, "ltp", "1m")
        if ltp.empty:
            raise SystemExit(f"no cached candles for {s}; run `deltabt.cli fetch`")
        ltp = ltp[(ltp.time >= STUDY) & (ltp.time <= CONFIG["data_end"])].reset_index(drop=True)
        mark = store.read(s, "mark", "1m")
        t1 = ltp["time"].to_numpy("int64")
        h = ltp["high"].to_numpy("float64"); l = ltp["low"].to_numpy("float64")
        if mark is not None and not mark.empty:
            mk = mark.set_index("time").reindex(t1)
            mh = mk["high"].to_numpy("float64"); ml = mk["low"].to_numpy("float64")
            bad = ~np.isfinite(mh) | ~np.isfinite(ml)
            mh = np.where(bad, h, mh); ml = np.where(bad, l, ml)
        else:
            mh, ml = h, l
        data[s] = dict(df=ltp, t1=t1, o=ltp["open"].to_numpy("float64"), h=h, l=l,
                       c=ltp["close"].to_numpy("float64"), mh=mh, ml=ml,
                       funding=store.read(s, "funding", "1h"),
                       costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
                       tradable=tradable_mask(ltp).astype(np.bool_))
        print(f"  {s:8} {len(ltp):>9,} 1m bars")
    return data


def metrics(frame, *, boot: bool) -> dict:
    if frame is None or frame.empty:
        return dict(trades=0)
    e = J.economics(frame)
    de = trade_design_effect(frame)
    e["effective_n"] = round(de["n_eff"], 1)
    g = frame.r_gross.to_numpy("float64"); n = frame.r_net.to_numpy("float64")
    w, lo = n[n > 0], n[n <= 0]
    e["profit_factor"] = round(float(w.sum() / -lo.sum()), 3) if lo.sum() < 0 else None
    eq = np.cumsum(n)
    e["max_dd_R"] = round(float(np.max(np.maximum.accumulate(eq) - eq)), 2)
    if boot and len(frame) >= 20:
        sq = np.sqrt(de["deff"])
        bg = block_bootstrap_mean(g, mean_block=6.0, n_boot=1000, seed=12)
        bn = block_bootstrap_mean(n, mean_block=6.0, n_boot=1000, seed=11)
        e["t_gross"] = round(float(bg["t"] / sq), 3) if np.isfinite(bg["t"]) else None
        e["t_net"] = round(float(bn["t"] / sq), 3) if np.isfinite(bn["t"]) else None
        e["net_ci_low"], e["net_ci_high"] = round(bn["ci_low"], 4), round(bn["ci_high"], 4)
    return e


def build_features(data: dict) -> dict:
    F, X, REG = {}, {}, {}
    tfs = MANIFEST["exec_timeframes_minutes"]
    for s, d in data.items():
        for tf in tfs:
            F[(s, tf)] = hema2.build_tf(d["df"], tf)
            for pair in MANIFEST["ema_pairs"]:
                X[(s, tf, tuple(pair))] = hema2.crossover_events(
                    F[(s, tf)]["close"], pair[0], pair[1])
        for tf in tfs:
            rtf = REGIME_MAP[tf]
            R = hema2.build_tf(d["df"], rtf)
            rf = hema2.project_regime(R["time"], rtf,
                                      hema2.ema(R["close"], REGIME_PAIR[0]),
                                      F[(s, tf)]["time"], tf)
            rs = hema2.project_regime(R["time"], rtf,
                                      hema2.ema(R["close"], REGIME_PAIR[1]),
                                      F[(s, tf)]["time"], tf)
            REG[(s, tf)] = (rf > rs, rf < rs)
    return dict(F=F, X=X, REG=REG)


def arm_signals(feat, arm, sym):
    tf, pair = arm["exec_tf"], (arm["fast"], arm["slow"])
    F = feat["F"][(sym, tf)]
    X = feat["X"][(sym, tf, pair)]
    reg = feat["REG"][(sym, tf)] if arm["mechanism"] == "M5" else None
    raw_lo, raw_sh = hema2.mech_signals(F, X, arm["mechanism"], arm["params"], regime=reg)
    w = warmup_for(arm)
    lo, sh = raw_lo.copy(), raw_sh.copy()
    lo[:w] = False; sh[:w] = False
    return F, raw_lo, raw_sh, lo, sh, w


def run_arm(feat, data, arm, window, *, with_controls=True):
    frames, funnels = [], []
    ctrl = {"C_a": [], "C_b": []}
    for sym, d in data.items():
        F, raw_lo, raw_sh, lo, sh, w = arm_signals(feat, arm, sym)
        lo1, sh1, sl1, ss1 = hema2.project(lo, sh, F, d["t1"], len(d["t1"]))
        res = hema2.simulate(d, lo1, sh1, sl1, ss1, window=window, label=arm["arm_id"])
        f = res.to_frame()
        fn = J.funnel(raw_lo, raw_sh, F, d, window, w, res, len(f))
        fn.update(symbol=sym, candidate_id=arm["arm_id"])
        fn["reconciled"] = J.reconcile(f, fn)["ok"]
        funnels.append(fn)
        if len(f):
            frames.append(f)
        if not with_controls:
            continue
        for seed in SEEDS:
            a_lo, a_sh, a_sl, a_ss = hema2.control_ca(lo1, sh1, sl1, ss1, seed)
            ca = hema2.simulate(d, a_lo, a_sh, a_sl, a_ss, window=window, label="C_a").to_frame()
            if len(ca):
                ctrl["C_a"].append(ca.assign(seed=seed))
            if len(f):
                b = hema2.control_cb(f.stop_pct.to_numpy(), F, d, window, w, seed)
                cb = hema2.simulate(d, b[0], b[1], b[2], b[3], window=window, label="C_b").to_frame()
                if len(cb):
                    ctrl["C_b"].append(cb.assign(seed=seed))
    out = dict(arm=arm, funnels=funnels,
               frame=pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())
    for k, lst in ctrl.items():
        out[k] = pd.concat(lst, ignore_index=True) if lst else pd.DataFrame()
    return out


def seed_stats(cf) -> dict:
    if cf is None or cf.empty:
        return dict(trades=0, net=None, gross=None, seed_sd=None)
    per = cf.groupby("seed").agg(trades=("r_net", "size"), net=("r_net", "mean"),
                                 gross=("r_gross", "mean"),
                                 stop=("stop_pct", "median")).reset_index()
    return dict(trades=int(len(cf)), net=float(per.net.mean()), gross=float(per.gross.mean()),
                net_median=float(per.net.median()),
                seed_sd=float(per.net.std(ddof=1)) if len(per) > 1 else 0.0,
                net_min=float(per.net.min()), net_max=float(per.net.max()),
                median_stop_pct=float(per.stop.median() * 100),
                per_seed=per.to_dict("records"))


def main() -> int:
    (OUT / "summaries").mkdir(parents=True, exist_ok=True)
    (OUT / "trades").mkdir(parents=True, exist_ok=True)
    print("=" * 100)
    print("H-EMA-2 TRAIN -- frozen manifest; VALID not constructed in this module")
    print(f"  arms   {MANIFEST['n_arms']}   prereg sha256 {MANIFEST['preregistration_sha256'][:16]}...")
    print(f"  TRAIN  {pd.Timestamp(TRAIN[0],unit='s')} -> {pd.Timestamp(TRAIN[1],unit='s')}")
    print(f"  seeds  {SEEDS}")
    print("=" * 100)
    data = load()
    print("building features (once per symbol/timeframe/pair)...")
    feat = build_features(data)
    print("  done\n")

    rows, funnel_rows, sym_rows, ctrl_rows = [], [], [], []
    t0 = time.time()
    n_arms = len(MANIFEST["arms"])
    for i, arm in enumerate(MANIFEST["arms"], 1):
        r = run_arm(feat, data, arm, TRAIN)
        f = r["frame"]
        m = metrics(f, boot=True)
        ca, cb = seed_stats(r["C_a"]), seed_stats(r["C_b"])
        row = dict(candidate_id=arm["arm_id"], mechanism=arm["mechanism"],
                   exec_tf=arm["exec_tf"], pair=f"{arm['fast']}/{arm['slow']}",
                   variant=json.dumps(arm["params"], sort_keys=True), **m)
        ne = m.get("net_expectancy")
        row.update(ca_net=ca["net"], cb_net=cb["net"], cb_seed_sd=cb["seed_sd"],
                   ca_trades=ca["trades"], cb_trades=cb["trades"],
                   cb_median_stop_pct=cb.get("median_stop_pct"),
                   excess_vs_ca=(ne - ca["net"]) if (ne is not None and ca["net"] is not None) else None,
                   excess_vs_cb=(ne - cb["net"]) if (ne is not None and cb["net"] is not None) else None)
        row.update({k: int(sum(x[k] for x in r["funnels"])) for k in
                    ("setups_detected", "rejected_warmup", "rejected_stop_invalid",
                     "rejected_no_entry_bar", "rejected_outside_split",
                     "eligible_setups", "skipped_stop", "skipped_size",
                     "rejected_position_open", "trades_entered")})
        row["reconciled"] = all(x["reconciled"] for x in r["funnels"])
        rows.append(row)
        funnel_rows.extend(r["funnels"])
        for nm, st in (("C_a", ca), ("C_b", cb)):
            ctrl_rows.append(dict(candidate_id=arm["arm_id"], control=nm,
                                  **{k: v for k, v in st.items() if k != "per_seed"},
                                  per_seed=json.dumps(st.get("per_seed", []), default=str)))
        if len(f):
            for s, g in f.groupby("symbol"):
                sym_rows.append(dict(candidate_id=arm["arm_id"], symbol=s,
                                     mechanism=arm["mechanism"], exec_tf=arm["exec_tf"],
                                     **metrics(g, boot=False)))
        if i % 5 == 0 or i == n_arms:
            el = time.time() - t0
            print(f"  [{i:>3}/{n_arms}] {arm['arm_id']:<26} n={row.get('trades',0):>6,} "
                  f"net={(row.get('net_expectancy') or 0):+.4f} "
                  f"exc_cb={(row.get('excess_vs_cb') or 0):+.4f} "
                  f"({el/60:.1f}m elapsed, eta {el/i*(n_arms-i)/60:.1f}m)", flush=True)

    arms_df = pd.DataFrame(rows)
    arms_df.to_csv(OUT / "summaries" / "arm_summary.csv", index=False)
    pd.DataFrame(funnel_rows).to_csv(OUT / "summaries" / "setup_funnel.csv", index=False)
    pd.DataFrame(sym_rows).to_csv(OUT / "summaries" / "symbol_summary.csv", index=False)
    pd.DataFrame(ctrl_rows).to_csv(OUT / "summaries" / "control_seeds.csv", index=False)
    json.dump(rows, open(OUT / "train_results.json", "w"), indent=2, default=str)
    json.dump(ctrl_rows, open(OUT / "control_results.json", "w"), indent=2, default=str)

    bad = arms_df[~arms_df.reconciled.astype(bool)]
    print(f"\nreconciliation: {len(arms_df)-len(bad)}/{len(arms_df)} arms OK")
    if len(bad):
        print("  FAILED:", bad.candidate_id.tolist()[:10])
    print(f"total elapsed {(time.time()-t0)/60:.1f}m")
    print(f"written to {OUT}. VALID NOT COMPUTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
