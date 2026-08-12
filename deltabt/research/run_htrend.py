"""Execute H-TREND-1 on TRAIN + VALIDATION only. TEST STAYS LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_htrend

Component attribution for pure multi-timeframe trend alignment. No Williams %R
anywhere, no pullback, no added indicators. Arm A is definitionally identical
to H-WPR-1's Arm B; it is re-run here rather than quoted so that every figure
in this experiment comes from one consistent execution.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.quality import tradable_mask
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hwpr
from deltabt.research.run_hwpr import STUDY, summarise
from deltabt.research.stats import bootstrap_diff

OUT = OUT_DIR / "htrend"
ARMS = {
    "T_A": "A  5m regime + 1m ST + 1m ADX/DI",
    "T_B": "B  5m regime only",
    "T_C": "C  5m regime + 1m ST",
    "T_D": "D  5m regime + 1m ADX/DI",
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hwpr_universe.csv").symbol.tolist()
    store, cat = CandleStore(), ProductCatalog()
    print(f"FROZEN UNIVERSE (eligibility over the COMPLETE study window): {universe}")

    data = {}
    for s in universe:
        ltp = store.read(s, "ltp", "1m")
        ltp = ltp[ltp.time >= STUDY].reset_index(drop=True)
        data[s] = dict(df=ltp, mark=store.read(s, "mark", "1m"),
                       funding=store.read(s, "funding", "1h"),
                       costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
                       tradable=tradable_mask(ltp))
    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print(f"  train {pd.Timestamp(TR[0],unit='s').date()} -> {pd.Timestamp(TR[1],unit='s').date()}")
    print(f"  valid {pd.Timestamp(VA[0],unit='s').date()} -> {pd.Timestamp(VA[1],unit='s').date()}")
    print(f"  test  {pd.Timestamp(VA[1],unit='s').date()} -> {pd.Timestamp(last,unit='s').date()}  [LOCKED]\n")

    print("computing frozen indicator conditions...")
    for s, d in data.items():
        d["C"] = hwpr.build_conditions(d["df"])
    print("  done\n")

    def run_all(arm, *, target_r=2.0, window=None, cm=1.0, sm=1.0):
        frames, sig = [], 0
        for s, d in data.items():
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"], d["C"],
                         arm=arm, target_r=target_r, start=STUDY,
                         end=window[1] if window else None,
                         cost_multiplier=cm, slippage_multiplier=sm,
                         tradable=d["tradable"])
            sig += r.signals
            f = r.to_frame()
            if len(f) and window:
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), sig

    # ---- §17 the critical metric: does gross expectancy survive? ----------
    print("=" * 104)
    print("GROSS EXPECTANCY BY ARM — the primary question, before any net economics")
    print("=" * 104)
    print(f"{'arm':<36}{'split':<7}{'trades':>8}{'gross_R':>10}{'t_gross':>9}"
          f"{'cost_R':>9}{'net_R':>10}{'win':>7}{'stop%':>8}")
    rows = []
    for arm, desc in ARMS.items():
        for nm, win in (("train", TR), ("valid", VA)):
            df, sig = run_all(arm, window=win)
            r = summarise(df, f"{arm}|{nm}")
            r.update(arm=arm, desc=desc, split=nm, signals=sig)
            rows.append(r)
            if not r["trades"]:
                print(f"{desc:<36}{nm:<7}{'no trades':>8}"); continue
            print(f"{desc:<36}{nm:<7}{r['trades']:>8,}{r['gross_r']:>+10.4f}"
                  f"{r['t_gross']:>9.2f}{r['cost_r']:>9.4f}{r['net_r']:>+10.4f}"
                  f"{r['win_rate']:>7.3f}{r['stop_pct_median']:>8.3f}")
    grid = pd.DataFrame(rows)
    grid.to_csv(OUT / "arms.csv", index=False)

    # ---- 3R diagnostic ----------------------------------------------------
    print("\n3R TARGET DIAGNOSTIC")
    for arm, desc in ARMS.items():
        line = f"  {desc:<36}"
        for nm, win in (("train", TR), ("valid", VA)):
            df, _ = run_all(arm, target_r=3.0, window=win)
            r = summarise(df, "")
            line += (f"{nm} gross {r['gross_r']:+.4f} net {r['net_r']:+.4f}   "
                     if r["trades"] else f"{nm} no trades   ")
        print(line)

    # ---- §15 primary comparison: does 1m confirmation add information? ----
    print("\n" + "=" * 104)
    print("§15 PRIMARY COMPARISON — Arm A (5m + 1m confirmation) vs Arm B (5m regime only)")
    print("=" * 104)
    for nm, win in (("train", TR), ("valid", VA)):
        a, _ = run_all("T_A", window=win)
        b, _ = run_all("T_B", window=win)
        ra, rb = summarise(a, "A"), summarise(b, "B")
        if not (ra["trades"] and rb["trades"]):
            continue
        print(f"\n  {nm}:")
        print(f"    {'metric':<22}{'A (5m+1m)':>14}{'B (5m only)':>14}{'delta':>12}")
        for k, lbl, f in (("trades", "trades", "{:,.0f}"), ("gross_r", "gross R", "{:+.4f}"),
                          ("t_gross", "t_gross", "{:+.2f}"), ("net_r", "net R", "{:+.4f}"),
                          ("win_rate", "win rate", "{:.3f}"), ("cost_r", "cost/R", "{:.4f}"),
                          ("profit_factor", "profit factor", "{:.3f}"),
                          ("stop_pct_median", "median stop %", "{:.3f}"),
                          ("trades_per_day", "trades/day", "{:.1f}")):
            va_, vb_ = ra.get(k), rb.get(k)
            if va_ is None or vb_ is None:
                continue
            print(f"    {lbl:<22}{f.format(va_):>14}{f.format(vb_):>14}"
                  f"{(va_-vb_):>+12.4f}")
        # is the difference in gross itself distinguishable from noise?
        cmp = bootstrap_diff(a.r_gross.to_numpy("float64"), b.r_gross.to_numpy("float64"),
                             mean_block=6.0, n_boot=2500, seed=19)
        print(f"    gross(A) - gross(B) = {cmp['diff']:+.4f}  "
              f"CI[{cmp['ci_low']:+.4f},{cmp['ci_high']:+.4f}]  t={cmp['t']:.2f}")

    # ---- detail on Arm A over train+valid ---------------------------------
    both, _ = run_all("T_A", window=(TR[0], VA[1]))
    if not both.empty:
        both.to_csv(OUT / "trades_armA.csv", index=False)
        print("\nPER SYMBOL (Arm A, train+valid)")
        ps = pd.DataFrame([summarise(g, s) for s, g in both.groupby("symbol")])
        print(ps[["label", "trades", "win_rate", "gross_r", "t_gross", "cost_r", "net_r"]]
              .to_string(index=False))
        print(f"  symbols with positive gross: {int((ps.gross_r > 0).sum())}/{len(ps)}")
        ps.to_csv(OUT / "per_symbol.csv", index=False)

        print("\nLONG vs SHORT (Arm A)")
        print(pd.DataFrame([summarise(g, "long" if k > 0 else "short")
                            for k, g in both.groupby("side")])
              [["label", "trades", "win_rate", "gross_r", "t_gross", "net_r"]].to_string(index=False))

        q = both.assign(quarter=pd.to_datetime(both.entry_time, unit="s")
                        .dt.to_period("Q").astype(str))
        qs = pd.DataFrame([summarise(g, k) for k, g in q.groupby("quarter")])
        print("\nBY QUARTER (Arm A)")
        print(qs[["label", "trades", "win_rate", "gross_r", "t_gross", "net_r"]].to_string(index=False))
        print(f"  quarters with positive gross: {int((qs.gross_r > 0).sum())}/{len(qs)}")
        qs.to_csv(OUT / "by_quarter.csv", index=False)

        mo = both.assign(month=pd.to_datetime(both.entry_time, unit="s")
                         .dt.to_period("M").astype(str)).groupby("month").agg(
            n=("r_gross", "size"), gross=("r_gross", "mean"), net=("r_net", "mean")).round(4)
        print("\nBY MONTH (Arm A)")
        print(mo.to_string())
        print(f"  months with positive gross: {int((mo.gross > 0).sum())}/{len(mo)}")
        mo.to_csv(OUT / "by_month.csv")

    # ---- decision ----------------------------------------------------------
    a_tr = grid[(grid.arm == "T_A") & (grid.split == "train")].iloc[0]
    a_va = grid[(grid.arm == "T_A") & (grid.split == "valid")].iloc[0]
    tg, vg = a_tr.gross_r, a_va.gross_r
    fric = max(a_tr.cost_r, a_va.cost_r)
    if (tg is None or vg is None) or tg <= 0 or vg <= 0:
        verdict = "NO SIGNAL"
        why = (f"Arm A gross is {tg:+.4f} on train and {vg:+.4f} on validation; "
               f"the pre-registered rule classifies NO SIGNAL when either is <= 0.")
    elif tg <= fric or vg <= fric:
        verdict = "NO ECONOMIC EDGE"
        why = f"gross positive on both segments but below friction {fric:.4f}"
    else:
        verdict = "PROMISING (pending robustness)"
        why = "gross materially exceeds friction on both segments"
    print("\n" + "=" * 104)
    print(f"§23 DECISION: {verdict}")
    print(f"  {why}")
    print("TEST SEGMENT NOT COMPUTED (locked).")
    json.dump(dict(verdict=verdict, why=why, universe=universe,
                   arms=grid.to_dict("records")),
              open(OUT / "decision.json", "w"), indent=2, default=str)
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
