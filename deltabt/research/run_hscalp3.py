"""H-Scalp-3: does the continuation mechanism survive at a longer horizon?

    PYTHONPATH=. python3 -u -m deltabt.research.run_hscalp3

PRE-REGISTERED IN docs/hscalp3_prereg.md, frozen before this was run at
SHA-256 a0f176beb8c5e40d4faad07404070b50c8743c104494277262a16e6c1f09fbcb.
Read that first; this file only executes what it specifies.

THE ONE-LINE QUESTION. H-Scalp-2 is the only experiment in the registry with a
positive gross (+0.1156R, positive in train, validation AND test, and on all
four symbols) and it loses 0.0203R to a cost/R of 0.1359. Since a k-sigma move
scales as sqrt(T), R scales as sqrt(T) and cost/R falls as 1/sqrt(T). Does the
gross survive long enough for the arithmetic to cross zero?

TWO SEPARABLE PREDICTIONS, REPORTED SEPARATELY.
  1. cost/R falls as 1/sqrt(T).  Nearly mechanical. A large miss means the
     move-scaling assumption is wrong and nothing below is interpretable.
  2. gross is flat in T.  The real question, and the one that can kill it: a
     longer horizon puts the target further into the future, where whatever
     the displacement knew may have decayed.

TEST IS COMPUTED BUT IS NOT EVIDENCE. H-Scalp-2 already spent the 2026 window
on this exact mechanism and symbol set, so it is contaminated by construction.
It is printed for completeness and cannot support a verdict. Selection across
the bar_minutes grid happens on VALIDATION.

NOTHING IN hscalp2.py's SIMULATOR IS REIMPLEMENTED. `bar_minutes` was threaded
through hscalp2.run with a default of 15, and the default was verified against
the registry: cost/R 0.1359 to four decimals, n=2,467 vs 2,466 recorded (one
extra event, from ~2h of bars added after the original run was recorded).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.costs import SymbolCosts
from deltabt.data.store import CandleStore, ProductCatalog
from deltabt.research import hscalp2
from deltabt.research.run_hscalp1 import SPLITS, START, SYMBOLS, summarise

OUT = OUT_DIR / "hscalp3"

#: 15 is the REFERENCE cell -- it must reproduce the registry and is not a
#: candidate. The rest are the hypothesis.
BAR_MINUTES = (15, 30, 60, 120, 240)

PRIMARY = dict(k=3.0, retest=0.33, exec_model="maker/maker",
               fill_model="conservative")

#: Pre-registered robustness axes. DO NOT EXPAND -- the grid is already 360
#: cells and every addition makes the multiple-comparison discount worse.
K_ROBUST = (2.5, 3.5)
RETEST_ROBUST = (0.25, 0.50)


def load() -> dict:
    store, cat = CandleStore(), ProductCatalog()
    return {
        s: dict(ltp=store.read(s, "ltp", "1m"), mark=store.read(s, "mark", "1m"),
                funding=store.read(s, "funding", "1h"),
                costs=SymbolCosts.from_spec(cat.get(s), slippage_bps=2.0))
        for s in SYMBOLS
    }


def run_cell(data, *, bar_minutes, k, retest, exec_model, fill_model,
             cost_multiplier=1.0):
    """One configuration, pooled across symbols, with the per-symbol split."""
    per, frames = {}, []
    for s in SYMBOLS:
        d = data[s]
        r = hscalp2.run(d["ltp"], d["mark"], d["funding"], d["costs"],
                        k=k, retest=retest, exec_model=exec_model,
                        fill_model=fill_model, start=START,
                        bar_minutes=bar_minutes,
                        cost_multiplier=cost_multiplier)
        f = r.to_frame()
        per[s] = f
        if len(f):
            frames.append(f)
    pooled = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return per, pooled


def net_of(m: dict) -> float:
    """summarise() reports gross, cost and funding; net is their difference."""
    if not m.get("trades"):
        return float("nan")
    return m["gross_r"] - m["cost_r"] - m["funding_r"]


def window(df: pd.DataFrame, name: str) -> dict:
    a, b = SPLITS[name]
    sub = df[(df.entry_time >= a) & (df.entry_time < b)] if len(df) else df
    return summarise(sub, name)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("loading 1m history...")
    data = load()
    out = {}

    # ---- the primary sweep --------------------------------------------
    print("\n" + "=" * 120)
    print("PRIMARY CELL  k=3.0  retest=0.33  maker/maker  conservative")
    print("TEST IS CONTAMINATED (H-Scalp-2 spent it) AND IS NOT EVIDENCE")
    print("=" * 120)
    print(f"  {'bars':>5} {'window':>12} {'n':>7} {'n_eff':>7} {'win':>6} "
          f"{'GROSS':>9} {'cost':>8} {'NET':>9} {'t_boot':>8} {'95% CI':>20}")
    print("  " + "-" * 116)

    for bm in BAR_MINUTES:
        per, pooled = run_cell(data, bar_minutes=bm, **PRIMARY)
        row = {}
        for name in ("train 2025H1", "valid 2025H2", "test  2026"):
            m = window(pooled, name)
            row[name] = m
            if not m["trades"]:
                print(f"  {bm:>5} {name:>12}   NO TRADES")
                continue
            flag = "  <- n<30" if m["trades"] < 30 else ""
            print(f"  {bm:>5} {name:>12} {m['trades']:>7,} "
                  f"{m['effective_n']:>7.0f} {m['win_rate']:>6.3f} "
                  f"{m['gross_r']:>+9.4f} {m['cost_r']:>8.4f} "
                  f"{net_of(m):>+9.4f} {str(m['t_boot']):>8} "
                  f"[{m['ci_low']:>+.3f},{m['ci_high']:>+.3f}]{flag}")
        # gross positivity per symbol, on train+valid only
        a = SPLITS["train 2025H1"][0]
        b = SPLITS["valid 2025H2"][1]
        sym_gross = {}
        for s, f in per.items():
            sub = f[(f.entry_time >= a) & (f.entry_time < b)] if len(f) else f
            sym_gross[s] = summarise(sub, s).get("gross_r")
        row["symbol_gross_trainvalid"] = sym_gross
        ok = [v for v in sym_gross.values() if v is not None]
        print(f"  {'':5} {'per-symbol':>12} gross train+valid: "
              + "  ".join(f"{s} {v:+.4f}" for s, v in sym_gross.items() if v is not None)
              + f"   ({sum(1 for v in ok if v > 0)}/{len(ok)} positive)")
        out[f"primary_{bm}"] = row
        print()

    # ---- prediction 1: does cost/R scale as 1/sqrt(T)? ------------------
    print("=" * 120)
    print("PREDICTION 1 -- cost/R should fall as 1/sqrt(T)")
    print("=" * 120)
    base = out.get("primary_15", {}).get("train 2025H1", {})
    c15 = base.get("cost_r")
    print(f"  {'bars':>5} {'observed cost/R':>17} {'predicted':>11} {'ratio':>8}")
    for bm in BAR_MINUTES:
        m = out.get(f"primary_{bm}", {}).get("train 2025H1", {})
        if not m.get("trades") or not c15:
            continue
        pred = c15 / np.sqrt(bm / 15.0)
        print(f"  {bm:>5} {m['cost_r']:>17.4f} {pred:>11.4f} "
              f"{m['cost_r']/pred:>8.3f}")

    # ---- prediction 2: is gross flat in T? -----------------------------
    print("\n" + "=" * 120)
    print("PREDICTION 2 -- gross should be FLAT in T. This is the real test.")
    print("=" * 120)
    for name in ("train 2025H1", "valid 2025H2"):
        xs, gs = [], []
        for bm in BAR_MINUTES:
            m = out.get(f"primary_{bm}", {}).get(name, {})
            if m.get("trades"):
                xs.append(bm); gs.append(m["gross_r"])
        if len(xs) >= 3:
            rx = pd.Series(xs).rank().to_numpy()
            ry = pd.Series(gs).rank().to_numpy()
            rho = float(np.corrcoef(rx, ry)[0, 1])
            print(f"  {name:>12}  gross by horizon: "
                  + "  ".join(f"{b}m {g:+.4f}" for b, g in zip(xs, gs)))
            print(f"  {'':>12}  rho(gross, bars) = {rho:+.3f}   "
                  + ("DECAYS -- hypothesis false" if rho < -0.5
                     else "flat or rising -- hypothesis survives"))

    # ---- the decision, on train+validation only ------------------------
    print("\n" + "=" * 120)
    print("SELECTION ON VALIDATION (test plays no part)")
    print("=" * 120)
    best, best_net = None, -9e9
    for bm in BAR_MINUTES:
        if bm == 15:
            continue                      # reference cell, not a candidate
        v = out.get(f"primary_{bm}", {}).get("valid 2025H2", {})
        if v.get("trades", 0) >= 30 and net_of(v) > best_net:
            best, best_net = bm, net_of(v)
    if best is None:
        print("  no candidate horizon produced 30+ validation trades.")
    else:
        tr = out[f"primary_{best}"]["train 2025H1"]
        va = out[f"primary_{best}"]["valid 2025H2"]
        sg = out[f"primary_{best}"]["symbol_gross_trainvalid"]
        pos = sum(1 for v in sg.values() if v is not None and v > 0)
        print(f"  best validation net: {best}m bars at {best_net:+.4f}R")
        print(f"    train net {net_of(tr):+.4f}  valid net {net_of(va):+.4f}  "
              f"gross positive on {pos}/4 symbols")

        # 1.5x cost stress, as pre-registered
        _, st = run_cell(data, bar_minutes=best, **PRIMARY, cost_multiplier=1.5)
        sv = window(st, "valid 2025H2")
        print(f"    1.5x cost stress on validation: net {net_of(sv):+.4f}")

        both_pos = net_of(tr) > 0 and net_of(va) > 0
        verdict = ("PROMISING BUT UNPROVEN"
                   if both_pos and pos == 4 and net_of(sv) > 0
                   else "NO ECONOMIC EDGE / NO SIGNAL -- see the rule")
        print(f"\n  PRE-REGISTERED VERDICT: {verdict}")
        out["decision"] = dict(bars=best, train_net=net_of(tr),
                               valid_net=net_of(va), symbols_positive=pos,
                               stress_net=net_of(sv), verdict=verdict)

    (OUT / "hscalp3.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {OUT / 'hscalp3.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
