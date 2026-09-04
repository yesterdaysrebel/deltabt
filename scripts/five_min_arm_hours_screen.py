"""Control for the 18-24 UTC window: the 25 thin perps screened on 2026-09-03.

    PYTHONPATH=. python3 scripts/five_min_arm_hours_screen.py <dir-with-<SYMBOL>.parquet>

The data is NOT in the repository cache. It was pulled into the session
scratchpad on 2026-09-03 for the universe question (memory note
"deltabt-universe-cannot-expand" records the selection rule: BEATUSD's
contract profile, the 20 longest-listed such symbols, plus five chosen on
stop width). It spans ~2025-07 .. 2026-09-04, i.e. it runs three weeks PAST
the archive the window was found on, and none of it was used to find the
window. One pre-declared test: 18-24 UTC against the rest, gross R, gate
off and on.

RESULT 2026-09-04 (out/sweep/five_min_arm_lab/hours_screen25.txt):

    ungated, n=20,967 over 25 symbols, gross by pooled block
      00-06  -0.041 -0.010 +0.008 +0.013  2/4   gross -0.007
      06-12  -0.040 -0.020 -0.141 +0.033  1/4   gross -0.043
      12-18  -0.078 -0.049 -0.012 -0.055  0/4   gross -0.048
      18-24  +0.075 +0.063 +0.046 +0.036  4/4   gross +0.055   win 51%
    window gross > rest on 20/25 symbols, window gross > 0 on 18/25.
    Gated (0.15): 18-24 +0.062 gross, 3/4; every other window negative.

So the window is a property of thin perps on this venue, not of BEATUSD.
NET is still negative on these 25 (-0.025 in the window, gated) because
their 4xATR stops are ~45 bps and cost 0.2-0.35R; the thin three convert it
to net because their stops are 300-450 bps and cost ~0.04R.
"""
import sys
import dataclasses, glob, os, numpy as np, pandas as pd
from dataclasses import replace
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
DATA = sys.argv[1]
files = sorted(glob.glob(f"{DATA}/*.parquet") + glob.glob(f"{DATA}/*/*.parquet"))
files = [f for f in files if not f.endswith("result.parquet")]
cat = ProductCatalog()
# BOTH ARMS, BUILT AS REAL FAMILIES. An earlier version of this script ran
# the all-hours arm and filtered its trades by entry hour afterwards. That
# overstates the window: a trade the all-hours arm is still HOLDING blocks the
# symbol's only position slot, so the windowed arm gets entries the filter can
# never show, and those extra entries are worse. Measure the arm that would
# trade, not a subset of a different arm's trades.
SPECS = {
    "all hours": build_spec("manual_scalp_st_banded", 5, 1,
                            stop_atr_multiplier=4.0, target_r=1.0),
    "18-24 UTC": build_spec("manual_scalp_st_banded_h18_24", 5, 1,
                            stop_atr_multiplier=4.0, target_r=1.0),
}
rows = []
for f in files:
    sym = os.path.basename(f)[:-8]
    P = pd.read_parquet(f)
    if "time" not in P.columns: P = P.reset_index()
    if "time" not in P.columns: print("skip", sym, P.columns.tolist()[:6]); continue
    P = P.sort_values("time").reset_index(drop=True)
    P = P[P["time"] >= P["time"].max() - 220 * 86400].reset_index(drop=True)  # same span as BEATUSD
    try: costs = SymbolCosts.from_spec(cat.get(sym))
    except Exception as e: print("skip", sym, e); continue
    for arm, spec in SPECS.items():
        sig = rulecore.to_engine_signals(rulecore.compute(P, None, spec))
        for gate, label in ((None, "ungated"), (0.15, "gated")):
            res = run_portfolio({sym: Book(symbol=sym, bars=P, signals=sig, costs=costs)},
                                replace(params_for(spec, 5, 24), max_cost_per_r=gate), RiskGates.off(), initial_capital=10_000.0)
            d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
            if d.empty: continue
            d["gross"] = d.r_multiple + d.cost_per_r; d["hour"] = (d.entry_time % 86400) // 3600; d["h6"] = d.hour // 6 * 6
            d["symbol"] = sym; d["gate"] = label; d["arm"] = arm
            rows.append(d)
D = pd.concat(rows, ignore_index=True)
t = D.entry_time; E = np.linspace(t.min(), t.max() + 1, 5).astype(np.int64); D["pblk"] = np.searchsorted(E[1:-1], t, side="right")
for label in ("ungated", "gated"):
    print(f"\n== 25 screened thin perps, {label}: the windowed arm against the all-hours arm")
    print(f"  {'arm':<12}{'gross by pooled block':>44}    gross     net   win      n  symbols")
    for arm in SPECS:
        d = D[(D.gate == label) & (D.arm == arm)]
        g = d.groupby("pblk").gross.mean(); n = d.groupby("pblk").size()
        print(f"  {arm:<12}" + "".join(f" {g.get(b, np.nan):+.3f}({n.get(b, 0):>4})" for b in range(4))
              + f"  {int((g > 0).sum())}/4  {d.gross.mean():+.3f}  {d.r_multiple.mean():+.3f}  {(d.r_multiple > 0).mean():>3.0%}  {len(d):>5}  {d.symbol.nunique()}")
    a = D[(D.gate == label) & (D.arm == "all hours")].groupby("symbol").gross.mean()
    w = D[(D.gate == label) & (D.arm == "18-24 UTC")].groupby("symbol").gross.mean()
    both = pd.concat([a.rename("all"), w.rename("win")], axis=1).dropna()
    print(f"  per symbol: windowed gross > all-hours on {(both.win > both['all']).sum()}/{len(both)}; "
          f"windowed gross > 0 on {(both.win > 0).sum()}/{len(both)}")
    print("  " + "  ".join(f"{s}:{r.win:+.2f}/{r['all']:+.2f}" for s, r in both.iterrows()))
