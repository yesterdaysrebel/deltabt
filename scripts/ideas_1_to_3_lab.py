"""Ideas 1-3 of the 2026-10-12 list, as pre-registered (docs/ideas_1_to_4_prereg.md).

    PYTHONPATH=. .venv/bin/python scripts/ideas_1_to_3_lab.py

1 limit-order entries   (StrategyParams.entry_limit_atr; judged L1 DEV + L2 OLD)
2 structure stop        (leg_extreme vs 4xATR and vs a width-matched ATR stop; judged on OLD)
3 simple splits         (side / weekday / 6h block chosen on DEV, judged on OLD)

Each window is loaded once; DEV runs first because idea 2's width-matched
control and idea 3's subgroups are fixed from it.
"""
import gc, sys
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from deltabt.catalog import build_spec
import labkit as K

PROD = ["BEATUSD", "AKEUSD", "BANKUSD"]
SPEC_LEG = replace(K.SPEC, stop="leg_extreme")
RES = {}


def run_window(win, root, m_atr=None):
    data = K.load_window(root, K.SCREEN)
    print(f"\n==== {win}: {len(data)} coins ====", flush=True)
    mk = K.books(data); lm = K.books(data, maker_entry=True)
    out = {}
    out["base_h"], _ = K.run(mk, data, "hold-to-3R")
    out["base_t"], _ = K.run(mk, data, "trail")
    out["lim0_h"], rj0 = K.run(lm, data, "hold-to-3R", entry_limit_atr=0.0, entry_limit_wait_bars=3)
    out["lim25_h"], rj25 = K.run(lm, data, "hold-to-3R", entry_limit_atr=0.25, entry_limit_wait_bars=3)
    out["lim0_t"], _ = K.run(lm, data, "trail", entry_limit_atr=0.0, entry_limit_wait_bars=3)
    out["rj0"], out["rj25"] = rj0, rj25
    prod = {s: mk[s] for s in PROD if s in mk}
    if prod:
        out["prod_base_h"], _ = K.run(prod, data, "hold-to-3R")
        out["prod_lim0_h"], _ = K.run({s: lm[s] for s in prod}, data, "hold-to-3R", entry_limit_atr=0.0, entry_limit_wait_bars=3)
    del mk, lm; gc.collect()
    leg = K.books(data, spec=SPEC_LEG)
    out["leg_h"], _ = K.run(leg, data, "hold-to-3R", spec=SPEC_LEG)
    out["leg_t"], _ = K.run(leg, data, "trail", spec=SPEC_LEG)
    del leg; gc.collect()
    if m_atr is None:                                        # DEV: fix the width-matched multiplier
        w = lambda t: (abs(t.entry_price - t.stop_price) / t.entry_price).median()
        m_atr = round(float(K.SPEC.stop_atr_multiplier * w(out["leg_h"]) / w(out["base_h"])), 2)
    spec_m = build_spec("manual_scalp_both_t3", 5, stop_atr_multiplier=m_atr)
    am = K.books(data, spec=spec_m)
    out["atrm_h"], _ = K.run(am, data, "hold-to-3R", spec=spec_m)
    out["m_atr"] = m_atr
    del am, data; gc.collect()
    return out


def section1(R):
    print("\n#### IDEA 1 -- limit-order entries (maker fee, fill only when price trades THROUGH the limit, 15 min wait)")
    verdict = []
    for win, o in R.items():
        print(f"\n  {win}")
        rj = o["rj0"]; fills = len(o["lim0_h"]); placed = fills + rj["limit_unfilled"] + rj["limit_full"]
        print(K.fmt("market entry (today)", o["base_h"]))
        print(K.fmt("limit at the close (k 0)", o["lim0_h"], f"  fill rate {100 * fills / max(placed, 1):.0f}%"))
        rj = o["rj25"]; f25 = len(o["lim25_h"]); p25 = f25 + rj["limit_unfilled"] + rj["limit_full"]
        print(K.fmt("limit 0.25 ATR better (reported)", o["lim25_h"], f"  fill rate {100 * f25 / max(p25, 1):.0f}%"))
        p = K.day_boot_p(o["lim0_h"], o["base_h"])
        print(f"    limit (k 0) beats market: day-clustered p {p:.3f}; avg fee {o['base_h'].fees.sum() / max(len(o['base_h']), 1):.2f}$ -> "
              f"{o['lim0_h'].fees.sum() / max(len(o['lim0_h']), 1):.2f}$ per trade")
        print(K.fmt("trail, market (reported)", o["base_t"]))
        print(K.fmt("trail, limit k 0 (reported)", o["lim0_t"]))
        if "prod_base_h" in o:
            print(K.fmt("prod 3, market (reported)", o["prod_base_h"]))
            print(K.fmt("prod 3, limit k 0 (reported)", o["prod_lim0_h"]))
        verdict.append(p <= 0.05)
    print(f"\n  L1 (DEV) {'PASS' if verdict[0] else 'fail'}   L2 (OLD) {'PASS' if verdict[1] else 'fail'}   "
          f"IDEA 1: {'PASS' if all(verdict) else 'FAIL'}")


def section2(R):
    print("\n#### IDEA 2 -- stop on price structure (leg_extreme) instead of 4xATR")
    for win, o in R.items():
        w = lambda t: 100 * (abs(t.entry_price - t.stop_price) / t.entry_price).median()
        print(f"\n  {win}")
        print(K.fmt("4xATR stop (today)", o["base_h"], f"  median stop {w(o['base_h']):.2f}%"))
        print(K.fmt("leg_extreme stop", o["leg_h"], f"  median stop {w(o['leg_h']):.2f}%"))
        print(K.fmt(f"ATR x{o['m_atr']} (width-matched control)", o["atrm_h"], f"  median stop {w(o['atrm_h']):.2f}%"))
        print(K.fmt("leg_extreme, trail (reported)", o["leg_t"]))
        print(f"    leg_extreme beats 4xATR p {K.day_boot_p(o['leg_h'], o['base_h']):.3f}; beats width-matched ATR p {K.day_boot_p(o['leg_h'], o['atrm_h']):.3f}")
    o = R["OLD 2024-06..2025-12"]
    S1 = K.day_boot_p(o["leg_h"], o["base_h"]) <= 0.05; S2 = K.day_boot_p(o["leg_h"], o["atrm_h"]) <= 0.05
    print(f"\n  S1 (OLD, vs 4xATR) {'PASS' if S1 else 'fail'}   S2 (OLD, vs width-matched) {'PASS' if S2 else 'fail'}   "
          f"IDEA 2: {'PASS' if S1 and S2 else 'FAIL'}")


def section3(R):
    print("\n#### IDEA 3 -- simple splits (subgroup chosen on DEV, judged on OLD)")
    dev, old = R["DEV 2025-12..2026-09"]["base_h"].copy(), R["OLD 2024-06..2025-12"]["base_h"].copy()
    for t in (dev, old):
        t["weekday"] = (t.entry_time // 86400 + 4) % 7          # 0 = Monday
        t["block"] = ((t.entry_time % 86400) // 3600) // 6       # 0 = 00-05 UTC ... 3 = 18-23 UTC
    names = {"weekday": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], "block": ["00-06 UTC", "06-12 UTC", "12-18 UTC", "18-24 UTC"],
             "side": {1: "long", -1: "short"}}
    for col in ("side", "weekday", "block"):
        g = dev.groupby(col).r_multiple.agg(["count", "mean"])
        g = g[g["count"] >= 200]
        best = g["mean"].idxmax(); lab = names[col][best] if col != "side" else names["side"][best]
        print(f"\n  {col}: DEV by group " + ", ".join(f"{(names[col][k] if col != 'side' else names['side'][k])} {v['mean']:+.3f}R (n {int(v['count'])})"
                                                for k, v in g.iterrows()))
        sub, rest = old[old[col] == best], old[old[col] != best]
        p = K.day_boot_p(sub, rest)
        ok = sub.r_multiple.mean() > 0 and p <= 0.05
        print(f"    chosen on DEV: {lab}. OLD: {lab} {sub.r_multiple.mean():+.3f}R (n {len(sub)}) vs the rest {rest.r_multiple.mean():+.3f}R "
              f"(n {len(rest)}), p {p:.3f} -> {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    m = None
    for win, root in K.WINDOWS.items():
        RES[win] = run_window(win, root, m_atr=m)
        m = RES[win]["m_atr"]
    section1(RES); section2(RES); section3(RES)
