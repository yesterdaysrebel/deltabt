"""BEATUSD alone: exit surfaces for the original setup, the original with 1h
direction (no %R ceiling, built inline -- not a catalog family), and the
live family; plus manual_scalp_h1dir pooled and per symbol, and the
original's 4R/72h cell in detail.

    PYTHONPATH=. python3 scripts/original_setup_beat.py

Results and context: the docstring of scripts/original_setup_exit_surface.py.
"""
import dataclasses, numpy as np, pandas as pd
from dataclasses import replace
from deltabt import rulecore
from deltabt.catalog import build_spec, _tf_rules
from deltabt.spec import StrategySpec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
THIN = ["BEATUSD", "AKEUSD", "BANKUSD"]
cat = ProductCatalog()
LOAD = {s: load_symbol(s) for s in THIN}
FR = {s: _resampled(LOAD[s], 5, {}) for s in THIN}
CF = {}
def cframe(s, m):
    if (s, m) not in CF: CF[(s, m)] = _resampled(LOAD[s], m, {})[0]
    return CF[(s, m)]
BE = {s: np.linspace(FR[s][0]["time"].min(), FR[s][0]["time"].max() + 1, 5).astype(np.int64) for s in FR}
allt = np.concatenate([FR[s][0]["time"].to_numpy() for s in FR]); PE = np.linspace(allt.min(), allt.max() + 1, 5).astype(np.int64)

def make_spec(fam, target):
    if fam == "manual_scalp_h1dir":
        # the original entry (%R variant_a, no ceiling) with direction from the last closed 1h Supertrend
        spec = StrategySpec(name="manual_scalp_h1dir@5m", primary_minutes=5, confirm_minutes=60,
                            primary=_tf_rules(wpr_rule="variant_a"), confirm=_tf_rules(supertrend="aligned"))
        return replace(spec, trigger="edge", stop="atr", stop_atr_multiplier=4.0, target_r=target, max_stop_pct=0.10)
    return build_spec(fam, 5, 60 if "h1dir" in fam else 1, stop_atr_multiplier=4.0, target_r=target)
SIG = {}
def trades(s, fam, target, hold):
    spec = make_spec(fam, target); key = (s, spec.config_hash)
    if key not in SIG:
        SIG[key] = rulecore.to_engine_signals(rulecore.compute(FR[s][0], cframe(s, spec.confirm_minutes) if spec.confirm.enabled else None, spec))
    P, mark, tr = FR[s]
    res = run_portfolio({s: Book(symbol=s, bars=P, signals=SIG[key], costs=SymbolCosts.from_spec(cat.get(s)), mark=mark, tradable=tr)},
                        params_for(spec, 5, hold), RiskGates.off(), initial_capital=10_000.0, funding={s: LOAD[s]["funding"]})
    d = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    if len(d):
        d["symbol"] = s; d["blk"] = np.searchsorted(BE[s][1:-1], d.entry_time, side="right"); d["pblk"] = np.searchsorted(PE[1:-1], d.entry_time, side="right")
        d["hour"] = (d.entry_time % 86400) // 3600
    return d
TG = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0); HD = (24, 48, 72, 120, 168)
def surface(fam, syms, col):
    rows = []
    label = "BEATUSD alone, own blocks" if syms == ["BEATUSD"] else "pooled thin three"
    print(f"\n== {fam}, {label}: NET R (n) [+ve blocks] {{top-3 share}}   rows = target, cols = hold")
    print("  " + " " * 8 + "".join(f"{'h' + str(h):>26}" for h in HD))
    for t in TG:
        line = f"  {t:>4.1f}R  "
        for h in HD:
            d = pd.concat([trades(s, fam, t, h) for s in syms], ignore_index=True); g = d.groupby(col).r_multiple.mean(); pos = int((g > 0).sum())
            tot = d.r_multiple.sum(); top3 = d.r_multiple.nlargest(3).sum() / tot if tot > 0 else np.nan
            rows.append(dict(fam=fam, target=t, hold=h, net=d.r_multiple.mean(), n=len(d), pos=pos, top3=top3,
                             dd=float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max()), **{f"b{i}": g.get(i, np.nan) for i in range(4)}))
            line += f"  {d.r_multiple.mean():+.3f}({len(d):>3})[{pos}/4]{{{top3:3.0%}}}" if np.isfinite(top3) else f"  {d.r_multiple.mean():+.3f}({len(d):>3})[{pos}/4]{{ --}}"
        print(line)
    R = pd.DataFrame(rows); prem = []
    for k in (1, 2, 3):
        cols = [f"b{i}" for i in range(k)]; best = R.loc[R[cols].mean(axis=1).idxmax()]; prem.append(best[cols].mean() - best[f"b{k}"])
        print(f"     select on blocks<{k}: {best.target:.0f}R/{int(best.hold)}h train {best[cols].mean():+.3f} -> block {k} {best[f'b{k}']:+.3f}")
    print(f"     selection premium {np.mean(prem):+.3f}; cells net>0: {(R.net > 0).sum()}/{len(R)}; cells 4/4: {(R.pos == 4).sum()}")
    return R
for fam in ("manual_scalp", "manual_scalp_h1dir", "manual_scalp_banded_h1dir"):
    surface(fam, ["BEATUSD"], "blk")
surface("manual_scalp_h1dir", THIN, "pblk")
print("\n== manual_scalp_h1dir per symbol, own blocks")
for t, h in ((1.0, 24), (3.0, 72), (4.0, 120)):
    for s in THIN:
        d = trades(s, "manual_scalp_h1dir", t, h); g = d.groupby("blk").r_multiple.mean()
        dd = float((d.r_multiple.cumsum().cummax() - d.r_multiple.cumsum()).max())
        print(f"  {t:.0f}R/{h:>3}h {s:<9}" + "".join(f" {g.get(b, np.nan):+.3f}({int((d.blk == b).sum()):>3})" for b in range(4)) + f"  {int((g > 0).sum())}/4  net {d.r_multiple.mean():+.3f}  DD {dd:5.1f}R  n={len(d)}")
print("\n== BEATUSD, manual_scalp 4R/72h: detail")
d = trades("BEATUSD", "manual_scalp", 4.0, 72); s_ = d.r_multiple.sort_values(ascending=False); tot = d.r_multiple.sum()
print(f"  n={len(d)} net {d.r_multiple.mean():+.3f} total {tot:+.1f}R  top3 {s_.head(3).sum() / tot:.0%} top10 {s_.head(10).sum() / tot:.0%}  mean without top10 {s_.iloc[10:].mean():+.3f}  median {d.r_multiple.median():+.3f}")
print(f"  exits {d.exit_reason.value_counts().to_dict()}  win {(d.r_multiple > 0).mean():.0%}  longs {d[d.side > 0].r_multiple.mean():+.3f}({(d.side > 0).sum()}) shorts {d[d.side < 0].r_multiple.mean():+.3f}({(d.side < 0).sum()})")
w = d[d.hour >= 18]; r = d[d.hour < 18]
print(f"  18-24 UTC {w.r_multiple.mean():+.3f}({len(w)})  rest {r.r_multiple.mean():+.3f}({len(r)})")
rng = np.random.default_rng(3); bs = [rng.choice(d.r_multiple.to_numpy(), len(d)).mean() for _ in range(3000)]
print(f"  bootstrap 95% [{np.percentile(bs, 2.5):+.3f}, {np.percentile(bs, 97.5):+.3f}]")
