"""Lab for the 5-minute arm the paper trader ran before decision C.

    PYTHONPATH=. python3 scripts/five_min_arm_lab.py [thin|majors|all] [family]

``family`` defaults to manual_scalp_st_banded (the arm below). It also runs
on the operator's original encoding, ``manual_scalp`` (%R alone, no
Supertrend gate), and on ``manual_scalp_both`` (%R on 5m AND 1m).

The arm is `manual_scalp_st_banded@5`: 5m Supertrend aligned + %R banded
(rising, lower half of the 140-bar range), edge trigger, 4xATR stop. It ran
live as MANUAL-STB-5M-PAPER-20260901-1 (seven symbols) and
MANUAL-THIN3-5M-PAPER-20260902-1 (thin three) before the direction filter
moved to the 1h chart.

WHAT WAS ALREADY MEASURED ON IT, AND IS NOT REPEATED HERE
    majors    entry features (session, weekday, vol regime, %R depth/slope,
              funding, momentum, volume) all flip sign between halves; the
              post-entry path is uninformative (breakeven/trailing/targets
              all leave gross ~0); age>=2, maker entry, 6x/8x stops, 48h
              hold reduce the loss and never flip the sign.
    thin 3    1h direction (adopted), %R hold filters (worse), price
              confirmation (not adopted), target/hold surface (3R/72h
              adopted), stop width (4x is the peak), age>=2 (1/4 blocks),
              band floor -70 (noise), setup-failure exit (harmful live).

WHAT THIS SCRIPT MEASURES, none of it measured on the thin three before
    A  baseline at the exit it ran (1R/24h) and the exit now live (3R/72h)
    B  exits the engine supports natively and were never switched on for
       this family: adverse-R early exit, 5m Supertrend flip exit
    C  the post-entry PATH: MFE/MAE per trade, breakeven and trailing
       stops replayed on the engine's own (gated) trade list
    D  the indicator-parameter neighbourhood of the live cell: Supertrend
       multiplier and period, %R period, band edges. A plateau is evidence
       the cell is not a fluke; a spike is evidence it is
    E  long against short
    F  entry features on BEATUSD, the only thin symbol with real history

Every cell runs through deltabt.rulecore and deltabt.portfolio.run_portfolio
with RiskGates.off() and the cost gate ON, i.e. the population the sweeps
measure. Blocks are anchored quarters of each symbol's OWN span, so
"n/4" on AKEUSD or BANKUSD means four five-day windows (see the memory note
on thin-3 history lengths); pooled lines use the union span, where every
AKEUSD/BANKUSD trade lands in the last block.

RESULTS 2026-09-04 (out/sweep/five_min_arm_lab/thin.txt, majors.txt)

    A  thin three 4x/1R/24h net -0.009 (2/4, n=591); 4x/3R/72h +0.021 (1/4,
       n=296, block 3 carries it). Majors -0.107 and -0.083.
    B  every engine exit is worse on both universes and both exits. The 5m
       Supertrend flip exit is the worst thing tried: -0.066 / -0.070 on the
       thin three, 0/4 blocks, drawdown doubles. adverse_r 0.25-0.75 all
       negative. SETTLED: no early exit.
    C  at 1R the path is uninformative (as on the majors): 0% of stop-outs
       ever reached +1R. At 3R, 33% of stop-outs reached +1R first, but
       breakeven is worse at every level and trailing is +0.03 with a paired
       bootstrap P(diff>0)=0.63. Nothing.
    D  1R/24h: a flat plateau of ~0 (-0.04..+0.00 over 16 neighbours;
       premium +0.120). 3R/72h: the live cell (+0.021) is the BEST of its 16
       neighbours and 11 of them are negative -- a spike, premium +0.303. The
       3R/72h exit on the 5m arm rests on exact indicator parameters. (The
       live 1h arm's neighbourhood, checked the same day, is a plateau: 15 of
       16 cells positive, 6 of them 4/4.)
    E  longs beat shorts on every thin symbol, but per block it tracks the
       symbol's drift on AKEUSD/BANKUSD and not even that on BEATUSD. Not a
       feature.
    F  on BEATUSD the features that agree in both halves on BOTH exits are:
       entry 18-24 UTC positive, funding < 0 negative, leg age 12+ negative.
       The hour survived every control -- see scripts/five_min_arm_hours.py
       and five_min_arm_hours_screen.py -- but measured AS AN ARM rather than
       as a filter it is +0.117, not +0.331
       (scripts/five_min_arm_hours_as_traded.py). Funding sign does not
       survive: on the live 1h arm at 1R funding < 0 is the BETTER half.
"""
from __future__ import annotations

import dataclasses
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

FAMILY = sys.argv[2] if len(sys.argv) > 2 else "manual_scalp_st_banded"
THIN = ["BEATUSD", "AKEUSD", "BANKUSD"]
MAJORS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
which = sys.argv[1] if len(sys.argv) > 1 else "thin"
SYMS = {"thin": THIN, "majors": MAJORS, "all": THIN + MAJORS}[which]

cat = ProductCatalog()
LOAD = {s: load_symbol(s) for s in SYMS}
LOAD = {s: d for s, d in LOAD.items() if d is not None}
FR = {s: _resampled(LOAD[s], 5, {}) for s in LOAD}
EDGES = {s: np.linspace(FR[s][0]["time"].min(), FR[s][0]["time"].max() + 1, 5).astype(np.int64)
         for s in FR}
allt = np.concatenate([FR[s][0]["time"].to_numpy() for s in FR])
POOLED = np.linspace(allt.min(), allt.max() + 1, 5).astype(np.int64)

LIVE_EXIT = dict(target=1.0, hold=24)
NOW_EXIT = dict(target=3.0, hold=72)


def spec_for(stop=4.0, target=1.0, st_mult=2.0, st_period=10, wpr_period=140,
             band=(-80.0, -20.0)):
    spec = build_spec(FAMILY, 5, 1, stop_atr_multiplier=stop, target_r=target)
    spec = replace(spec, st_multiplier=st_mult, st_atr_period=st_period,
                   wpr_period=wpr_period,
                   primary=replace(spec.primary, wpr_long_level=band[0],
                                   wpr_short_level=band[1]),
                   confirm=replace(spec.confirm, wpr_long_level=band[0],
                                   wpr_short_level=band[1]))
    return spec


_CF = {}


def confirm_frame(sym, spec):
    """The confirmation frame a family needs, or None when its gate is off."""
    if not spec.confirm.enabled:
        return None
    key = (sym, spec.confirm_minutes)
    if key not in _CF:
        _CF[key] = _resampled(LOAD[sym], spec.confirm_minutes, {})[0]
    return _CF[key]


_SIG = {}


def signals(sym, spec):
    key = (sym, spec.config_hash)
    if key not in _SIG:
        _SIG[key] = rulecore.compute(FR[sym][0], confirm_frame(sym, spec), spec)
    return _SIG[key]


def trades(sym, spec, hold, **params):
    P, mark, tradable = FR[sym]
    sig = signals(sym, spec)
    prm = replace(params_for(spec, 5, hold), **params)
    res = run_portfolio(
        {sym: Book(symbol=sym, bars=P, signals=rulecore.to_engine_signals(sig),
                   costs=SymbolCosts.from_spec(cat.get(sym)), mark=mark, tradable=tradable)},
        prm, RiskGates.off(), initial_capital=10_000.0, funding={sym: LOAD[sym]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if d.empty:
        return d
    d["gross"] = d.r_multiple + d.cost_per_r
    d["blk"] = np.searchsorted(EDGES[sym][1:-1], d.entry_time.to_numpy(), side="right")
    d["pblk"] = np.searchsorted(POOLED[1:-1], d.entry_time.to_numpy(), side="right")
    d["symbol"] = sym
    return d


def pool(spec, hold, syms=None, **params):
    fs = [trades(s, spec, hold, **params) for s in (syms or FR)]
    fs = [f for f in fs if len(f)]
    return pd.concat(fs, ignore_index=True) if fs else pd.DataFrame()


def blocks(d, col="pblk"):
    if d.empty:
        return "no trades"
    g = d.groupby(col).r_multiple.mean()
    n = d.groupby(col).size()
    return ("".join(f" {g.get(b, np.nan):+.3f}({n.get(b, 0):>3})" for b in range(4))
            + f"  {int((g > 0).sum())}/4")


def line(name, d, col="pblk", extra=""):
    if d.empty:
        print(f"  {name:<34} no trades")
        return
    dd = float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max())
    print(f"  {name:<34}{blocks(d, col)}  net {d.r_multiple.mean():+.3f}"
          f"  gross {d.gross.mean():+.3f}  win {(d.r_multiple > 0).mean():>3.0%}"
          f"  DD {dd:>5.1f}R  n={len(d):<5}{extra}")


def per_symbol(name, spec, hold, **params):
    for s in FR:
        line(f"  {s} {name}", trades(s, spec, hold, **params), col="blk")


HEAD = f"  {'variant':<34}{'blk0':>11}{'blk1':>11}{'blk2':>11}{'blk3':>11}"
print(f"family {FAMILY}   universe {list(FR)}   pooled blocks: "
      + "  ".join(f"b{i} {pd.Timestamp(POOLED[i], unit='s').date()}" for i in range(4)))
for s in FR:
    P = FR[s][0]
    print(f"  {s:<9} {len(P):>6} 5m bars  {pd.Timestamp(P.time.min(), unit='s').date()} .. "
          f"{pd.Timestamp(P.time.max(), unit='s').date()}  own blocks every "
          f"{(P.time.max() - P.time.min()) / 86400 / 4:.0f} days")

# ---------------------------------------------------------------- A: baseline
print("\n== A. BASELINE  (4xATR, cost gate on, RiskGates off)")
print(HEAD)
S1 = spec_for(target=1.0)
S3 = spec_for(target=3.0)
line("1R / 24h  (as it ran live)", pool(S1, 24))
line("3R / 72h  (the exit now live)", pool(S3, 72))
per_symbol("1R/24h", S1, 24)
per_symbol("3R/72h", S3, 72)

# ---------------------------------------------------------------- B: engine exits
print("\n== B. EXITS THE ENGINE SUPPORTS, never switched on for this family")
print("  (closed-bar conditions, checked AFTER the resting stop and target;")
print("   adverse_r x = close at x of the planned loss; flip = 5m Supertrend turns against)")
for label, spec, hold in (("1R/24h", S1, 24), ("3R/72h", S3, 72)):
    print(f"  -- on {label}")
    print(HEAD)
    line("as is", pool(spec, hold))
    for a in (0.25, 0.5, 0.75):
        line(f"adverse_r {a}", pool(spec, hold, exit_at_adverse_r=a))
    line("ST flip exit", pool(spec, hold, exit_on_trend_flip=True))
    line("ST flip + adverse_r 0.5", pool(spec, hold, exit_on_trend_flip=True, exit_at_adverse_r=0.5))
print("  per symbol, 3R/72h:")
for s in FR:
    line(f"  {s} as is", trades(s, S3, 72), col="blk")
    line(f"  {s} ST flip exit", trades(s, S3, 72, exit_on_trend_flip=True), col="blk")
    line(f"  {s} adverse_r 0.5", trades(s, S3, 72, exit_at_adverse_r=0.5), col="blk")

# ---------------------------------------------------------------- C: path replay
print("\n== C. POST-ENTRY PATH, replayed on the engine's own trade list")
print("  Entry set is FIXED (the engine's, gated). An exit that closes earlier could")
print("  free the slot for an entry the engine did not take; that is not modelled,")
print("  so treat these as the path's shape, not as a backtest. Net R here is")
print("  path R minus the trade's cost_per_r (taker both legs), for every variant")
print("  including 'as is', so the comparison is like for like.")


def replay(sym, d, hold_bars, target_r, variant):
    """Replay every trade in ``d`` from the bar after entry on mark high/low."""
    P, mark, _ = FR[sym]
    t = P["time"].to_numpy("int64")
    close = P["close"].to_numpy("float64")
    m = mark.set_index("time").reindex(t) if mark is not None else P.set_index("time")
    mh = np.where(np.isfinite(m["high"].to_numpy()), m["high"].to_numpy(), P["high"].to_numpy())
    ml = np.where(np.isfinite(m["low"].to_numpy()), m["low"].to_numpy(), P["low"].to_numpy())
    atr = signals(sym, S1).atr
    out = []
    for tr in d.itertuples():
        i0 = int(np.searchsorted(t, tr.entry_time))
        side, e, rpu = tr.side, tr.entry_price, tr.risk_per_unit
        stop = tr.stop_price
        target = e + side * target_r * rpu if target_r else None
        a0 = atr[i0] if np.isfinite(atr[i0]) else rpu / 4.0
        mfe = mae = 0.0
        best = e
        r = None
        reason = "hold"
        last = min(i0 + hold_bars, len(t) - 1)
        for i in range(i0 + 1, last + 1):
            hi, lo, c = mh[i], ml[i], close[i]
            fav = (hi - e) * side / rpu if side > 0 else (e - lo) / rpu
            adv = (e - lo) / rpu if side > 0 else (hi - e) / rpu
            mfe, mae = max(mfe, fav), max(mae, adv)
            # resting orders first, stop before target on the same bar
            hit_stop = lo <= stop if side > 0 else hi >= stop
            hit_tgt = target is not None and (hi >= target if side > 0 else lo <= target)
            if hit_stop:
                r, reason = side * (stop - e) / rpu, "stop"
                break
            if hit_tgt:
                r, reason = target_r, "target"
                break
            # then the variant's stop management, on the closed bar
            best = max(best, c) if side > 0 else min(best, c)
            unreal = side * (c - e) / rpu
            kind, k = variant
            if kind == "breakeven" and unreal >= k:
                stop = max(stop, e) if side > 0 else min(stop, e)
            elif kind == "trail" and unreal >= 1.0:
                cand = best - side * k * a0
                stop = max(stop, cand) if side > 0 else min(stop, cand)
            elif kind == "trail_only" and unreal >= 1.0:
                cand = best - side * k * a0
                stop = max(stop, cand) if side > 0 else min(stop, cand)
        if r is None:
            r = side * (close[last] - e) / rpu
        out.append(dict(symbol=sym, r=r - tr.cost_per_r, path=r, mfe=mfe, mae=mae,
                        reason=reason, blk=tr.blk, pblk=tr.pblk, side=side))
    return pd.DataFrame(out)


def replay_pool(dfs, hold_bars, target_r, variant):
    parts = [replay(s, d, hold_bars, target_r, variant) for s, d in dfs.items() if len(d)]
    return pd.concat(parts, ignore_index=True)


def rline(name, x):
    g = x.groupby("pblk").r.mean()
    n = x.groupby("pblk").size()
    print(f"  {name:<34}" + "".join(f" {g.get(b, np.nan):+.3f}({n.get(b, 0):>3})" for b in range(4))
          + f"  {int((g > 0).sum())}/4  net {x.r.mean():+.3f}  win {(x.r > 0).mean():>3.0%}"
          f"  exits {x.reason.value_counts().to_dict()}")


for label, spec, hold, tgt in (("1R/24h", S1, 24, 1.0), ("3R/72h", S3, 72, 3.0)):
    dfs = {s: trades(s, spec, hold) for s in FR}
    hb = hold * 12
    base = replay_pool(dfs, hb, tgt, ("none", 0))
    print(f"\n  -- {label}: engine net {pd.concat(dfs.values()).r_multiple.mean():+.3f}, "
          f"replay of the same trades {base.r.mean():+.3f} (difference = maker exit on targets + funding)")
    st = base[base.reason == "stop"]
    tg = base[base.reason == "target"]
    print(f"  stop-outs n={len(st)}: reached +0.5R first {(st.mfe >= 0.5).mean():.0%}, "
          f"+1R {(st.mfe >= 1.0).mean():.0%}, +1.5R {(st.mfe >= 1.5).mean():.0%}, +2R {(st.mfe >= 2.0).mean():.0%}")
    if len(tg):
        print(f"  target hits n={len(tg)}: went -0.5R first {(tg.mae >= 0.5).mean():.0%}, "
              f"-0.75R {(tg.mae >= 0.75).mean():.0%}")
    hd = base[base.reason == "hold"]
    if len(hd):
        print(f"  time-outs n={len(hd)}: mean path {hd.path.mean():+.2f}R, mean MFE {hd.mfe.mean():.2f}R")
    print(HEAD)
    rline("as is (replay)", base)
    for k in (0.5, 1.0, 1.5, 2.0):
        if k < tgt:
            rline(f"breakeven once +{k}R", replay_pool(dfs, hb, tgt, ("breakeven", k)))
    for k in (2.0, 3.0, 4.0):
        rline(f"trail {k}xATR after +1R, keep target", replay_pool(dfs, hb, tgt, ("trail", k)))
    for k in (2.0, 3.0, 4.0):
        rline(f"trail {k}xATR after +1R, NO target", replay_pool(dfs, hb, None, ("trail_only", k)))

# ---------------------------------------------------------------- D: parameter map
print("\n== D. INDICATOR-PARAMETER NEIGHBOURHOOD of the live cell")
print("  live cell: Supertrend (2.0, 10), %R 140, band -80/-20. One knob at a time.")
print("  A cell that only works at exactly these values is a fluke; a plateau is not.")
cells = {}
for label, spec, hold in (("1R/24h", S1, 24), ("3R/72h", S3, 72)):
    print(f"  -- on {label}")
    print(HEAD)
    tgt = spec.target_r
    grid = [("live", dict())]
    grid += [(f"ST mult {m}", dict(st_mult=m)) for m in (1.5, 2.5, 3.0, 4.0)]
    grid += [(f"ST period {p}", dict(st_period=p)) for p in (7, 14, 20, 30)]
    grid += [(f"%R period {w}", dict(wpr_period=w)) for w in (70, 100, 200, 280)]
    grid += [(f"band {b[0]:.0f}/{b[1]:.0f}", dict(band=b)) for b in ((-90.0, -10.0), (-70.0, -30.0), (-85.0, -25.0))]
    for name, kw in grid:
        d = pool(spec_for(target=tgt, **kw), hold)
        cells[(label, name)] = d
        line(name, d)
    # selection premium over this one-at-a-time set
    prem = []
    for k in (1, 2, 3):
        best, bv = None, -9
        for name, _ in grid:
            d = cells[(label, name)]
            tr = d[d.pblk < k].r_multiple.mean() if len(d) else np.nan
            if np.isfinite(tr) and tr > bv:
                best, bv = name, tr
        d = cells[(label, best)]
        oos = d[d.pblk == k].r_multiple.mean()
        prem.append(bv - oos)
        print(f"     select on blocks<{k}: best '{best}' train {bv:+.3f} -> block {k} {oos:+.3f}")
    print(f"     selection premium {np.mean(prem):+.3f}  (any in-sample gain below this is the act of choosing)")

print("  -- Supertrend multiplier x period, 1R/24h, net (n)")
periods = (7, 10, 14, 20)
print("  " + " " * 10 + "".join(f"{'p' + str(p):>14}" for p in periods))
for m in (1.5, 2.0, 2.5, 3.0, 4.0):
    row = f"  mult {m:<4}"
    for p in periods:
        d = pool(spec_for(target=1.0, st_mult=m, st_period=p), 24)
        row += f"  {d.r_multiple.mean():+.3f}({len(d):>4})" if len(d) else f"  {'--':>12}"
    print(row)

# ---------------------------------------------------------------- E: sides
print("\n== E. LONG against SHORT")
for label, spec, hold in (("1R/24h", S1, 24), ("3R/72h", S3, 72)):
    print(f"  -- on {label}")
    print(HEAD)
    d = pool(spec, hold)
    line("longs", d[d.side > 0])
    line("shorts", d[d.side < 0])
    for s in FR:
        x = d[d.symbol == s]
        line(f"  {s} longs", x[x.side > 0], col="blk")
        line(f"  {s} shorts", x[x.side < 0], col="blk")

# ---------------------------------------------------------------- F: entry features on BEATUSD
if "BEATUSD" in FR:
    print("\n== F. ENTRY FEATURES on BEATUSD (its own two halves; a feature is only")
    print("      worth anything if the sign agrees in BOTH halves)")
    sym = "BEATUSD"
    P = FR[sym][0]
    t = P["time"].to_numpy("int64")
    pi = signals(sym, S1).primary
    atr = pi.atr
    # causal ATR percentile over a trailing week (2016 five-minute bars)
    s_atr = pd.Series(atr)
    atr_pct = s_atr.rolling(2016, min_periods=288).rank(pct=True).to_numpy()
    leg_age = np.arange(len(t)) - pi.leg_start
    fund = LOAD[sym]["funding"]
    ft, fr = fund["time"].to_numpy("int64"), fund["close"].to_numpy("float64")
    hours = ((t % 86400) // 3600).astype(int)
    dow = pd.to_datetime(t, unit="s").dayofweek
    half = t[len(t) // 2]
    for label, spec, hold in (("1R/24h", S1, 24), ("3R/72h", S3, 72)):
        d = trades(sym, spec, hold)
        i = np.searchsorted(t, d.entry_time.to_numpy())
        d = d.assign(
            hour=hours[i] // 6 * 6,
            weekend=dow[i] >= 5,
            vol=pd.cut(atr_pct[i], [0, 1 / 3, 2 / 3, 1.0001], labels=["low", "mid", "high"]),
            dist=pd.qcut(np.abs(P["close"].to_numpy()[i] - pi.st[i]) / atr[i], 3, labels=["near", "mid", "far"]),
            age=pd.cut(leg_age[i], [-1, 0, 3, 11, 10 ** 9], labels=["0", "1-3", "4-11", "12+"]),
            fund=np.where(fr[np.clip(np.searchsorted(ft, d.entry_time.to_numpy(), side="right") - 1, 0, len(fr) - 1)] >= 0, "f>=0", "f<0"),
            h=np.where(d.entry_time.to_numpy() < half, "H1", "H2"),
        )
        print(f"  -- on {label}: n={len(d)}  net {d.r_multiple.mean():+.3f}  "
              f"H1 {d[d.h == 'H1'].r_multiple.mean():+.3f}  H2 {d[d.h == 'H2'].r_multiple.mean():+.3f}")
        for feat in ("hour", "weekend", "vol", "dist", "age", "fund"):
            g = d.groupby([feat, "h"], observed=True).r_multiple.agg(["mean", "size"]).unstack("h")
            parts = []
            for v in g.index:
                m1, m2 = g.loc[v, ("mean", "H1")], g.loc[v, ("mean", "H2")]
                n1, n2 = g.loc[v, ("size", "H1")], g.loc[v, ("size", "H2")]
                same = np.isfinite(m1) and np.isfinite(m2) and np.sign(m1) == np.sign(m2)
                parts.append(f"{v}: {m1:+.3f}({int(n1) if np.isfinite(n1) else 0})/{m2:+.3f}({int(n2) if np.isfinite(n2) else 0}){'*' if same else ' '}")
            print(f"     {feat:<8} " + "   ".join(parts))
    print("     (* = same sign in both halves; H1/H2 = first/second half of BEATUSD's span)")
