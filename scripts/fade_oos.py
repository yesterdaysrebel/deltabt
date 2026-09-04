"""TRUE out-of-sample for manual_scalp_st_banded_fade on the majors, 2026-08-12 -> now.

The archive every sweep used ends 2026-08-12. This pulls Delta 1m from ten
days before that (indicator warm-up), buckets to 5m on Delta's own integer
times, and runs the live family and its fade on bars at or after the cut.
The numbers are recorded beside the family in deltabt/catalog.py.

    PYTHONPATH=. python3 scripts/fade_oos.py
"""
import time, dataclasses, numpy as np, pandas as pd
from deltabt.data.client import DeltaClient
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import params_for
from deltabt.metrics import compute
from deltabt.portfolio import Book, RiskGates, run_portfolio
cli = DeltaClient(); now = int(time.time()); OOS = int(pd.Timestamp("2026-08-12", tz="UTC").timestamp())
SYMS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]; cat = ProductCatalog()
def five(sym):
    raw = pd.DataFrame(cli.candles(sym, "1m", OOS - 10*86400, now)).sort_values("time").reset_index(drop=True)
    m = raw[["time","open","high","low","close","volume"]].copy()
    for c in ("open","high","low","close","volume"): m[c] = m[c].astype(float)
    m["time"] = m["time"].astype("int64"); b = (m["time"]//300)*300
    p = m.groupby(b).agg(open=("open","first"),high=("high","max"),low=("low","min"),close=("close","last"),volume=("volume","sum")).reset_index()
    p = p.rename(columns={p.columns[0]:"time"}); p["time"] = p["time"].astype("int64")
    return p, int((p.time.diff().dropna()!=300).sum())
frames = {}
for s in SYMS:
    p, gaps = five(s); frames[s] = p; print(f"  {s}: {len(p)} 5m bars, {gaps} gaps, {pd.Timestamp(p.time.iloc[0],unit='s').date()} -> {pd.Timestamp(p.time.iloc[-1],unit='s'):%m-%d %H:%M}")
def run(sym, family, stop, tr, hold):
    spec = build_spec(family, 5, 1, stop_atr_multiplier=stop, target_r=tr); p = frames[sym]; t5 = p["time"].to_numpy()
    sig = rulecore.to_engine_signals(rulecore.compute(p, None, spec)); inb = t5 >= OOS
    sig = dataclasses.replace(sig, long_entry=sig.long_entry & inb, short_entry=sig.short_entry & inb)
    res = run_portfolio({sym: Book(symbol=sym, bars=p, signals=sig, costs=SymbolCosts.from_spec(cat.get(sym)), mark=p, tradable=np.ones(len(p), bool))}, params_for(spec, 5, hold), RiskGates.off(), initial_capital=10_000.0)
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades]); m = dataclasses.asdict(compute(res)); return d, m
print(f"\nTRUE OOS majors 2026-08-12 -> {pd.Timestamp(now,unit='s'):%m-%d %H:%M}Z  (mark = bar OHLC; stop fills approximate)")
print(f"  {'symbol':<8}{'cell':<22}{'n':>5}{'win':>6}{'gross':>8}{'net':>8}{'net [lo,hi]':>20}{'return':>9}")
CELLS = [("manual_scalp_st_banded", 4.0, 1.0, 24, "live 4x 1R 24h"), ("manual_scalp_st_banded_fade", 4.0, 1.0, 24, "FADE 4x 1R 24h (primary)"), ("manual_scalp_st_banded_fade", 8.0, 2.0, 48, "FADE 8x 2R 48h"), ("manual_scalp_st_banded_fade", 4.0, 2.0, 48, "FADE 4x 2R 48h (sweep pick)")]
pooled = {c[4]: [] for c in CELLS}
for sym in SYMS:
    for fam, st, tr, hd, label in CELLS:
        d, m = run(sym, fam, st, tr, hd); pooled[label].append(d)
        g = (d.r_multiple + d.cost_per_r).mean() if len(d) else np.nan
        print(f"  {sym:<8}{label:<22}{m['trades']:>5}{m['win_rate']:>6.0%}{g:>+8.3f}{m['expectancy_r']:>+8.3f}   [{m['expectancy_r_lo']:+.3f},{m['expectancy_r_hi']:+.3f}]{m['return_pct']:>+8.1f}%")
print("\n  POOLED (trade-weighted):")
for label, ds in pooled.items():
    d = pd.concat(ds, ignore_index=True); g = d.r_multiple + d.cost_per_r
    bs = [np.random.default_rng(i).choice(d.r_multiple.to_numpy(), len(d)).mean() for i in range(400)]
    print(f"  {'':<8}{label:<22}{len(d):>5}{(d.r_multiple>0).mean():>6.0%}{g.mean():>+8.3f}{d.r_multiple.mean():>+8.3f}   [{np.percentile(bs,2.5):+.3f},{np.percentile(bs,97.5):+.3f}]")
