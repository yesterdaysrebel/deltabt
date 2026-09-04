"""The operator's original setup, and whether it can be improved -- exit surface.

    PYTHONPATH=. python3 scripts/original_setup_exit_surface.py

The original setup is `manual_scalp`: %R rising above -80 (variant_a, no
ceiling), no Supertrend, no DI, 4xATR stop, 1R target, 5m. It was recovered
from 165 hand-placed round trips (out/manual/roundtrips_seven.csv) and ran
live as MANUAL-SCALP-5M-PAPER-20260831-*. `manual_scalp_both` is the strict
reading of "5 min and 1 min as confirmation". `manual_scalp_banded_h1dir` is
the live family, the original's descendant.

Companions: scripts/original_setup_beat.py (BEATUSD alone, plus the original
entry with 1h direction and no ceiling), scripts/five_min_arm_lab.py thin
manual_scalp (exits, path, parameters, sides, features) and
scripts/five_min_arm_hours.py manual_scalp.

SECTIONS
    1. target x hold surface (2-8R, 48-240h), pooled thin three, for the
       original, the 1m-confirmed original and the live family; the same for
       the original on the majors in net AND gross; selection test per family
    2. cell detail: exits, top-3 share, drawdown; per symbol on own blocks

RESULTS 2026-09-04 (out/sweep/five_min_arm_lab/original_setup_exit_surface.txt,
original_setup_beat.txt, beat_4r_sides.txt)

    the original, as traded (1R/24h): thin three -0.048 (1/4, n=908),
      BEATUSD -0.026, majors -0.074. Every engine exit, every %R period and
      band, breakeven and trailing: nothing (five_min_arm_lab thin
      manual_scalp). The hour window is weak on it (+0.084, 2/4).
    the exit is the lever, on BEATUSD only: 4R/72h +0.288 (4/4 on its own
      blocks incl. the -68% block, n=260, bootstrap [+0.04, +0.56], top 3 =
      18%, top 10 = 57%, win 29%, median trade -1.0R). Holds 72-168h are a
      plateau at 4R (+0.28 each); targets are NOT (3R +0.17, 5R +0.11 with
      top-3 75%, 6R +0.05). AKEUSD and BANKUSD get WORSE at 4R (-0.51,
      -0.52): on 21 days each, the wide target has nothing to catch.
    1h direction on the original (no ceiling): pooled 4R/120h +0.272 4/4
      n=279; BEATUSD +0.297 4/4, AKEUSD +0.431 3/4, BANKUSD -0.104 2/4. The
      live family (with the ceiling) at the same cell: +0.342 4/4 n=166 --
      fewer trades, more per trade; on BEATUSD +0.353 vs +0.297.
    the 1m confirmation (manual_scalp_both): 3R/72h +0.140 3/4 but the
      surface is bumpy (4R/120h +0.031 with top-3 143%) and the selection
      test flips cell every split. Not an improvement that can be relied on.
    majors: net-negative at every robust cell; gross is +0.08..+0.18 and
      4/4 at 5R-8R targets, so the entries have a right tail and cost
      (0.10R) eats it -- the known conclusion, now on the original family.
    selection premium over the 30-35 cell grids is +0.6..+1.0 because the
      8R/168-240h corner posts enormous in-sample numbers that do not hold.
      The 4R/72-120h region is the one place every family agrees (4/4,
      top-3 about 20%) and it is where the live arm already sits (3R/72h,
      chosen one step inside the 4R peak on 2026-09-04).
    sides at 4R on BEATUSD: longs +0.51 (4/4, +0.02 in the -68% block),
      shorts +0.04 (2/4). Not drift alone, but a long-only reading was NOT
      pre-declared and is not a finding.
"""
import dataclasses, numpy as np, pandas as pd
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
CF = {}
def cframe(s, m):
    if (s, m) not in CF: CF[(s, m)] = _resampled(LOAD[s], m, {})[0]
    return CF[(s, m)]
def edges(syms):
    t = np.concatenate([FR[s][0]["time"].to_numpy() for s in syms]); return np.linspace(t.min(), t.max() + 1, 5).astype(np.int64)
PE = {"thin": edges(THIN), "majors": edges(MAJ)}
BE = {s: np.linspace(FR[s][0]["time"].min(), FR[s][0]["time"].max() + 1, 5).astype(np.int64) for s in FR}
SIG = {}
def trades(s, fam, target, hold):
    spec = build_spec(fam, 5, 60 if "h1dir" in fam else 1, stop_atr_multiplier=4.0, target_r=target)
    key = (s, spec.config_hash)
    if key not in SIG:
        SIG[key] = rulecore.to_engine_signals(rulecore.compute(FR[s][0], cframe(s, spec.confirm_minutes) if spec.confirm.enabled else None, spec))
    P, mark, tr = FR[s]
    res = run_portfolio({s: Book(symbol=s, bars=P, signals=SIG[key], costs=SymbolCosts.from_spec(cat.get(s)), mark=mark, tradable=tr)},
                        params_for(spec, 5, hold), RiskGates.off(), initial_capital=10_000.0, funding={s: LOAD[s]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if len(d):
        d["symbol"] = s; d["gross"] = d.r_multiple + d.cost_per_r; d["blk"] = np.searchsorted(BE[s][1:-1], d.entry_time, side="right")
    return d
def pool(fam, t, h, syms, grp):
    d = pd.concat([trades(s, fam, t, h) for s in syms], ignore_index=True)
    d["pblk"] = np.searchsorted(PE[grp][1:-1], d.entry_time, side="right"); return d
TG = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0); HD = (48, 72, 120, 168, 240)
def surface(fam, syms, grp, col="r_multiple"):
    rows = []
    print(f"\n== {fam} on {grp}: pooled {'NET' if col == 'r_multiple' else 'GROSS'} R (n) [+ve blocks]   rows = target, cols = hold (hours)")
    print("  " + " " * 8 + "".join(f"{'h' + str(h):>22}" for h in HD))
    for t in TG:
        line = f"  {t:>4.1f}R  "
        for h in HD:
            d = pool(fam, t, h, syms, grp); g = d.groupby("pblk")[col].mean(); pos = int((g > 0).sum())
            tot = d[col].sum()
            rows.append(dict(fam=fam, grp=grp, target=t, hold=h, net=d[col].mean(), n=len(d), pos=pos,
                             dd=float((d[col].cumsum().cummax() - d[col].cumsum()).max()),
                             top3=d[col].nlargest(3).sum() / tot if tot > 0 else np.nan,
                             timeouts=(d.exit_reason == "max_hold").mean(), targets=(d.exit_reason == "target").mean(),
                             **{f"b{i}": g.get(i, np.nan) for i in range(4)}))
            line += f"  {d[col].mean():+.3f}({len(d):>4})[{pos}/4]"
        print(line)
    return pd.DataFrame(rows)
R = pd.concat([surface("manual_scalp", THIN, "thin"), surface("manual_scalp_both", THIN, "thin"),
               surface("manual_scalp_banded_h1dir", THIN, "thin"),
               surface("manual_scalp", MAJ, "majors"), surface("manual_scalp", MAJ, "majors", col="gross")], ignore_index=True)
print("\n== selection over the 30-cell grid per family (thin): best on blocks<k -> block k")
for fam in ("manual_scalp", "manual_scalp_both", "manual_scalp_banded_h1dir"):
    x = R[(R.fam == fam) & (R.grp == "thin")].drop_duplicates(["target", "hold"]); prem = []
    for k in (1, 2, 3):
        cols = [f"b{i}" for i in range(k)]; best = x.loc[x[cols].mean(axis=1).idxmax()]
        prem.append(best[cols].mean() - best[f"b{k}"])
        print(f"  {fam:<28} k={k}: {best.target:.0f}R/{int(best.hold)}h  train {best[cols].mean():+.3f} -> block {k} {best[f'b{k}']:+.3f}")
    print(f"  {fam:<28} premium {np.mean(prem):+.3f}")
print("\n== cell detail, thin: exits, concentration, drawdown")
print(f"  {'family':<28}{'cell':<10}{'net':>7}{'n':>6}{'+ve':>5}{'DD':>8}{'top3':>7}{'target%':>9}{'timeout%':>10}")
for fam, t, h in (("manual_scalp", 1, 24), ("manual_scalp", 3, 72), ("manual_scalp", 4, 72), ("manual_scalp", 4, 120), ("manual_scalp", 5, 120), ("manual_scalp", 6, 168),
                  ("manual_scalp_both", 3, 72), ("manual_scalp_both", 4, 120),
                  ("manual_scalp_banded_h1dir", 3, 72), ("manual_scalp_banded_h1dir", 4, 120), ("manual_scalp_banded_h1dir", 6, 168)):
    d = pool(fam, float(t), h, THIN, "thin"); g = d.groupby("pblk").r_multiple.mean(); tot = d.r_multiple.sum()
    dd = float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max())
    print(f"  {fam:<28}{f'{t}R/{h}h':<10}{d.r_multiple.mean():>+7.3f}{len(d):>6}{int((g > 0).sum()):>4}/4{dd:>7.1f}R"
          f"{(d.r_multiple.nlargest(3).sum() / tot if tot > 0 else float('nan')):>7.0%}{(d.exit_reason == 'target').mean():>9.0%}{(d.exit_reason == 'max_hold').mean():>10.0%}")
print("\n== per symbol, own blocks, manual_scalp 4R/120h and 4R/72h against the original 1R/24h")
for s in THIN:
    for t, h in ((1.0, 24), (4.0, 72), (4.0, 120)):
        d = trades(s, "manual_scalp", t, h); g = d.groupby("blk").r_multiple.mean()
        dd = float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max())
        print(f"  {s:<9} {t:.0f}R/{h:>3}h  " + "".join(f" {g.get(b, np.nan):+.3f}({int((d.blk == b).sum()):>3})" for b in range(4))
              + f"  {int((g > 0).sum())}/4  net {d.r_multiple.mean():+.3f}  DD {dd:5.1f}R  n={len(d)}")
