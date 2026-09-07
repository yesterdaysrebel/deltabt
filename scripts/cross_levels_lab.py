"""cross_levels with Supertrend / ADX / DI, at 1R and 1.5R, windowed, across timeframes.

    PYTHONPATH=. python3 scripts/cross_levels_lab.py

WHY THIS EXISTS
    The deployed arm's entry (`variant_a`) is a floor with no ceiling, so it
    enters at the TOP of the range on 75% of longs and the BOTTOM on 63% of
    shorts. The operator says that is not how they trade. `cross_levels` is
    the rule that matches: a long fires on the bar %R crosses UP through -80,
    leaving oversold; a short mirrors it at -20.

    `cross_levels both 3R` scored +0.305 on 4/4 blocks in the first look, but
    on n=72 and as the best of seven cells. This lab asks whether that
    survives being put on a proper footing: gates (Supertrend, DI, ADX), the
    targets the operator actually takes (1R, 1.5R), the 18-24 UTC window, and
    five primary/confirmation timeframe pairs.

    EVERY CELL IS RUN AS AN ARM -- the engine takes the trades a live bot
    would, one position per symbol -- because a filter applied to another
    arm's trades overstates itself (learned 2026-09-04: a window measured as
    a filter said +0.331, the same window built as an arm said +0.117).

READ THE SELECTION TEST, NOT THE TABLE. With this many cells the best net is
a lottery winner. The out-of-block test picks on blocks < k and scores on
block k, and the premium it reports is how much the table overstates.
"""
from __future__ import annotations

import dataclasses
import itertools
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio

THIN = ["BEATUSD", "AKEUSD", "BANKUSD"]
HOLD = 72
WIN = (18, 24)
MIN_N = 25          # below this a cell is noise; reported, never ranked

cat = ProductCatalog()
LOAD = {s: load_symbol(s) for s in THIN}
FRAMES: dict = {}


def frame(s, m):
    return _resampled(LOAD[s], m, FRAMES)


# anchored blocks come from the 5m frame so every timeframe shares one calendar
_allt = np.concatenate([frame(s, 5)[0]["time"].to_numpy() for s in THIN])
PE = np.linspace(_allt.min(), _allt.max() + 1, 5).astype(np.int64)
DAYS = (_allt.max() - _allt.min()) / 86400

TFS = [(5, 1), (15, 3), (15, 5), (30, 5), (60, 15)]
CONFIRMS = {"x": "cross_levels", "-": "off"}      # %R rule required on confirm
STS = {"-": "off", "ST": "aligned"}
DIS = {"-": False, "DI": True}
ADXS = {"-": None, "A25": 25.0}
TARGETS = [1.0, 1.5, 3.0]
HOURS = {"all": None, "18-24": WIN}


def spec_of(pm, cm, conf, st, di, adx, target, hours):
    s = build_spec("manual_scalp_both", pm, cm, stop_atr_multiplier=4.0, target_r=target)
    prim = replace(s.primary, wpr_rule="cross_levels", supertrend=st, di=di, adx_min=adx)
    if conf == "off":
        cnf = replace(s.confirm, wpr_rule="none", supertrend="off", di=False, adx_min=None)
    else:
        cnf = replace(s.confirm, wpr_rule=conf, supertrend="off", di=False, adx_min=None)
    return replace(s, primary=prim, confirm=cnf, entry_hours_utc=hours)


SIG: dict = {}


def trades(s, spec, pm):
    key = (s, pm, spec.config_hash)
    if key not in SIG:
        C = frame(s, spec.confirm_minutes)[0] if spec.confirm.enabled else None
        SIG[key] = rulecore.to_engine_signals(rulecore.compute(frame(s, pm)[0], C, spec))
    P, mark, tr = frame(s, pm)
    res = run_portfolio({s: Book(symbol=s, bars=P, signals=SIG[key],
                                 costs=SymbolCosts.from_spec(cat.get(s)), mark=mark, tradable=tr)},
                        params_for(spec, pm, HOLD), RiskGates.off(), initial_capital=10_000.0,
                        funding={s: LOAD[s]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if len(d):
        d["symbol"] = s
        d["pblk"] = np.searchsorted(PE[1:-1], d.entry_time, side="right")
    return d


CELLS = list(itertools.product(TFS, CONFIRMS, STS, DIS, ADXS, TARGETS, HOURS))
print(f"{len(CELLS)} cells; thin three, 4xATR, cost gate on, hold {HOLD}h, "
      f"RiskGates off, {DAYS:.0f} days, anchored blocks", file=sys.stderr)

rng = np.random.default_rng(11)
rows, TR = [], {}
for i, ((pm, cm), ck, stk, dik, adxk, tgt, hk) in enumerate(CELLS):
    if i % 60 == 0:
        print(f"  {i}/{len(CELLS)}", file=sys.stderr)
    spec = spec_of(pm, cm, CONFIRMS[ck], STS[stk], DIS[dik], ADXS[adxk], tgt, HOURS[hk])
    parts = [x for x in (trades(s, spec, pm) for s in THIN) if len(x)]
    d = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    gates = "+".join(x for x in (stk, dik, adxk) if x != "-") or "none"
    name = f"{pm}/{cm}m c={ck} {gates:<9} {tgt:g}R {hk}"
    TR[name] = d
    if not len(d):
        rows.append(dict(cell=name, net=np.nan, pos=0, n=0)); continue
    g = d.groupby("pblk").r_multiple.mean()
    dd = float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max())
    bs = np.array([rng.choice(d.r_multiple.to_numpy(), len(d)).mean() for _ in range(2000)])
    sym = {s: d[d.symbol == s].r_multiple.mean() for s in THIN if len(d[d.symbol == s])}
    rows.append(dict(
        cell=name, tf=f"{pm}/{cm}", conf=ck, gates=gates, tgt=tgt, hrs=hk,
        net=d.r_multiple.mean(), pos=int((g > 0).sum()), nblk=int(g.notna().sum()),
        n=len(d), win=(d.r_multiple > 0).mean(), dd=dd, wk=len(d) / (DAYS / 7),
        lo=np.percentile(bs, 2.5), hi=np.percentile(bs, 97.5),
        allsym=bool(sym) and all(v > 0 for v in sym.values()),
        **{f"s_{s[:4]}": sym.get(s, np.nan) for s in THIN},
        **{f"b{j}": g.get(j, np.nan) for j in range(4)}))

R = pd.DataFrame(rows).set_index("cell")
R.to_csv("out/sweep/cross_levels_lab.csv")
OK = R[R.n >= MIN_N].copy()

print(f"\n== 1. EVERY CELL WITH n >= {MIN_N}, best net first "
      f"({len(OK)} of {len(R)} cells cleared the bar)")
print(f"  {'cell':<34}{'net':>8}{'blk':>6}{'n':>6}{'win':>6}{'DD':>7}{'/wk':>6}{'boot 95%':>19}  sym+")
for name, r in OK.sort_values("net", ascending=False).head(25).iterrows():
    print(f"  {name:<34}{r.net:>+8.3f}{int(r.pos)}/{int(r.nblk):<4}{int(r.n):>6}{r.win:>6.0%}"
          f"{r.dd:>7.1f}{r.wk:>6.1f}   [{r.lo:+.3f}, {r.hi:+.3f}]  {'yes' if r.allsym else '-'}")

print(f"\n== 2. WHAT EACH TIMEFRAME IS WORTH (cells with n >= {MIN_N})")
print(f"  {'tf':>8}{'cells':>7}{'median net':>12}{'best net':>10}{'total n':>9}{'median /wk':>12}")
for tf, g in OK.groupby("tf"):
    print(f"  {tf:>8}{len(g):>7}{g.net.median():>+12.3f}{g.net.max():>+10.3f}"
          f"{int(g.n.sum()):>9}{g.wk.median():>12.1f}")
dead = R[R.n < MIN_N].groupby("tf").size() if len(R[R.n < MIN_N]) else None
if dead is not None:
    print(f"  cells below n={MIN_N} by timeframe: {dict(dead)}")

print(f"\n== 3. DOES EACH GATE PAY? (median net over cells with n >= {MIN_N})")
for axis, label in [("gates", "gate set"), ("tgt", "target"), ("hrs", "hours"), ("conf", "confirm %R")]:
    print(f"  by {label}:")
    for k, g in OK.groupby(axis):
        print(f"     {str(k):<12} median {g.net.median():>+7.3f}   best {g.net.max():>+7.3f}   cells {len(g):>3}")

print(f"\n== 4. SELECTION TEST -- pick the best cell on blocks < k, score it on block k")
print(f"   with {len(OK)} live cells the table's best net is a lottery winner; this is the check")
for k in (1, 2, 3):
    best, bv = None, -9e9
    for name in OK.index:
        tr = TR[name]
        t = tr[tr.pblk < k]
        if len(t) < MIN_N:
            continue
        if t.r_multiple.mean() > bv:
            best, bv = name, t.r_multiple.mean()
    if best is None:
        print(f"   k={k}: no cell has {MIN_N} training trades"); continue
    ho = TR[best][TR[best].pblk == k]
    sc = ho.r_multiple.mean() if len(ho) else float("nan")
    print(f"   k={k}: picks {best:<34} train {bv:+.3f} -> block {k} {sc:+.3f} (n={len(ho)})"
          f"   PREMIUM {bv - sc:+.3f}")

print(f"\n== 5. CELLS POSITIVE ON EVERY BLOCK *AND* EVERY SYMBOL (n >= {MIN_N})")
cons = OK[(OK.pos == OK.nblk) & (OK.nblk == 4) & OK.allsym].sort_values("net", ascending=False)
if not len(cons):
    print("   none. No cross_levels cell is positive on all four blocks and all three symbols.")
for name, r in cons.iterrows():
    print(f"   {name:<34}{r.net:>+8.3f}  n={int(r.n):<5} /wk {r.wk:>4.1f}  "
          f"blocks {r.b0:+.2f} {r.b1:+.2f} {r.b2:+.2f} {r.b3:+.2f}")

print(f"\n== 6. THE CURRENT REGIME (block 3) ranked, for cells with >= {MIN_N} trades in it")
b3 = [(n, TR[n][TR[n].pblk == 3]) for n in OK.index]
b3 = sorted([(n, d.r_multiple.mean(), len(d)) for n, d in b3 if len(d) >= MIN_N],
            key=lambda x: -x[1])
for n, v, c in b3[:10]:
    print(f"   {n:<34}{v:>+8.3f}  n={c}")
print(f"\n  incumbent for reference: variant_a both 3R scored +0.140 overall, "
      f"3/4 blocks, n=373, and +0.290 in block 3")
