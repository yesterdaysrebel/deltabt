"""Entry hour of day on the 5-minute arm, and on the live 1h arm: the one
feature in scripts/five_min_arm_lab.py that agreed in both halves of
BEATUSD's history on both exits.

    PYTHONPATH=. python3 scripts/five_min_arm_hours.py [family]

``family`` defaults to manual_scalp_st_banded; the live 1h arm is always
included as the third line.

Reads the same cached archive as the lab (2026-01-05..2026-08-12 on the thin
three, 2025-01-01..2026-08-12 on the majors). Sections:

    1. net by entry hour (2h and 6h buckets), pooled thin three and pooled
       majors, for the 5m arm at both exits and for the live 1h arm; with a
       selection test (pick the best 6h bucket on blocks < k, score on k)
    2. mechanism: realised 5m range by hour bucket, and the entry share
    3. BEATUSD halves at 2h resolution
    4. robustness of the 18-24 UTC window: shifted windows, concentration,
       ambiguity, sides, bootstrap, and what it would have done as a filter

The window is NOT expressed by any StrategySpec field today. Promoting it
means adding one, honoured by rulecore for both the backtester and the bot.

!! THE NUMBERS BELOW ARE A FILTER ON THE ALL-HOURS ARM'S TRADES, NOT AN ARM !!

    Keeping the in-window subset of what the all-hours arm took cannot show
    the entries a windowed arm would ALSO take while that arm was busy holding
    a position. Measured as a real family the same window is +0.117, not
    +0.331 -- see scripts/five_min_arm_hours_as_traded.py, which is the
    number that would trade. This script is kept as the DISCOVERY: it is how
    the effect was found and it is still the right tool for asking "is there a
    time-of-day effect at all", which is a question about the trades you have.

RESULTS 2026-09-04 (out/sweep/five_min_arm_lab/hours.txt, hours_robustness.txt)

    5m arm, 1R/24h, thin three, entries 18-24 UTC (23:30-05:30 IST):
      net +0.331 (n=110, win 67%, 74 target / 36 stop) against -0.086 for
      the other 18 hours; 4/4 pooled blocks (+0.62 +0.29 +0.25 +0.32) and
      4/4 on BEATUSD's own blocks (n=91). Picking the best 6h bucket on
      blocks < k chose 18-24 at every k and it scored +0.29/+0.25/+0.32 out
      of block. Bootstrap 95% [+0.16, +0.50]; window minus rest [+0.22,
      +0.61]. Not concentrated: top 3 = 10%, top 10 = 30%, median trade
      +0.95R. Longs +0.33, shorts +0.33. Shifted windows 17-23, 19-01,
      18-22, 20-24 are all 4/4; the effect peaks 20-24. Drawdown 4.2R
      against 27.2R unfiltered; 3.5 trades a week.
    5m arm, 3R/72h: window +0.328 but top 3 = 52% of it, median trade
      -0.97R, bootstrap includes zero. The 1R exit is the right one HERE,
      even though 3R is the right one for all-hours entries.
    live 1h arm, 3R/72h: window +0.445 (n=50) but concentrated (top 3 =
      42%), bootstrap includes zero. Not a reason to change the live arm.
    majors: no window effect on any arm (18-24 minus rest +0.02).
    mechanism, unexplained: 18-24 UTC is the QUIETEST window on the thin
      symbols (BEATUSD 47 bps mean 5m range vs 56-60) and the least-entered
      (19% of entries); it is not the quiet window on the majors. Stop width
      at entry is not different (476 bps vs 409-493).
    what was NOT done: no data after 2026-08-12 exists for the thin three in
      the cache, so there is no true out-of-sample on them. The 25-symbol
      control (five_min_arm_hours_screen.py) is the independent evidence.
"""
import dataclasses, sys, numpy as np, pandas as pd
from dataclasses import replace
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio

THIN = ["BEATUSD", "AKEUSD", "BANKUSD"]; MAJ = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
cat = ProductCatalog()
LOAD = {s: load_symbol(s) for s in THIN + MAJ}
FR = {s: _resampled(LOAD[s], 5, {}) for s in LOAD}
CTX = {s: _resampled(LOAD[s], 60, {})[0] for s in LOAD}
EDGES = {s: np.linspace(FR[s][0]["time"].min(), FR[s][0]["time"].max() + 1, 5).astype(np.int64) for s in FR}
def pooled_edges(syms):
    t = np.concatenate([FR[s][0]["time"].to_numpy() for s in syms]); return np.linspace(t.min(), t.max() + 1, 5).astype(np.int64)
PE = {"thin": pooled_edges(THIN), "majors": pooled_edges(MAJ)}

FAM = sys.argv[1] if len(sys.argv) > 1 else "manual_scalp_st_banded"


def spec_for(family, target):
    return build_spec(family, 5, 60 if "h1dir" in family else 1, stop_atr_multiplier=4.0, target_r=target)


_CF = {}


def confirm_frame(sym, spec):
    if not spec.confirm.enabled:
        return None
    key = (sym, spec.confirm_minutes)
    if key not in _CF:
        _CF[key] = _resampled(LOAD[sym], spec.confirm_minutes, {})[0]
    return _CF[key]

_T = {}
def trades(sym, spec, hold):
    key = (sym, spec.config_hash, hold)
    if key in _T: return _T[key]
    P, mark, tr = FR[sym]
    sig = rulecore.compute(P, confirm_frame(sym, spec), spec)
    res = run_portfolio({sym: Book(symbol=sym, bars=P, signals=rulecore.to_engine_signals(sig),
                                   costs=SymbolCosts.from_spec(cat.get(sym)), mark=mark, tradable=tr)},
                        params_for(spec, 5, hold), RiskGates.off(), initial_capital=10_000.0, funding={sym: LOAD[sym]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if len(d):
        d["blk"] = np.searchsorted(EDGES[sym][1:-1], d.entry_time.to_numpy(), side="right")
        d["symbol"] = sym
        d["hour"] = (d.entry_time % 86400) // 3600
        d["h2"] = d.hour // 2 * 2
        d["h6"] = d.hour // 6 * 6
    _T[key] = d; return d

def pool(spec, hold, syms, grp):
    fs = [trades(s, spec, hold) for s in syms]; fs = [f for f in fs if len(f)]
    d = pd.concat(fs, ignore_index=True)
    d["pblk"] = np.searchsorted(PE[grp][1:-1], d.entry_time.to_numpy(), side="right"); return d

def blocks(d, col):
    g = d.groupby(col).r_multiple.mean(); n = d.groupby(col).size()
    return "".join(f" {g.get(b, np.nan):+.3f}({n.get(b, 0):>3})" for b in range(4)) + f"  {int((g > 0).sum())}/4"

def line(name, d, col="pblk"):
    if d.empty: print(f"  {name:<30} no trades"); return
    print(f"  {name:<30}{blocks(d, col)}  net {d.r_multiple.mean():+.3f}  win {(d.r_multiple > 0).mean():>3.0%}  n={len(d)}")

ARMS = [(f"{FAM} 1R/24h", spec_for(FAM, 1.0), 24),
        (f"{FAM} 3R/72h", spec_for(FAM, 3.0), 72),
        ("1h arm 3R/72h (live)", spec_for("manual_scalp_banded_h1dir_t3", 3.0), 72)]

for grp, syms in (("thin", THIN), ("majors", MAJ)):
    print(f"\n######## {grp.upper()} ########")
    for name, spec, hold in ARMS:
        d = pool(spec, hold, syms, grp)
        print(f"\n== {name}: net by entry hour (UTC), 2h buckets, pooled {grp}   overall {d.r_multiple.mean():+.3f} n={len(d)}")
        g = d.groupby("h2").r_multiple.agg(["mean", "size"])
        print("  " + " ".join(f"{int(h):02d}:{g.loc[h,'mean']:+.2f}({int(g.loc[h,'size'])})" for h in g.index))
        print(f"  {'6h bucket':<30}{'blk0':>11}{'blk1':>11}{'blk2':>11}{'blk3':>11}")
        for h in (0, 6, 12, 18):
            line(f"{h:02d}-{h + 6:02d} UTC", d[d.h6 == h])
        # selection test over the four 6h buckets
        prem = []
        for k in (1, 2, 3):
            best = max((0, 6, 12, 18), key=lambda h: d[(d.h6 == h) & (d.pblk < k)].r_multiple.mean())
            tr = d[(d.h6 == best) & (d.pblk < k)].r_multiple.mean(); oo = d[(d.h6 == best) & (d.pblk == k)].r_multiple.mean()
            prem.append(tr - oo); print(f"     pick best 6h bucket on blocks<{k}: {best:02d}-{best + 6:02d}  train {tr:+.3f} -> block {k} {oo:+.3f}")
        print(f"     selection premium {np.mean(prem):+.3f}; 18-24 minus rest = {d[d.h6 == 18].r_multiple.mean() - d[d.h6 != 18].r_multiple.mean():+.3f}")
        print("  per symbol, own blocks:")
        for s in syms:
            x = d[d.symbol == s]
            line(f"  {s} 18-24", x[x.h6 == 18], "blk"); line(f"  {s} 00-18", x[x.h6 != 18], "blk")

print("\n== MECHANISM: realised 5m range by hour bucket (mean |close/close-1| in bps), and share of entries")
for s in THIN + MAJ:
    P = FR[s][0]; t = P["time"].to_numpy(); c = P["close"].to_numpy()
    r = np.abs(np.diff(np.log(c))) * 1e4; h6 = ((t[1:] % 86400) // 3600) // 6 * 6
    d = trades(s, ARMS[0][1], 24)
    sh = d.h6.value_counts(normalize=True).sort_index()
    print(f"  {s:<9} range " + "  ".join(f"{h:02d}h {r[h6 == h].mean():5.1f}" for h in (0, 6, 12, 18))
          + "   | entry share " + "  ".join(f"{h:02d}h {sh.get(h, 0):4.0%}" for h in (0, 6, 12, 18)))

print("\n== BEATUSD halves at 2h resolution, 5m arm 1R/24h (H1 / H2 mean, n)")
d = trades("BEATUSD", ARMS[0][1], 24); half = FR["BEATUSD"][0]["time"].iloc[len(FR["BEATUSD"][0]) // 2]
d["h"] = np.where(d.entry_time < half, "H1", "H2")
g = d.groupby(["h2", "h"]).r_multiple.agg(["mean", "size"]).unstack("h")
for h in g.index:
    print(f"  {int(h):02d}-{int(h) + 2:02d}  H1 {g.loc[h, ('mean', 'H1')]:+.3f}({int(g.loc[h, ('size', 'H1')])})   H2 {g.loc[h, ('mean', 'H2')]:+.3f}({int(g.loc[h, ('size', 'H2')])})")


# ---------------------------------------------------------------- 4. robustness
ARMS = [(f"{FAM} 1R/24h", spec_for(FAM, 1.0), 24),
        (f"{FAM} 3R/72h", spec_for(FAM, 3.0), 72),
        ("1h arm 3R/72h (live)", spec_for("manual_scalp_banded_h1dir_t3", 3.0), 72)]
rng = np.random.default_rng(1)
for name, spec, hold in ARMS:
    d = pool(spec, hold, THIN, "thin")
    print(f"\n== {name}  (pooled thin 3, n={len(d)})")
    print("  window robustness: net(n) for the entry window, and the rest")
    for a, b in ((16, 22), (17, 23), (18, 24), (19, 1), (20, 2), (18, 22), (20, 24), (21, 3), (22, 4)):
        inw = ((d.hour >= a) & (d.hour < b)) if a < b else ((d.hour >= a) | (d.hour < b))
        x, y = d[inw], d[~inw]
        print(f"    {a:02d}-{b:02d}  in {x.r_multiple.mean():+.3f}({len(x):>3}) {blocks(x, 'pblk')}   rest {y.r_multiple.mean():+.3f}({len(y):>3})")
    x = d[d.h6 == 18]; y = d[d.h6 != 18]
    tot = x.r_multiple.sum(); s = x.r_multiple.sort_values(ascending=False)
    print(f"  18-24 concentration: total {tot:+.1f}R; top 3 = {s.head(3).sum() / tot:.0%}, top 10 = {s.head(10).sum() / tot:.0%}; "
          f"mean without top 10 {s.iloc[10:].mean():+.3f}; median trade {x.r_multiple.median():+.3f}")
    print(f"  18-24 exits {x.exit_reason.value_counts().to_dict()}  ambiguous {x.ambiguous.mean():.0%} (rest {y.ambiguous.mean():.0%});"
          f" bars held median {x.bars_held.median():.0f} (rest {y.bars_held.median():.0f}); cost_r {x.cost_per_r.mean():.3f} (rest {y.cost_per_r.mean():.3f})")
    print(f"  18-24 longs {x[x.side > 0].r_multiple.mean():+.3f}({(x.side > 0).sum()})  shorts {x[x.side < 0].r_multiple.mean():+.3f}({(x.side < 0).sum()})")
    bx = [rng.choice(x.r_multiple.to_numpy(), len(x)).mean() for _ in range(3000)]
    diff = [rng.choice(x.r_multiple.to_numpy(), len(x)).mean() - rng.choice(y.r_multiple.to_numpy(), len(y)).mean() for _ in range(3000)]
    print(f"  bootstrap: 18-24 mean {x.r_multiple.mean():+.3f} 95% [{np.percentile(bx, 2.5):+.3f}, {np.percentile(bx, 97.5):+.3f}] P(>0)={np.mean(np.array(bx) > 0):.2f};"
          f"  18-24 minus rest {np.mean(diff):+.3f} 95% [{np.percentile(diff, 2.5):+.3f}, {np.percentile(diff, 97.5):+.3f}]")
    # what a live filter would have done: restrict entries to 18-24, blocks and drawdown
    dd_all = float((d.sort_values('entry_time').r_multiple.cumsum().cummax() - d.sort_values('entry_time').r_multiple.cumsum()).max())
    xs = x.sort_values('entry_time'); dd_x = float((xs.r_multiple.cumsum().cummax() - xs.r_multiple.cumsum()).max())
    print(f"  as a filter: all trades net {d.r_multiple.mean():+.3f} DD {dd_all:.1f}R n={len(d)}  ->  18-24 only net {x.r_multiple.mean():+.3f} DD {dd_x:.1f}R n={len(x)}"
          f"  ({len(x) / ((d.entry_time.max() - d.entry_time.min()) / 86400 / 7):.1f} trades/week)")

print("\n== BEATUSD only, own blocks, 18-24 window, all three arms")
for name, spec, hold in ARMS:
    d = trades("BEATUSD", spec, hold); x = d[d.h6 == 18]
    print(f"  {name:<22} {blocks(x, 'blk')}  net {x.r_multiple.mean():+.3f} n={len(x)}   rest {d[d.h6 != 18].r_multiple.mean():+.3f} n={len(d) - len(x)}")

print("\n== is the window an ATR artefact? stop width (bps) at entry by bucket, BEATUSD 5m arm")
d = trades("BEATUSD", ARMS[0][1], 24)
d["stop_bps"] = np.abs(d.entry_price - d.stop_price) / d.entry_price * 1e4
print("  " + "   ".join(f"{h:02d}-{h + 6:02d}: {d[d.h6 == h].stop_bps.median():.0f}bps" for h in (0, 6, 12, 18)))
