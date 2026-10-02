"""Exit rules paired on IDENTICAL entries: baseline vs ladder vs trail.

    PYTHONPATH=. .venv/bin/python scripts/paired_exits_lab.py

WHY (2026-10-02). The prod pilot's dry run shadows all three exits on the
same entries; this asks the same question of history first. The baseline's
own entries on the prod universe (BEATUSD, AKEUSD, BANKUSD) are generated
once, then FORCED into three runs that differ only in the exit rule, so every
trade has all three outcomes. Filler bars are dropped (live-equivalent bar
set; see CONTEXT.md). This removes turnover by construction -- the dry run's
shadow does the same -- so it measures the exit rule alone.

RESULT 2026-10-02 (out/sweep/five_min_arm_lab/paired_exits_trail.txt), 423
paired trades, 2026-01-06..09-30:

    baseline +0.037R/trade (+15.8R), 3R hit 107, win 28%, max DD 42.7R
    ladder   -0.018R/trade ( -7.7R), 3R hit  14, win 44%, max DD 25.1R
    trail    +0.006R/trade ( +2.6R), 3R hit   4, win 62%, max DD 17.2R

    trail - baseline -0.031R (t -0.41); ladder - baseline -0.055R (t -0.78);
    trail - ladder +0.024R (t +1.34). NONE IS DISTINGUISHABLE FROM ZERO, and
    the baseline itself is not either. The trail does not earn more; it
    changes the SHAPE: drawdown 17.2R vs 42.7R, longest non-positive run 7 vs
    18, sd 0.85R vs 1.78R. On a $250 account at 2% ($5/R) a 20% latch is
    10R, which all three rules exceeded over these nine months.
"""
import sys, dataclasses, math, statistics as st, collections
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.data.quality import tradable_mask
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
from deltabt.strategy import Signals

UNIVERSE = ["BEATUSD", "AKEUSD", "BANKUSD"]
cat = ProductCatalog()
def clean(d):
    l = d["ltp"]; flat = (l.volume == 0) & (l.open == l.close) & (l.high == l.low)
    l = l[~flat].reset_index(drop=True)
    return dict(symbol=d["symbol"], ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
DATA = {s: clean(load_symbol(s)) for s in UNIVERSE}
cache = {}
base_spec = build_spec("manual_scalp_both_t3", 5)

def books(signal_fn):
    out = {}
    for s, d in DATA.items():
        P, mk, tr = _resampled(d, 5, cache)
        out[s] = Book(symbol=s, bars=P, signals=signal_fn(s, P), costs=SymbolCosts.from_spec(cat.get(s)),
                      mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
    return out

def run(bks, spec, **extra):
    params = replace(params_for(spec, 5, 72), stop_fill="ltp_close", **extra)
    res = run_portfolio(bks, params, replace(RiskGates.off(), max_open_positions=len(bks)),
                        initial_capital=10_000.0, funding={s: DATA[s]["funding"] for s in bks})
    return pd.DataFrame([dataclasses.asdict(t) for t in res.trades])

# 1) the baseline's own entries
def real_signals(s, P):
    C = _resampled(DATA[s], 1, cache)[0]
    return rulecore.to_engine_signals(rulecore.compute(P, C, base_spec))
base = run(books(real_signals), base_spec)
ENT = {s: base[base.symbol == s][["entry_time", "side", "stop_price"]] for s in UNIVERSE}

# 2) the same entries forced, with each exit rule
def forced(s, P):
    sig = real_signals(s, P)
    t = P.time.to_numpy("int64"); n = len(t)
    le = np.zeros(n, bool); se = np.zeros(n, bool)
    e = ENT[s]; idx = np.searchsorted(t, e.entry_time.to_numpy())
    for i, side in zip(idx, e.side):
        if i < n: (le if side > 0 else se)[i] = True
    sig.long_entry, sig.short_entry = le, se
    return sig
fb = books(forced)
RULES = {"baseline": dict(), "ladder": dict(ladder_rungs=((0.5,0.0),(1.0,0.5),(1.5,1.0),(2.0,1.5))),
         "trail":   dict(trail_after_r=0.5, trail_r=0.5)}
res = {k: run(fb, base_spec, **v) for k, v in RULES.items()}
key = lambda d: d.set_index(["symbol", "side", "entry_time"])
B = key(res["baseline"]); common = B.index
for k in ("ladder", "trail"): common = common.intersection(key(res[k]).index)
def t(x): x = list(x); return st.mean(x) / (st.stdev(x) / math.sqrt(len(x))) if len(x) > 1 and st.stdev(x) > 0 else float("nan")
span = pd.to_datetime([int(base.entry_time.min()), int(base.entry_time.max())], unit="s")
print(f"window {span[0].date()} .. {span[1].date()}  universe {UNIVERSE}  (filler bars dropped)")
print(f"baseline entries {len(base)}; replayed under all three rules, paired: {len(common)}\n")
print(f"{'rule':9s} {'n':>5s} {'sumR':>8s} {'meanR':>7s} {'win%':>5s} {'3R hit':>6s} {'promoted':>8s} {'fees $':>7s}")
for k in RULES:
    d = key(res[k]).loc[common]
    print(f"{k:9s} {len(d):5d} {d.r_multiple.sum():+8.2f} {d.r_multiple.mean():+7.3f} {100*(d.r_multiple>0).mean():5.0f} "
          f"{(d.exit_reason=='target').sum():6d} {int(d.stop_promoted.sum()):8d} {d.fees.sum():7.0f}")
print()
for k in ("ladder", "trail"):
    diff = key(res[k]).loc[common].r_multiple.to_numpy() - B.loc[common].r_multiple.to_numpy()
    print(f"{k} - baseline, paired: mean {diff.mean():+.3f}R/trade  t={t(diff):+.2f}  better {(diff>0.05).sum()} same {(abs(diff)<=0.05).sum()} worse {(diff<-0.05).sum()}")
dlt = key(res["trail"]).loc[common].r_multiple.to_numpy() - key(res["ladder"]).loc[common].r_multiple.to_numpy()
print(f"trail - ladder,   paired: mean {dlt.mean():+.3f}R/trade  t={t(dlt):+.2f}")
print("\nper symbol, meanR (n):")
for s in UNIVERSE:
    m = [i for i in common if i[0] == s]
    row = "  ".join(f"{k} {key(res[k]).loc[m].r_multiple.mean():+.3f}" for k in RULES)
    print(f"  {s:8s} n={len(m):4d}  {row}")
q = np.quantile([i[2] for i in common], [0.25, 0.5, 0.75])
print("per anchored quarter of the sample, meanR:")
for b in range(4):
    m = [i for i in common if np.searchsorted(q, i[2], side="right") == b]
    row = "  ".join(f"{k} {key(res[k]).loc[m].r_multiple.mean():+.3f}" for k in RULES)
    print(f"  q{b} n={len(m):4d}  {row}")

print("\nrisk shape on the same trades (in R, by exit time):")
for k in RULES:
    d = key(res[k]).loc[common].sort_values("exit_time")
    e = p = mdd = 0.0; streak = worst = 0
    for r in d.r_multiple:
        e += r; p = max(p, e); mdd = max(mdd, p - e)
        streak = streak + 1 if r <= 0 else 0; worst = max(worst, streak)
    print(f"  {k:9s} max drawdown {mdd:5.1f}R   longest run of non-positive exits {worst:3d}   "
          f"sd {d.r_multiple.std():.2f}R   worst trade {d.r_multiple.min():+.2f}R")
