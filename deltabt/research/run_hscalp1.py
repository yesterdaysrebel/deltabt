"""Execute the pre-registered H-Scalp-1 experiment end to end.

    PYTHONPATH=. python3 -m deltabt.research.run_hscalp1
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hscalp1, nulls
from deltabt.research.hscalp1 import EXEC_MODELS, FILL_MODELS, K_VALUES
from deltabt.research.registry import Experiment, record
from deltabt.research.stats import (
    block_bootstrap_mean,
    bootstrap_diff,
    effective_n,
    max_drawdown,
    sharpe_sortino,
    trade_design_effect,
)
from deltabt.strategy import resample_ohlcv

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
START = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())
OUT = OUT_DIR / "hscalp1"
SIG_BAR = 3.2          # pre-registered significance threshold
MIN_EFF_N = 200        # below this -> NOT INTERPRETABLE

# Train/validation/test, chronological and declared in advance.
SPLITS = {
    "train 2025H1": (START, int(pd.Timestamp("2025-07-01", tz="UTC").timestamp())),
    "valid 2025H2": (int(pd.Timestamp("2025-07-01", tz="UTC").timestamp()),
                     int(pd.Timestamp("2026-01-01", tz="UTC").timestamp())),
    "test  2026":   (int(pd.Timestamp("2026-01-01", tz="UTC").timestamp()), 2**31 - 1),
}


def load() -> dict:
    store, cat = CandleStore(), ProductCatalog()
    data = {}
    for s in SYMBOLS:
        data[s] = dict(
            ltp=store.read(s, "ltp", "1m"),
            mark=store.read(s, "mark", "1m"),
            funding=store.read(s, "funding", "1h"),
            costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
        )
    return data


def pooled_frame(results: dict) -> pd.DataFrame:
    fr = [r.to_frame() for r in results.values() if r.trades]
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def summarise(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return dict(label=label, trades=0, note="no trades")
    r = df["r_net"].to_numpy("float64")
    de = trade_design_effect(df)
    bs = block_bootstrap_mean(r, mean_block=8.0, n_boot=4000, seed=11)
    # Inflate the SE by the design effect: clustered trades carry duplicate
    # information and the bootstrap alone does not know about cross-symbol
    # overlap.
    t_adj = bs["t"] / np.sqrt(de["deff"]) if np.isfinite(bs["t"]) else np.nan
    eq = np.cumsum(r)
    sh, so = sharpe_sortino(r, periods_per_year=365 * 96 / max(df["bars_held"].mean(), 1))
    wins = r[r > 0]; losses = r[r <= 0]
    return dict(
        label=label,
        trades=int(len(df)),
        effective_n=round(de["n_eff"], 1),
        deff=round(de["deff"], 2),
        win_rate=round(float((r > 0).mean()), 4),
        avg_r=round(float(r.mean()), 4),
        median_r=round(float(np.median(r)), 4),
        gross_r=round(float(df["r_gross"].mean()), 4),
        cost_r=round(float(df["cost_r"].mean()), 4),
        funding_r=round(float(df["funding_r"].mean()), 5),
        profit_factor=round(float(wins.sum() / -losses.sum()), 3) if losses.sum() < 0 else np.inf,
        sharpe=round(sh, 3) if np.isfinite(sh) else None,
        sortino=round(so, 3) if np.isfinite(so) else None,
        max_dd_r=round(max_drawdown(eq), 2),
        ci_low=round(bs["ci_low"], 4),
        ci_high=round(bs["ci_high"], 4),
        t_boot=round(bs["t"], 3) if np.isfinite(bs["t"]) else None,
        t_adj=round(t_adj, 3) if np.isfinite(t_adj) else None,
        hold_mean=round(float(df["bars_held"].mean()), 2),
        hold_median=float(df["bars_held"].median()),
        mfe_r=round(float(df["mfe_r"].mean()), 3),
        mae_r=round(float(df["mae_r"].mean()), 3),
        ambiguous_pct=round(100 * float(df["ambiguous"].mean()), 1),
        pct_target=round(100 * float((df["exit_reason"] == "target").mean()), 1),
        pct_stop=round(100 * float((df["exit_reason"] == "stop").mean()), 1),
        pct_time=round(100 * float((df["exit_reason"] == "time").mean()), 1),
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load()

    # ---- effective cross-sectional N over the experiment window ----------
    cols = {}
    for s in SYMBOLS:
        d = data[s]["ltp"]; d = d[d.time >= START]
        b = resample_ohlcv(d, 15)
        cols[s] = pd.Series(b.close.to_numpy(float), index=b.time.to_numpy("int64"))
    panel = np.log(pd.DataFrame(cols)).diff().dropna()
    xn = effective_n(panel)
    print(f"cross-sectional N: nominal {xn['K']}, mean_rho {xn['mean_rho']:.3f}, "
          f"PC1 {100*xn['pc1']:.1f}%, N_eff(PR) {xn['pr']:.2f}, N_eff(DEFF) {xn['deff']:.2f}\n")

    grid_rows, all_pooled = [], {}

    for k in K_VALUES:
        for exec_model in EXEC_MODELS:
            for fill_model in FILL_MODELS:
                if not exec_model.startswith("maker") and fill_model == "conservative":
                    continue  # fill model is meaningless for taker entry
                res = {}
                for s in SYMBOLS:
                    d = data[s]
                    res[s] = hscalp1.run(
                        d["ltp"], d["mark"], d["funding"], d["costs"],
                        k=k, exec_model=exec_model, fill_model=fill_model, start=START,
                    )
                pooled = pooled_frame(res)
                key = (k, exec_model, fill_model)
                all_pooled[key] = (res, pooled)
                row = summarise(pooled, f"k={k} {exec_model} {fill_model}")
                row.update(k=k, exec_model=exec_model, fill_model=fill_model,
                           signals=sum(r.signals for r in res.values()),
                           unfilled=sum(r.unfilled for r in res.values()))
                grid_rows.append(row)
                print(f"k={k} {exec_model:<20} {fill_model:<13} "
                      f"n={row['trades']:>5} netR={row.get('avg_r')} "
                      f"t_adj={row.get('t_adj')} cost/R={row.get('cost_r')}")

    grid = pd.DataFrame(grid_rows)
    grid.to_csv(OUT / "grid.csv", index=False)

    # ---- primary configuration: pre-registered as maker/maker conservative,
    # k=3.0 (the middle of the three declared values). Chosen before results.
    primary_key = (3.0, "maker/maker", "conservative")
    prim_res, prim_df = all_pooled[primary_key]
    prim = summarise(prim_df, "PRIMARY k=3.0 maker/maker conservative")

    print("\n" + "=" * 78)
    print("PRIMARY CONFIGURATION  k=3.0, maker/maker, conservative fill")
    print("=" * 78)
    for kk, vv in prim.items():
        print(f"  {kk:<16} {vv}")

    # ---- per-symbol ------------------------------------------------------
    per_symbol = [summarise(r.to_frame(), s) for s, r in prim_res.items()]
    ps = pd.DataFrame(per_symbol); ps.to_csv(OUT / "per_symbol.csv", index=False)
    print("\nPER SYMBOL (primary config)")
    print(ps[["label", "trades", "win_rate", "avg_r", "t_boot", "cost_r"]].to_string(index=False))

    # ---- by quarter ------------------------------------------------------
    if not prim_df.empty:
        q = prim_df.copy()
        q["quarter"] = pd.to_datetime(q["entry_time"], unit="s").dt.to_period("Q").astype(str)
        qrows = []
        for name, sub in q.groupby("quarter"):
            s_ = summarise(sub, name)
            if len(sub) < 30:
                s_["note"] = "INSUFFICIENT OBSERVATIONS"
            qrows.append(s_)
        qs = pd.DataFrame(qrows); qs.to_csv(OUT / "by_quarter.csv", index=False)
        print("\nBY QUARTER (primary config)")
        cols_q = ["label", "trades", "win_rate", "avg_r", "t_boot"]
        if "note" in qs:
            cols_q.append("note")
        print(qs[cols_q].to_string(index=False))

    # ---- primary null ----------------------------------------------------
    print("\nEXPOSURE-MATCHED RANDOM NULL (200 sims/symbol)")
    null_all = []
    for s in SYMBOLS:
        tmpl = prim_res[s].to_frame()
        if tmpl.empty:
            continue
        d = data[s]
        nn = nulls.random_entry_null(d["ltp"], d["mark"], d["funding"], d["costs"],
                                     tmpl, start=START, n_sims=60, seed=7)
        if nn["per_trade"].size:
            null_all.append(nn["per_trade"])
            print(f"  {s:<8} null mean netR = {nn['per_trade'].mean():+.4f} "
                  f"(strategy {tmpl['r_net'].mean():+.4f}, {len(tmpl)} trades)")
    null_pool = np.concatenate(null_all) if null_all else np.zeros(0)

    vs_null = {}
    if null_pool.size and not prim_df.empty:
        vs_null = bootstrap_diff(prim_df["r_net"].to_numpy("float64"), null_pool,
                                 mean_block=8.0, n_boot=4000, seed=13)
        print(f"\n  strategy - null = {vs_null['diff']:+.4f}R  "
              f"95% CI [{vs_null['ci_low']:+.4f}, {vs_null['ci_high']:+.4f}]  t={vs_null['t']:.2f}")

    # ---- directional baselines ------------------------------------------
    print("\nDIRECTIONAL BASELINES (buy&hold over the same window)")
    base = {}
    for s in SYMBOLS:
        lo_ = nulls.directional_baseline(data[s]["ltp"], data[s]["costs"], start=START, side=1)
        base[s] = lo_
        print(f"  {s:<8} long-only total {100*lo_['total_return']:+7.1f}%  Sharpe {lo_['sharpe']:+.2f}")

    # ---- out of sample ---------------------------------------------------
    print("\nTRAIN / VALIDATION / TEST (primary config)")
    oos = {}
    if not prim_df.empty:
        for name, (a, b) in SPLITS.items():
            sub = prim_df[(prim_df.entry_time >= a) & (prim_df.entry_time < b)]
            s_ = summarise(sub, name)
            oos[name] = s_
            note = "" if len(sub) >= 30 else "  <- INSUFFICIENT"
            print(f"  {name:<14} n={s_['trades']:>4} netR={s_.get('avg_r')} "
                  f"t={s_.get('t_boot')}{note}")

    # ---- cost / slippage stress -----------------------------------------
    print("\nCOST & SLIPPAGE STRESS (primary config)")
    stress = []
    for cm, sm, lbl in ((1.0, 1.0, "base"), (1.25, 1.0, "1.25x cost"),
                        (1.5, 1.0, "1.5x cost"), (2.0, 1.0, "2.0x cost"),
                        (1.0, 1.5, "1.5x slip"), (1.0, 2.0, "2.0x slip"),
                        (1.0, 3.0, "3.0x slip")):
        fr = []
        for s in SYMBOLS:
            d = data[s]
            r = hscalp1.run(d["ltp"], d["mark"], d["funding"], d["costs"],
                            k=3.0, exec_model="maker/maker", fill_model="conservative",
                            start=START, cost_multiplier=cm, slippage_multiplier=sm)
            if r.trades:
                fr.append(r.to_frame())
        if fr:
            f = pd.concat(fr, ignore_index=True)
            m = f["r_net"].mean()
            stress.append(dict(scenario=lbl, trades=len(f), net_r=round(float(m), 4)))
            print(f"  {lbl:<12} n={len(f):>5} netR={m:+.4f}")
    pd.DataFrame(stress).to_csv(OUT / "stress.csv", index=False)

    json.dump(dict(primary=prim, per_symbol=per_symbol, oos=oos,
                   vs_null={k_: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                            for k_, v in vs_null.items()},
                   cross_sectional_n=xn, baselines=base),
              open(OUT / "results.json", "w"), indent=2, default=str)
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
