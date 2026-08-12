"""Execute the pre-registered H-Scalp-2 experiment.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hscalp2
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hscalp2, nulls
from deltabt.research.hscalp2 import (
    EXEC_MODELS,
    FILL_MODELS,
    K_VALUES,
    PRIMARY,
    RETEST_FRACS,
)
from deltabt.research.run_hscalp1 import SPLITS, START, SYMBOLS, summarise
from deltabt.research.stats import bootstrap_diff, effective_n
from deltabt.strategy import resample_ohlcv

OUT = OUT_DIR / "hscalp2"


def load() -> dict:
    store, cat = CandleStore(), ProductCatalog()
    return {
        s: dict(ltp=store.read(s, "ltp", "1m"), mark=store.read(s, "mark", "1m"),
                funding=store.read(s, "funding", "1h"),
                costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0))
        for s in SYMBOLS
    }


def run_config(data, *, k, retest, exec_model, fill_model,
               cost_multiplier=1.0, slippage_multiplier=1.0):
    res = {}
    for s in SYMBOLS:
        d = data[s]
        res[s] = hscalp2.run(d["ltp"], d["mark"], d["funding"], d["costs"],
                             k=k, retest=retest, exec_model=exec_model,
                             fill_model=fill_model, start=START,
                             cost_multiplier=cost_multiplier,
                             slippage_multiplier=slippage_multiplier)
    frames = [r.to_frame() for r in res.values() if r.trades]
    pooled = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return res, pooled


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load()

    cols = {}
    for s in SYMBOLS:
        d = data[s]["ltp"]; d = d[d.time >= START]
        b = resample_ohlcv(d, 15)
        cols[s] = pd.Series(b.close.to_numpy(float), index=b.time.to_numpy("int64"))
    xn = effective_n(np.log(pd.DataFrame(cols)).diff().dropna())
    print(f"cross-sectional N: nominal {xn['K']}, mean_rho {xn['mean_rho']:.3f}, "
          f"PC1 {100*xn['pc1']:.1f}%, N_eff(PR) {xn['pr']:.2f}, N_eff(DEFF) {xn['deff']:.2f}\n")

    # ---- full pre-registered grid ---------------------------------------
    print("FULL GRID (k x retest x execution x fill) -- complete, not filtered")
    rows = []
    for k in K_VALUES:
        for rt in RETEST_FRACS:
            for em in EXEC_MODELS:
                for fm in FILL_MODELS:
                    if not em.startswith("maker") and fm == "conservative":
                        continue
                    res, pooled = run_config(data, k=k, retest=rt,
                                             exec_model=em, fill_model=fm)
                    row = summarise(pooled, f"k={k} rt={rt} {em} {fm}")
                    row.update(k=k, retest=rt, exec_model=em, fill_model=fm,
                               signals=sum(r.signals for r in res.values()),
                               invalidated=sum(r.invalidated for r in res.values()),
                               expired=sum(r.expired for r in res.values()),
                               skipped=sum(r.skipped_ambiguous for r in res.values()))
                    rows.append(row)
                    print(f"  k={k} rt={rt:.2f} {em:<20}{fm:<13}"
                          f"n={row['trades']:>5} netR={row.get('avg_r')} "
                          f"grossR={row.get('gross_r')} t_adj={row.get('t_adj')}")
    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "grid.csv", index=False)

    # ---- primary --------------------------------------------------------
    prim_res, prim_df = run_config(data, **PRIMARY)
    prim = summarise(prim_df, "PRIMARY k=3.0 retest=0.33 maker/maker conservative")
    print("\n" + "=" * 78)
    print("PRIMARY  k=3.0, retest=33%, maker/maker, conservative fill")
    print("=" * 78)
    for kk, vv in prim.items():
        print(f"  {kk:<16} {vv}")
    print(f"  signals          {sum(r.signals for r in prim_res.values())}")
    print(f"  invalidated      {sum(r.invalidated for r in prim_res.values())}")
    print(f"  expired unfilled {sum(r.expired for r in prim_res.values())}")
    print(f"  skipped (ambig)  {sum(r.skipped_ambiguous for r in prim_res.values())}")

    per_symbol = [summarise(r.to_frame(), s) for s, r in prim_res.items()]
    pd.DataFrame(per_symbol).to_csv(OUT / "per_symbol.csv", index=False)
    print("\nPER SYMBOL")
    print(pd.DataFrame(per_symbol)[
        ["label", "trades", "win_rate", "gross_r", "avg_r", "t_boot", "cost_r"]
    ].to_string(index=False))

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
        print("\nBY QUARTER")
        c_ = ["label", "trades", "win_rate", "gross_r", "avg_r", "t_boot"]
        if "note" in qs:
            c_.append("note")
        print(qs[c_].to_string(index=False))

    # ---- null -----------------------------------------------------------
    print("\nEXPOSURE-MATCHED RANDOM NULL")
    null_all = []
    for s in SYMBOLS:
        tmpl = prim_res[s].to_frame()
        if tmpl.empty:
            continue
        # the null needs an `event_range` column to size risk from; H-Scalp-2
        # sizes off the retracement distance instead, so map it across
        tmpl = tmpl.assign(event_range=tmpl["r_price"] / 1.5)
        d = data[s]
        nn = nulls.random_entry_null(d["ltp"], d["mark"], d["funding"], d["costs"],
                                     tmpl, start=START, n_sims=60, seed=7)
        if nn["per_trade"].size:
            null_all.append(nn["per_trade"])
            print(f"  {s:<8} null {nn['per_trade'].mean():+.4f}  "
                  f"strategy {prim_res[s].to_frame()['r_net'].mean():+.4f} "
                  f"({len(tmpl)} trades)")
    vs_null = {}
    if null_all and not prim_df.empty:
        pool = np.concatenate(null_all)
        vs_null = bootstrap_diff(prim_df["r_net"].to_numpy("float64"), pool,
                                 mean_block=8.0, n_boot=4000, seed=13)
        print(f"\n  strategy - null = {vs_null['diff']:+.4f}R  "
              f"95% CI [{vs_null['ci_low']:+.4f}, {vs_null['ci_high']:+.4f}]  "
              f"t={vs_null['t']:.2f}")

    print("\nDIRECTIONAL BASELINES")
    base = {}
    for s in SYMBOLS:
        b = nulls.directional_baseline(data[s]["ltp"], data[s]["costs"], start=START, side=1)
        base[s] = b
        print(f"  {s:<8} long-only {100*b['total_return']:+7.1f}%  "
              f"short-only {-100*b['total_return']:+7.1f}%  Sharpe {b['sharpe']:+.2f}")

    print("\nTRAIN / VALIDATION / TEST")
    oos = {}
    if not prim_df.empty:
        for name, (a, b) in SPLITS.items():
            sub = prim_df[(prim_df.entry_time >= a) & (prim_df.entry_time < b)]
            s_ = summarise(sub, name); oos[name] = s_
            note = "" if len(sub) >= 30 else "  <- INSUFFICIENT"
            print(f"  {name:<14} n={s_['trades']:>4} grossR={s_.get('gross_r')} "
                  f"netR={s_.get('avg_r')} t={s_.get('t_boot')}{note}")

    print("\nCOST & SLIPPAGE STRESS")
    stress = []
    for cm, sm, lbl in ((1.0, 1.0, "base"), (1.25, 1.0, "1.25x cost"),
                        (1.5, 1.0, "1.5x cost"), (2.0, 1.0, "2.0x cost"),
                        (1.0, 1.5, "1.5x slip"), (1.0, 2.0, "2.0x slip"),
                        (1.0, 3.0, "3.0x slip")):
        _, f = run_config(data, **{**PRIMARY, "cost_multiplier": cm,
                                   "slippage_multiplier": sm})
        if not f.empty:
            stress.append(dict(scenario=lbl, trades=len(f),
                               net_r=round(float(f["r_net"].mean()), 4)))
            print(f"  {lbl:<12} n={len(f):>5} netR={f['r_net'].mean():+.4f}")
    pd.DataFrame(stress).to_csv(OUT / "stress.csv", index=False)

    prim_df.to_csv(OUT / "trades_primary.csv", index=False)
    json.dump(dict(primary=prim, per_symbol=per_symbol, oos=oos,
                   vs_null={k_: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                            for k_, v in vs_null.items()},
                   cross_sectional_n=xn, baselines=base),
              open(OUT / "results.json", "w"), indent=2, default=str)
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
