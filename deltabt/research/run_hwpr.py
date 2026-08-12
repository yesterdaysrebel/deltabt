"""Execute H-WPR-1 on TRAIN + VALIDATION only. TEST STAYS LOCKED.

    PYTHONPATH=. python3 -u -m deltabt.research.run_hwpr
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
from deltabt.research.stats import block_bootstrap_mean, trade_design_effect

OUT = OUT_DIR / "hwpr"
STUDY = int(pd.Timestamp("2025-01-01", tz="UTC").timestamp())


def summarise(df: pd.DataFrame, label: str) -> dict:
    if df.empty:
        return dict(label=label, trades=0, gross_r=None, net_r=None, note="no trades")
    g = df.r_gross.to_numpy("float64"); n = df.r_net.to_numpy("float64")
    de = trade_design_effect(df)
    bs = block_bootstrap_mean(n, mean_block=6.0, n_boot=2500, seed=11)
    bg = block_bootstrap_mean(g, mean_block=6.0, n_boot=2500, seed=12)
    sq = np.sqrt(de["deff"])
    wins = n[n > 0]; losses = n[n <= 0]
    days = max((df.entry_time.max() - df.entry_time.min()) / 86400, 1)
    eq = np.cumsum(n)
    return dict(
        label=label, trades=int(len(df)), effective_n=round(de["n_eff"], 1),
        win_rate=round(float((n > 0).mean()), 4),
        avg_win_r=round(float(wins.mean()), 4) if wins.size else None,
        avg_loss_r=round(float(losses.mean()), 4) if losses.size else None,
        gross_r=round(float(g.mean()), 4),
        fee_r=round(float(df.fee_r.mean()), 4),
        slip_r=round(float(df.slip_r.mean()), 4),
        funding_r=round(float(df.funding_r.mean()), 5),
        cost_r=round(float(df.cost_r.mean()), 4),
        net_r=round(float(n.mean()), 4),
        t_gross=round(float(bg["t"] / sq), 3) if np.isfinite(bg["t"]) else None,
        t_net=round(float(bs["t"] / sq), 3) if np.isfinite(bs["t"]) else None,
        ci_low=round(bs["ci_low"], 4), ci_high=round(bs["ci_high"], 4),
        profit_factor=round(float(wins.sum() / -losses.sum()), 3) if losses.sum() < 0 else None,
        max_dd_r=round(float(np.max(np.maximum.accumulate(eq) - eq)), 1),
        dur_mean=round(float(df.bars_held.mean()), 1),
        dur_median=float(df.bars_held.median()),
        trades_per_day=round(len(df) / days, 2),
        stop_pct_median=round(float(df.stop_pct.median()) * 100, 4),
        cost_over_gross=(round(float(df.cost_r.mean() / g.mean()), 3) if g.mean() > 0 else None),
        pct_long=round(100 * float((df.side > 0).mean()), 1),
        pct_target=round(100 * float((df.exit_reason == "target").mean()), 1),
        pct_stop=round(100 * float((df.exit_reason == "stop").mean()), 1),
        ambiguous_pct=round(100 * float(df.ambiguous.mean()), 1),
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    universe = pd.read_csv(OUT_DIR / "hwpr_universe.csv").symbol.tolist()
    store, cat = CandleStore(), ProductCatalog()

    print(f"FROZEN UNIVERSE (set before results): {universe}")
    data = {}
    for s in universe:
        ltp = store.read(s, "ltp", "1m")
        ltp = ltp[ltp.time >= STUDY].reset_index(drop=True)
        data[s] = dict(
            df=ltp, mark=store.read(s, "mark", "1m"),
            funding=store.read(s, "funding", "1h"),
            costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0),
            tradable=tradable_mask(ltp),
        )
        print(f"  {s}: {len(ltp):,} 1m bars "
              f"{pd.Timestamp(int(ltp.time.iloc[0]),unit='s').date()} -> "
              f"{pd.Timestamp(int(ltp.time.iloc[-1]),unit='s').date()}")

    last = min(int(d["df"].time.iloc[-1]) for d in data.values())
    span = last - STUDY
    TR = (STUDY, STUDY + int(span * 0.6))
    VA = (STUDY + int(span * 0.6), STUDY + int(span * 0.8))
    print(f"\n  train {pd.Timestamp(TR[0],unit='s').date()} -> {pd.Timestamp(TR[1],unit='s').date()}")
    print(f"  valid {pd.Timestamp(VA[0],unit='s').date()} -> {pd.Timestamp(VA[1],unit='s').date()}")
    print(f"  test  {pd.Timestamp(VA[1],unit='s').date()} -> {pd.Timestamp(last,unit='s').date()}  [LOCKED]\n")

    print("computing frozen indicator conditions (once per symbol)...")
    for s, d in data.items():
        d["C"] = hwpr.build_conditions(d["df"])
    print("  done\n")

    def run_all(arm, *, wpr_variant="A", target_r=2.0, legacy_stop=False,
                cm=1.0, sm=1.0, window=None):
        frames = []
        meta = dict(signals=0, skipped_stop=0, skipped_size=0)
        for s, d in data.items():
            r = hwpr.run(d["df"], d["mark"], d["funding"], d["costs"], d["C"],
                         arm=arm, wpr_variant=wpr_variant, target_r=target_r,
                         start=STUDY, end=window[1] if window else None,
                         legacy_stop=legacy_stop, cost_multiplier=cm,
                         slippage_multiplier=sm, tradable=d["tradable"])
            meta["signals"] += r.signals
            meta["skipped_stop"] += r.skipped_stop
            meta["skipped_size"] += r.skipped_size
            f = r.to_frame()
            if len(f) and window:
                f = f[(f.entry_time >= window[0]) & (f.entry_time < window[1])]
            if len(f):
                frames.append(f)
        return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), meta

    # ---------------- frozen baseline: Arm A, WPR-A, 2R, structural stop ----
    print("=" * 100)
    print("FROZEN BASELINE — Arm A (5m ST+ADX/DI · 1m ST+ADX/DI · WPR>-80 rising) · 2R · structural stop")
    print("=" * 100)
    base = {}
    for nm, win in (("train", TR), ("valid", VA)):
        df, meta = run_all("A", window=win)
        base[nm] = summarise(df, nm)
        base[nm].update(meta)
        m = base[nm]
        if not m["trades"]:
            print(f"  {nm}: NO TRADES (signals {meta['signals']:,})"); continue
        print(f"  {nm}: n={m['trades']:,}  signals={meta['signals']:,}  win={m['win_rate']:.3f}  "
              f"GROSS={m['gross_r']:+.4f}  cost={m['cost_r']:.4f}  NET={m['net_r']:+.4f}  "
              f"t_gross={m['t_gross']}  t_net={m['t_net']}")
        print(f"         fee {m['fee_r']:.4f} | slip {m['slip_r']:.4f} | funding {m['funding_r']:+.5f} "
              f"| stop {m['stop_pct_median']:.3f}% | {m['trades_per_day']:.1f} trades/day "
              f"| PF {m['profit_factor']} | dur {m['dur_median']:.0f}m")
    json.dump(base, open(OUT / "baseline.json", "w"), indent=2, default=str)

    # ---------------- ablation arms A-E ------------------------------------
    print("\n" + "=" * 100)
    print("PRE-DECLARED ABLATION ARMS (diagnostics — the best arm is NOT the strategy)")
    print("=" * 100)
    desc = {"A": "full stack + WPR", "B": "remove WPR", "C": "remove 1m ADX/DI",
            "D": "remove 1m Supertrend", "E": "5m regime + WPR only"}
    rows = []
    for arm in hwpr.ARMS:
        for nm, win in (("train", TR), ("valid", VA)):
            df, meta = run_all(arm, window=win)
            r = summarise(df, f"{arm}|{nm}")
            r.update(arm=arm, split=nm, desc=desc[arm], **meta)
            rows.append(r)
            if r["trades"]:
                print(f"  {arm} {desc[arm]:<24} {nm:<6} n={r['trades']:>6,}  "
                      f"GROSS={r['gross_r']:+.4f}  cost={r['cost_r']:.4f}  NET={r['net_r']:+.4f}  "
                      f"t_gross={r['t_gross']}")
            else:
                print(f"  {arm} {desc[arm]:<24} {nm:<6} no trades")
    pd.DataFrame(rows).to_csv(OUT / "arms.csv", index=False)

    # ---------------- WPR variants -----------------------------------------
    print("\n" + "=" * 100)
    print("WILLIAMS %R VARIANTS (Arm A structure, pre-registered variants only)")
    print("=" * 100)
    wrows = []
    wdesc = {"A": "> -80 & rising", "B": "> -50 & rising", "C": "crosses up through -80"}
    for v in hwpr.WPR_VARIANTS:
        for nm, win in (("train", TR), ("valid", VA)):
            df, _ = run_all("A", wpr_variant=v, window=win)
            r = summarise(df, f"WPR-{v}|{nm}")
            r.update(variant=v, split=nm, desc=wdesc[v])
            wrows.append(r)
            if r["trades"]:
                print(f"  WPR-{v} {wdesc[v]:<24} {nm:<6} n={r['trades']:>6,}  "
                      f"GROSS={r['gross_r']:+.4f}  NET={r['net_r']:+.4f}  t_gross={r['t_gross']}")
            else:
                print(f"  WPR-{v} {wdesc[v]:<24} {nm:<6} no trades")
    pd.DataFrame(wrows).to_csv(OUT / "wpr_variants.csv", index=False)

    # ---------------- targets & stop definition ----------------------------
    print("\n" + "=" * 100)
    print("TARGET AND STOP DIAGNOSTICS (Arm A)")
    print("=" * 100)
    drows = []
    for tr_ in (2.0, 3.0):
        for legacy in (False, True):
            for nm, win in (("train", TR), ("valid", VA)):
                df, _ = run_all("A", target_r=tr_, legacy_stop=legacy, window=win)
                r = summarise(df, f"{tr_}R|{'legacy' if legacy else 'structural'}|{nm}")
                r.update(target_r=tr_, stop="legacy" if legacy else "structural", split=nm)
                drows.append(r)
                if r["trades"]:
                    print(f"  {tr_:.0f}R {('legacy min(ST,low)' if legacy else 'structural leg-low'):<22} "
                          f"{nm:<6} n={r['trades']:>6,}  GROSS={r['gross_r']:+.4f}  "
                          f"NET={r['net_r']:+.4f}  stop={r['stop_pct_median']:.3f}%")
    pd.DataFrame(drows).to_csv(OUT / "targets_stops.csv", index=False)

    # ---------------- §20 comparison vs the previous pullback strategy -----
    print("\n" + "=" * 100)
    print("§20 COMPARISON — H-WPR-1 (no pullback) vs the PREVIOUS pullback strategy")
    print("   identical symbols, window, indicators, stop, sizing and cost model")
    print("=" * 100)
    comp = []
    for arm, nm_ in (("A", "H-WPR-1 momentum (no pullback)"), ("PULLBACK", "previous pullback strategy")):
        for nm, win in (("train", TR), ("valid", VA)):
            df, meta = run_all(arm, window=win)
            r = summarise(df, f"{nm_}|{nm}")
            r.update(strategy=nm_, split=nm, **meta)
            comp.append(r)
            if r["trades"]:
                print(f"  {nm_:<32} {nm:<6} n={r['trades']:>6,}  signals={meta['signals']:>8,}  "
                      f"GROSS={r['gross_r']:+.4f}  NET={r['net_r']:+.4f}  win={r['win_rate']:.3f}  "
                      f"cost/R={r['cost_r']:.4f}  stop={r['stop_pct_median']:.3f}%")
            else:
                print(f"  {nm_:<32} {nm:<6} n=0  signals={meta['signals']:,}")
    pd.DataFrame(comp).to_csv(OUT / "comparison.csv", index=False)

    # ---------------- decision gate -----------------------------------------
    tg, vg = base["train"].get("gross_r"), base["valid"].get("gross_r")
    tc, vc = base["train"].get("cost_r"), base["valid"].get("cost_r")
    print("\n" + "=" * 100)
    if tg is None or vg is None:
        verdict = "NO TRADES"
    elif tg <= 0 and vg <= 0:
        verdict = "NO SIGNAL — gross expectancy <= 0 on BOTH train and validation"
    elif (tg <= tc) or (vg <= vc):
        verdict = "NO ECONOMIC EDGE — gross positive but <= transaction cost on at least one segment"
    else:
        verdict = "GROSS EDGE EXCEEDS COST ON BOTH SEGMENTS — proceed to robustness"
    print(f"PRIMARY DECISION (§16): {verdict}")
    print(f"  train gross {tg} vs cost {tc}    |    valid gross {vg} vs cost {vc}")
    print("TEST SEGMENT NOT COMPUTED (locked).")
    json.dump(dict(verdict=verdict, baseline=base, universe=universe),
              open(OUT / "decision.json", "w"), indent=2, default=str)
    print(f"\nwritten to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
