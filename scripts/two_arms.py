"""Which two 5-minute arms should run? Every candidate on one footing.

    PYTHONPATH=. python3 scripts/two_arms.py

The 1h-direction arm was withdrawn by instruction on 2026-09-04, so every
candidate here reads its rules from the 5m chart alone (a 1m confirmation is
allowed). Thin three, 4xATR stop, cost gate on, RiskGates off, the hold at
the deployable global value of 72h for every cell so no candidate is flattered
by an exit the other stack cannot share.

THE CANDIDATE LIST IS DECLARED HERE, BEFORE ANY NUMBER IS READ, and is the
set of cells the earlier labs already visited plus the two obvious
combinations they did not (the original %R rule with the evening window).
It is not a grid. Every cell is measured as an ARM -- the engine takes the
trades a live bot would -- which matters after today's lesson that a filter
on another arm's trades overstates itself.

WHAT IS PRINTED
    1. the table: net, anchored pooled blocks, n, win, drawdown, top-3
       share, bootstrap 95%, trades/week; BEATUSD on its own blocks; AKEUSD
       and BANKUSD nets (21 days each -- read them as weather, not climate)
    2. the selection test: choose the best candidate on blocks < k, score
       it on block k. If the winner changes with k, the table is ranking
       noise.
    3. pairwise overlap among the leaders: share of one arm's entries that
       sit within an hour of the other's on the same symbol. Two arms that
       take the same trades are one arm and one instance's rent.
    4. what each leader is like to live with: median trade, hold, share of
       full losses, longest losing run.

RESULT 2026-09-04 (out/sweep/five_min_arm_lab/two_arms.txt, two_arms_gated.txt)

    Top of the table by net: %R 5m+1m 3R +0.140 (3/4), st+band 18-24 1.5R
    +0.135 (2/4), %R only 4R +0.127 (4/4), st+band 18-24 2R +0.120 (4/4),
    st+band 18-24 1R +0.114 (4/4). Every bootstrap includes zero.

    THE SELECTION TEST decides, and it is not the top of the table. Choosing
    the best candidate on blocks < k: k=1 picks st (no band) 3R and it
    scores -0.220 on block 1 -- the warning that the table ranks noise. k=2
    and k=3 both pick st+band 18-24 1R and it scores +0.113 and +0.074. On
    block-sign count the only candidates positive on every training block
    AND on the held-out block at k=3 are st+band 18-24 1R (+0.074) and
    %R only 4R (+0.100). Premium over 18 candidates +0.298.

    OVERLAP: the three windowed cells are one arm (87-100% shared entries).
    The three %R-entry cells are one family (42-64%). st+band 18-24 1R and
    %R only 4R share 15% / 8%.

    CHOSEN: st+band 18-24 1R (`manual_scalp_st_banded_h18_24`) and %R only 4R
    (`manual_scalp_t4`). Different mechanisms -- a 57%-win scalp resolved in
    3h inside six hours of the day, and a 26%-win hold that needs days --
    different symbols carry them, and each passes the out-of-block test on
    its own. Runner-up for the second slot: %R 5m+1m 3R, higher net and a
    lower drawdown (29R vs 40R) but a negative block 1 and never chosen out
    of block.

    Under the LIVE gates (which are off: 6 slots, no breaker, no drawdown
    halt) as one shared account each: hours +10.6% / 3.9% max drawdown,
    tail +21.0% / 11.6%, the all-hours control -3.8% / 14.4%. No halts.
"""
from __future__ import annotations

import dataclasses
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
cat = ProductCatalog()
LOAD = {s: load_symbol(s) for s in THIN}
FR = {s: _resampled(LOAD[s], 5, {}) for s in THIN}
CF = {}


def cframe(s, m):
    if (s, m) not in CF:
        CF[(s, m)] = _resampled(LOAD[s], m, {})[0]
    return CF[(s, m)]


allt = np.concatenate([FR[s][0]["time"].to_numpy() for s in FR])
PE = np.linspace(allt.min(), allt.max() + 1, 5).astype(np.int64)
BE = {s: np.linspace(FR[s][0]["time"].min(), FR[s][0]["time"].max() + 1, 5).astype(np.int64) for s in FR}
DAYS = (allt.max() - allt.min()) / 86400
WIN = (18, 24)

# name -> (family, target, window)
CANDIDATES = {
    "st+band 1R":            ("manual_scalp_st_banded", 1.0, None),
    "st+band 1.5R":          ("manual_scalp_st_banded", 1.5, None),
    "st+band 2R":            ("manual_scalp_st_banded", 2.0, None),
    "st+band 18-24 1R":      ("manual_scalp_st_banded", 1.0, WIN),
    "st+band 18-24 1.5R":    ("manual_scalp_st_banded", 1.5, WIN),
    "st+band 18-24 2R":      ("manual_scalp_st_banded", 2.0, WIN),
    "st (no band) 1R":       ("manual_scalp_st", 1.0, None),
    "st (no band) 3R":       ("manual_scalp_st", 3.0, None),
    "st (no band) 18-24 1R": ("manual_scalp_st", 1.0, WIN),
    "%R only 1R":            ("manual_scalp", 1.0, None),
    "%R only 3R":            ("manual_scalp", 3.0, None),
    "%R only 4R":            ("manual_scalp", 4.0, None),
    "%R only 18-24 1R":      ("manual_scalp", 1.0, WIN),
    "%R only 18-24 4R":      ("manual_scalp", 4.0, WIN),
    "%R 5m+1m 1R":           ("manual_scalp_both", 1.0, None),
    "%R 5m+1m 3R":           ("manual_scalp_both", 3.0, None),
    "%R 5m+1m 4R":           ("manual_scalp_both", 4.0, None),
    "%R banded 1.5R":        ("manual_scalp_banded", 1.5, None),
}


def spec_of(family, target, window):
    spec = build_spec(family, 5, 1, stop_atr_multiplier=4.0, target_r=target)
    return replace(spec, entry_hours_utc=window) if window else spec


SIG = {}


def trades(s, spec):
    key = (s, spec.config_hash)
    if key not in SIG:
        SIG[key] = rulecore.to_engine_signals(rulecore.compute(
            FR[s][0], cframe(s, spec.confirm_minutes) if spec.confirm.enabled else None, spec))
    P, mark, tr = FR[s]
    res = run_portfolio({s: Book(symbol=s, bars=P, signals=SIG[key],
                                 costs=SymbolCosts.from_spec(cat.get(s)), mark=mark, tradable=tr)},
                        params_for(spec, 5, HOLD), RiskGates.off(), initial_capital=10_000.0,
                        funding={s: LOAD[s]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if len(d):
        d["symbol"] = s
        d["blk"] = np.searchsorted(BE[s][1:-1], d.entry_time, side="right")
        d["pblk"] = np.searchsorted(PE[1:-1], d.entry_time, side="right")
    return d


def run(name):
    fam, tgt, win = CANDIDATES[name]
    fs = [trades(s, spec_of(fam, tgt, win)) for s in THIN]
    return pd.concat([f for f in fs if len(f)], ignore_index=True)


rng = np.random.default_rng(11)
rows, TR = [], {}
for name in CANDIDATES:
    d = run(name); TR[name] = d
    g = d.groupby("pblk").r_multiple.mean()
    tot = d.r_multiple.sum()
    top3 = d.r_multiple.nlargest(3).sum() / tot if tot > 0 else np.nan
    dd = float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max())
    bs = np.array([rng.choice(d.r_multiple.to_numpy(), len(d)).mean() for _ in range(3000)])
    b = d[d.symbol == "BEATUSD"]; gb = b.groupby("blk").r_multiple.mean()
    rows.append(dict(
        arm=name, net=d.r_multiple.mean(), pos=int((g > 0).sum()), n=len(d),
        win=(d.r_multiple > 0).mean(), dd=dd, top3=top3,
        lo=np.percentile(bs, 2.5), hi=np.percentile(bs, 97.5), p=(bs > 0).mean(),
        wk=len(d) / (DAYS / 7),
        beat=b.r_multiple.mean(), beat_pos=int((gb > 0).sum()), beat_n=len(b),
        ake=d[d.symbol == "AKEUSD"].r_multiple.mean(), bank=d[d.symbol == "BANKUSD"].r_multiple.mean(),
        **{f"b{i}": g.get(i, np.nan) for i in range(4)}))
R = pd.DataFrame(rows).set_index("arm")

print(f"== 1. EVERY CANDIDATE AS AN ARM  (thin three, 4xATR, cost gate on, hold {HOLD}h, {DAYS:.0f} days)")
print(f"  {'arm':<22}{'net':>7}{'blocks':>8}{'n':>5}{'win':>5}{'DD':>7}{'top3':>6}{'boot 95%':>18}{'P>0':>5}{'/wk':>5}"
      f"  | BEAT net blk n | AKE  | BANK")
for name, r in R.sort_values("net", ascending=False).iterrows():
    t3 = f"{r.top3:>5.0%}" if np.isfinite(r.top3) else "   --"
    print(f"  {name:<22}{r.net:>+7.3f}{r.pos:>5}/4 {r.n:>5.0f}{r.win:>5.0%}{r.dd:>6.1f}R{t3}"
          f"  [{r.lo:+.3f},{r.hi:+.3f}]{r.p:>5.2f}{r.wk:>5.1f}"
          f"  | {r.beat:+.3f} {r.beat_pos}/4 {r.beat_n:>3.0f} | {r.ake:+.2f} | {r.bank:+.2f}")

print("\n== 2. SELECTION TEST: best candidate on blocks < k, scored on block k")
prem = []
for k in (1, 2, 3):
    cols = [f"b{i}" for i in range(k)]
    tr = R[cols].mean(axis=1); best = tr.idxmax()
    oos = R.loc[best, f"b{k}"]; prem.append(tr[best] - oos)
    ranked = tr.sort_values(ascending=False).head(3)
    print(f"  k={k}: pick '{best}'  train {tr[best]:+.3f} -> block {k} {oos:+.3f}"
          f"    (next: " + ", ".join(f"{n} {v:+.3f}" for n, v in ranked.iloc[1:].items()) + ")")
print(f"  selection premium over these {len(R)} candidates: {np.mean(prem):+.3f}")
print("  the same test on block-sign count (how many of the k training blocks were positive):")
for k in (2, 3):
    cols = [f"b{i}" for i in range(k)]
    cnt = (R[cols] > 0).sum(axis=1); tr = R[cols].mean(axis=1)
    order = sorted(R.index, key=lambda a: (cnt[a], tr[a]), reverse=True)[:4]
    print(f"  k={k}: " + "; ".join(f"{a} {cnt[a]}/{k} -> blk{k} {R.loc[a, f'b{k}']:+.3f}" for a in order))

print("\n== 3. OVERLAP among the leaders (share of the ROW arm's entries within 1h of a COLUMN arm's entry, same symbol)")
leaders = list(R.sort_values("net", ascending=False).head(8).index)


def overlap(a, b):
    da, db = TR[a], TR[b]
    hit = 0
    for s in THIN:
        ta = np.sort(da[da.symbol == s].entry_time.to_numpy()); tb = np.sort(db[db.symbol == s].entry_time.to_numpy())
        if len(ta) == 0 or len(tb) == 0:
            continue
        j = np.searchsorted(tb, ta)
        near = np.zeros(len(ta), dtype=bool)
        for off in (-1, 0):
            jj = np.clip(j + off, 0, len(tb) - 1)
            near |= np.abs(tb[jj] - ta) <= 3600
        hit += int(near.sum())
    return hit / max(len(da), 1)


print("  " + " " * 22 + "".join(f"{c[:10]:>11}" for c in leaders))
for a in leaders:
    print(f"  {a:<22}" + "".join(f"{overlap(a, c):>11.0%}" if a != c else f"{'--':>11}" for c in leaders))

print("\n== 4. WHAT EACH LEADER LOOKS LIKE TO LIVE WITH")
for a in leaders[:6]:
    d = TR[a]
    med = d.r_multiple.median(); ex = d.exit_reason.value_counts().to_dict()
    held = d.bars_held.median() * 5 / 60
    losses = (d.r_multiple <= -0.5).mean()
    streak = 0; worst = 0
    for r in d.sort_values("entry_time").r_multiple:
        streak = streak + 1 if r <= 0 else 0; worst = max(worst, streak)
    print(f"  {a:<22} median trade {med:+.2f}R  median hold {held:>5.1f}h  full losses {losses:>4.0%}"
          f"  longest losing run {worst:>2}  exits {ex}")
