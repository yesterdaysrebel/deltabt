"""``manual_scalp_st_banded_fade`` on the majors: pre-declared cell, one sweep, and the selection premium.

    PYTHONPATH=. python3 scripts/fade_walkforward.py BTCUSD,ETHUSD,SOLUSD,XRPUSD

THE PRIMARY RESULT IS ONE CELL, DECLARED BEFORE THE SWEEP RAN: fade@5m, 4xATR
stop, 1R target, 24h hold -- the live family's own settings with the side
swapped. It was chosen on the horizon test (forward move after the live
signal is negative at 12-96 bars), not on any backtest number, so its
anchored blocks are an honest test of that observation.

THE SWEEP IS SECONDARY AND IS SCORED AS A PROCEDURE. Stop {4,6,8} x target
{1,1.5,2} x hold {24,48} is 18 cells; the best of 18 on the past is expected
to look good on the past. So for each anchored split the best cell on the
training blocks (pooled across symbols, by gross R) is chosen and measured on
the next block it did not see. ``selection premium`` = training score of the
chosen cell minus its out-of-sample score, averaged over splits. Any cell
whose in-sample gross does not exceed the premium is not distinguishable
from the act of choosing.

Blocks are anchored quarters of the archive by time; trades are assigned to
the block of their ENTRY, so one full-period run per cell is enough and no
position is cut at a boundary.
"""
import sys, dataclasses, itertools, numpy as np, pandas as pd
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio

SYMS = sys.argv[1].split(",") if len(sys.argv) > 1 else ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
PRIMARY = ("manual_scalp_st_banded_fade", 4.0, 1.0, 24)
GRID = [("manual_scalp_st_banded_fade", s, t, h) for s, t, h in itertools.product((4.0, 6.0, 8.0), (1.0, 1.5, 2.0), (24, 48))]
cat = ProductCatalog(); LOADED = {s: load_symbol(s) for s in SYMS}
FR = {s: _resampled(d, 5, {}) for s, d in LOADED.items()}
allt = np.concatenate([FR[s][0]["time"].to_numpy() for s in SYMS]); EDGES = np.linspace(allt.min(), allt.max() + 1, 5).astype(np.int64)

def trades(sym, family, stop, tr, hold) -> pd.DataFrame:
    P, mark, tradable = FR[sym]; spec = build_spec(family, 5, 1, stop_atr_multiplier=stop, target_r=tr)
    sig = rulecore.to_engine_signals(rulecore.compute(P, None, spec)); costs = SymbolCosts.from_spec(cat.get(sym))
    res = run_portfolio({sym: Book(symbol=sym, bars=P, signals=sig, costs=costs, mark=mark, tradable=tradable)},
                        params_for(spec, 5, hold), RiskGates.off(), initial_capital=10_000.0, funding={sym: LOADED[sym]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if d.empty: return d
    d["gross"] = d.r_multiple + d.cost_per_r; d["blk"] = np.searchsorted(EDGES[1:-1], d.entry_time.to_numpy(), side="right"); d["symbol"] = sym
    return d

def summarise(d: pd.DataFrame) -> str:
    if d.empty: return "no trades"
    g = d.groupby("blk").gross.mean(); n = d.groupby("blk").gross.size(); pos = int((g > 0).sum())
    return ("".join(f"  {g.get(b, np.nan):+.3f}({n.get(b, 0):>4})" for b in range(4))
            + f"  {pos}/4  gross {d.gross.mean():+.3f}  net {d.r_multiple.mean():+.3f}  win {(d.r_multiple > 0).mean():.0%}  n={len(d)}")

print(f"archive blocks: " + "  ".join(f"blk{i} {pd.Timestamp(EDGES[i], unit='s').date()}" for i in range(4)))
print("\n== PRIMARY, declared before the sweep: fade@5 4x 1R 24h   (live family alongside, same bars, opposite side)")
print(f"  {'':<8}{'':>2}" + "".join(f"{'blk' + str(i):>13}" for i in range(4)))
prim = {}
for sym in SYMS:
    live = trades(sym, "manual_scalp_st_banded", 4.0, 1.0, 24); fade = trades(sym, *PRIMARY)
    prim[sym] = fade
    print(f"  {sym:<8} live" + summarise(live)); print(f"  {sym:<8} FADE" + summarise(fade))
pool = pd.concat(prim.values(), ignore_index=True); print(f"  {'POOLED':<8} FADE" + summarise(pool))

print("\n== SWEEP (in-sample, full period, pooled across symbols) -- read with the premium below")
cells = {}
for c in GRID:
    cells[c] = pd.concat([trades(s, *c) for s in SYMS], ignore_index=True)
    d = cells[c]; g = d.groupby("blk").gross.mean()
    print(f"  {c[0][-4:]} stop {c[1]:.0f}x  target {c[2]:.1f}R  hold {c[3]:>2}h " + "".join(f"  {g.get(b, np.nan):+.3f}" for b in range(4)) + f"  {int((g > 0).sum())}/4  gross {d.gross.mean():+.3f}  net {d.r_multiple.mean():+.3f}  n={len(d)}")

print("\n== SELECTION TEST: best-of-18 on training blocks, measured on the next block")
prem = []
for k in (1, 2, 3):
    def score(d, blocks, col): x = d[d.blk.isin(blocks)]; return x[col].mean() if len(x) >= 40 else -np.inf
    for col in ("gross", "r_multiple"):
        best = max(cells, key=lambda c: score(cells[c], range(k), col)); tr = score(cells[best], range(k), col)
        oos = cells[best][cells[best].blk == k]; o = oos[col].mean() if len(oos) >= 10 else np.nan
        if col == "gross": prem.append(tr - o)
        print(f"  train blk0..{k-1} by {'GROSS' if col=='gross' else 'NET  '}: chose stop {best[1]:.0f}x target {best[2]:.1f}R hold {best[3]}h  train {tr:+.3f}  ->  blk{k} {o:+.3f} (n={len(oos)})")
print(f"\n  selection premium (gross, mean over 3 splits): {np.nanmean(prem):+.3f}")
print(f"  primary cell in-sample gross {pool.gross.mean():+.3f}: {'exceeds' if pool.gross.mean() > np.nanmean(prem) else 'DOES NOT exceed'} the premium -- but the primary was not selected, so the premium applies to the sweep, not to it")
