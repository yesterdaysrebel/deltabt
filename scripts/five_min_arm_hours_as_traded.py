"""The entry window measured as an ARM, not as a filter on another arm's trades.

    PYTHONPATH=. python3 scripts/five_min_arm_hours_as_traded.py

WHY THIS SCRIPT EXISTS, AND IT IS THE MOST IMPORTANT FILE OF THE THREE.

    scripts/five_min_arm_hours.py found the window by taking the ALL-HOURS
    arm's trades and keeping those whose entry bar opened 18:00-24:00 UTC. That
    is how you FIND a time-of-day effect and it is NOT how you size one, for a
    reason that is obvious once seen and invisible until then:

        a trade the all-hours arm is still HOLDING occupies the symbol's only
        position slot, so an arm that skips the other 18 hours is free at
        moments the filter can never show. It takes entries that do not exist
        in the filtered set.

    Measured on the thin three, 4xATR / 1R / 24h, cost gate on:

        all hours                        591 trades  net -0.009  2/4
        the filter, 18-24                110 trades  net +0.331  4/4
        THE ARM, entry_hours_utc=(18,24) 181 trades  net +0.117  4/4

        of the arm's 181:
          100 inherited from the all-hours arm    net +0.362
           81 NEW, the slot was busy before       net -0.186

    So the window is real and the extra capacity it frees is spent on worse
    signals -- the second and later triggers of an evening. +0.117 is the
    number a live run can reproduce; +0.331 is not, and nothing should quote
    it for this family.

WHAT IT PRINTS
    the three lines above and the inherited/new split; then the arm's own
    robustness (per symbol, sides, concentration, exits, bootstrap, trades per
    week); then every shifted window built as a real family; then the window
    at other targets.

RESULT 2026-09-04 (out/sweep/five_min_arm_lab/true_window.txt)
    +0.117, 4/4 blocks (+0.404 +0.136 +0.113 +0.074), n=181, win 57%,
    103 targets / 77 stops, top 3 = 18% of profit, median trade +0.93R,
    drawdown 11.0R against 30.8R, 5.8 trades/week.
    Bootstrap 95% [-0.031, +0.260], P(net>0) = 0.94 -- INCLUDES ZERO. Against
    the all-hours arm's out-of-window trades: +0.203 [+0.029, +0.375], which
    does not.
    A plateau in time: 18-22 +0.037, 16-22 +0.044, 17-23 +0.045, 19-01 +0.054,
    20-02 +0.101, 18-24 +0.117, 20-24 +0.167; and 00-06 -0.031, 06-12 -0.122,
    12-18 -0.126. A plateau in the target: 1R +0.117, 1.5R +0.142, 2R +0.122,
    all 4/4; 3R -0.033 and 1/4.
    Per symbol it is thin: BEATUSD +0.078 3/4 with block 3 NEGATIVE (n=147),
    AKEUSD and BANKUSD 17 trades each.

    Deployed as the `hours` stack, SPEC:manual_scalp_st_banded_h18_24@5, to
    produce the out-of-sample the cached archive cannot.
"""
import dataclasses, numpy as np, pandas as pd
from dataclasses import replace
from deltabt import rulecore
from deltabt.catalog import build_spec, FAMILIES
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
THIN = ["BEATUSD", "AKEUSD", "BANKUSD"]
cat = ProductCatalog(); LOAD = {s: load_symbol(s) for s in THIN}; FR = {s: _resampled(LOAD[s], 5, {}) for s in THIN}
allt = np.concatenate([FR[s][0]["time"].to_numpy() for s in FR]); PE = np.linspace(allt.min(), allt.max()+1, 5).astype(np.int64)
BE = {s: np.linspace(FR[s][0]["time"].min(), FR[s][0]["time"].max()+1, 5).astype(np.int64) for s in FR}
base = build_spec("manual_scalp_st_banded", 5, 1, stop_atr_multiplier=4.0, target_r=1.0)

def run(spec, hold=24, syms=THIN):
    fs=[]
    for s in syms:
        P, mark, tr = FR[s]
        sig = rulecore.to_engine_signals(rulecore.compute(P, None, spec))
        res = run_portfolio({s: Book(symbol=s, bars=P, signals=sig, costs=SymbolCosts.from_spec(cat.get(s)), mark=mark, tradable=tr)},
                            params_for(spec, 5, hold), RiskGates.off(), initial_capital=10_000.0, funding={s: LOAD[s]["funding"]})
        d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
        if len(d):
            d["symbol"]=s; d["blk"]=np.searchsorted(BE[s][1:-1], d.entry_time, side="right"); fs.append(d)
    d = pd.concat(fs, ignore_index=True)
    d["pblk"]=np.searchsorted(PE[1:-1], d.entry_time, side="right"); d["hour"]=(d.entry_time%86400)//3600
    return d

def line(name, d, col="pblk"):
    if not len(d): print(f"  {name:<30} no trades"); return
    g = d.groupby(col).r_multiple.mean(); n = d.groupby(col).size(); tot = d.r_multiple.sum()
    dd = float((d.r_multiple.cumsum().cummax()-d.r_multiple.cumsum()).max())
    top3 = d.r_multiple.nlargest(3).sum()/tot if tot>0 else np.nan
    print(f"  {name:<30}" + "".join(f" {g.get(b,np.nan):+.3f}({n.get(b,0):>3})" for b in range(4))
          + f"  {int((g>0).sum())}/4  net {d.r_multiple.mean():+.3f}  win {(d.r_multiple>0).mean():>3.0%}"
          + f"  DD {dd:>5.1f}R  top3 {top3:>4.0%}  n={len(d)}" if np.isfinite(top3) else
          f"  {name:<30}" + "".join(f" {g.get(b,np.nan):+.3f}({n.get(b,0):>3})" for b in range(4))
          + f"  {int((g>0).sum())}/4  net {d.r_multiple.mean():+.3f}  win {(d.r_multiple>0).mean():>3.0%}  DD {dd:>5.1f}R  n={len(d)}")

print("== THE ARM AS IT WOULD TRADE (a family with the window) vs THE POST-HOC FILTER")
print(f"  {'variant':<30}{'blk0':>11}{'blk1':>11}{'blk2':>11}{'blk3':>11}")
allh = run(base)
line("all hours (the 5m arm)", allh)
line("post-hoc filter 18-24", allh[allh.hour>=18])
W = run(build_spec("manual_scalp_st_banded_h18_24", 5, 1, stop_atr_multiplier=4.0, target_r=1.0))
line("THE WINDOWED FAMILY", W)

print("\n== why they differ: the freed position slot")
inherited = set(map(tuple, allh[allh.hour>=18][["symbol","entry_time"]].values))
mine = set(map(tuple, W[["symbol","entry_time"]].values))
extra = W[[t not in inherited for t in map(tuple, W[["symbol","entry_time"]].values)]]
lost  = allh[allh.hour>=18][[t not in mine for t in map(tuple, allh[allh.hour>=18][["symbol","entry_time"]].values)]]
print(f"  inherited from the all-hours arm : {len(mine & inherited):>4}  net {W[[t in inherited for t in map(tuple, W[['symbol','entry_time']].values)]].r_multiple.mean():+.3f}")
print(f"  NEW, the slot was busy before    : {len(extra):>4}  net {extra.r_multiple.mean():+.3f}  win {(extra.r_multiple>0).mean():.0%}")
print(f"  in the filter but not the arm    : {len(lost):>4}  (a trade the arm was still holding)")

print("\n== robustness of the arm as it trades")
print(f"  {'variant':<30}{'blk0':>11}{'blk1':>11}{'blk2':>11}{'blk3':>11}")
for s in THIN: line(f"  {s}", W[W.symbol==s], "blk")
line("longs", W[W.side>0]); line("shorts", W[W.side<0])
tot = W.r_multiple.sum(); srt = W.r_multiple.sort_values(ascending=False)
print(f"  concentration: total {tot:+.1f}R  top3 {srt.head(3).sum()/tot:.0%}  top10 {srt.head(10).sum()/tot:.0%}"
      f"  mean without top10 {srt.iloc[10:].mean():+.3f}  median {W.r_multiple.median():+.3f}")
print(f"  exits {W.exit_reason.value_counts().to_dict()}  cost_r {W.cost_per_r.mean():.3f}")
rng = np.random.default_rng(7)
b = [rng.choice(W.r_multiple.to_numpy(), len(W)).mean() for _ in range(4000)]
y = allh[allh.hour<18]
d2 = [rng.choice(W.r_multiple.to_numpy(), len(W)).mean() - rng.choice(y.r_multiple.to_numpy(), len(y)).mean() for _ in range(4000)]
print(f"  bootstrap net {W.r_multiple.mean():+.3f} 95% [{np.percentile(b,2.5):+.3f}, {np.percentile(b,97.5):+.3f}] P(>0)={np.mean(np.array(b)>0):.3f}")
print(f"  minus the all-hours arm's out-of-window trades: {np.mean(d2):+.3f} 95% [{np.percentile(d2,2.5):+.3f}, {np.percentile(d2,97.5):+.3f}]")
print(f"  trades/week {len(W)/((W.entry_time.max()-W.entry_time.min())/86400/7):.1f}")

print("\n== shifted windows, each built as a real family")
print(f"  {'variant':<30}{'blk0':>11}{'blk1':>11}{'blk2':>11}{'blk3':>11}")
for lo, hi in ((16,22),(17,23),(18,24),(19,1),(20,2),(18,22),(20,24),(12,18),(6,12),(0,6)):
    line(f"{lo:02d}-{hi:02d}", run(replace(base, entry_hours_utc=(lo,hi))))
print("\n== the window at other targets, as a real family")
print(f"  {'variant':<30}{'blk0':>11}{'blk1':>11}{'blk2':>11}{'blk3':>11}")
for t in (1.0, 1.5, 2.0, 3.0):
    for h in (24, 72):
        line(f"18-24 at {t:.1f}R/{h}h", run(replace(base, entry_hours_utc=(18,24), target_r=t), h))
