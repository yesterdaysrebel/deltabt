"""The paired three-exit lab on ANY universe, with the pilot's $250 sizing (2026-10-05).

    PYTHONPATH=. .venv/bin/python scripts/paired_exits_universe.py ETHUSD,SOLUSD,XRPUSD

Runs scripts/paired_exits_lab.py unchanged except for its UNIVERSE line, then
sizes every trade as the prod dry run does ($250, 2%, one-contract floor 3%,
3x leverage cap) and reports each exit rule's dollars, drawdown, and the share
of 21-day windows in which the account falls 20% / 50% from its peak.

RESULT 2026-10-05 (out/sweep/five_min_arm_lab/symbol_screen/portfolios_2026-10-05.txt),
2025-12-21..2026-09-30, the TRAIL as the account's exit:
    universe                    trades  per 21d   total $   20% line   50% line
    BEATUSD                        291       23      -57         0%         0%
    BEAT, AKE, BANK (as run)       377       29      -12         0%         0%
    SOLUSD, BEATUSD                581       45     -164        18%         0%
    ETH, SOL, XRP                  817       62     -337        33%         0%
    ETH, SOL, XRP, BEAT           1108       85     -393        52%         0%
More symbols is more trades, and every trade pays fees: the sets that are
MEASURED best lose most. No set is expected to make money (scripts/symbol_screen.py).
"""
import pathlib, sys, math
import numpy as np, pandas as pd
uni = sys.argv[1].split(",")
src = pathlib.Path("scripts/paired_exits_lab.py").read_text()
src = src.replace('UNIVERSE = ["BEATUSD", "AKEUSD", "BANKUSD"]', f"UNIVERSE = {uni!r}")
assert f"UNIVERSE = {uni!r}" in src
g = {"__name__": "lab", "__file__": str(pathlib.Path("scripts/paired_exits_lab.py").resolve())}
exec(compile(src, "paired_exits_lab", "exec"), g)
res = g["res"]; key = ["symbol", "side", "entry_time"]
common = set(map(tuple, res["baseline"][key].values))
for k in ("ladder", "trail"): common &= set(map(tuple, res[k][key].values))
R = {k: v[v[key].apply(tuple, axis=1).isin(common)].sort_values("exit_time").reset_index(drop=True) for k, v in res.items()}
EQ, RISK, CAP, LEV = 250.0, 0.02, 0.03, 3.0
CV = {s: float(g["fb"][s].costs.contract_value) for s in uni}
def size(row):
    per = row.risk_per_unit * CV[row.symbol]                       # $ risk of one contract
    n = min(int(EQ * RISK / per), int(EQ * LEV / (row.entry_price * CV[row.symbol])))
    if n <= 0: n = 1 if per <= EQ * CAP and row.entry_price * CV[row.symbol] <= EQ * LEV else 0
    return n, n * per
print("\n== per symbol: stop width, cost, and sizing on $250 at 2% (floor cap 3%, leverage cap 3x)")
b = R["baseline"]
for s in uni:
    d = b[b.symbol == s]
    stop_pct = 100 * d.risk_per_unit / d.entry_price
    sz = d.apply(size, axis=1, result_type="expand"); n, usd = sz[0], sz[1]
    days = (d.entry_time.max() - d.entry_time.min()) / 86400
    print(f"  {s:8s} n={len(d):4d} ({len(d)/days*21:.0f} per 21 days)  stop% median {stop_pct.median():.2f} (p10 {stop_pct.quantile(.1):.2f}, p90 {stop_pct.quantile(.9):.2f})"
          f"  cost/R median {d.cost_per_r.median():.3f}  contracts median {n.median():.0f} (skipped {100*(n==0).mean():.0f}%, floor-sized {100*((n==1)&(d.risk_per_unit*CV[s]>EQ*RISK)).mean():.0f}%)"
          f"  $ at risk median {usd[n>0].median():.2f} ({100*usd[n>0].median()/EQ:.1f}%)  price {d.entry_price.iloc[-1]:.4g}")
print("\n== the account in dollars on $250 (each exit rule on its own), trades skipped by sizing removed")
DAY = 86400
for k in ("baseline", "ladder", "trail"):
    d = R[k].copy(); sz = d.apply(size, axis=1, result_type="expand"); d["usd_r"] = sz[1]; d = d[sz[0] > 0]
    usd = (d.r_multiple * d.usd_r).to_numpy(); ex = d.exit_time.to_numpy()
    e = p = mdd = 0.0
    for x in usd: e += x; p = max(p, e); mdd = max(mdd, p - e)
    out = {}
    for lim in (50, 125):
        hits = wins = 0
        for start in range(int(ex.min()), int(ex.max() - 21 * DAY), DAY):
            m = (ex >= start) & (ex < start + 21 * DAY)
            if not m.any(): continue
            wins += 1; e = p = 0.0
            for x in usd[m]:
                e += x; p = max(p, e)
                if p - e >= lim: hits += 1; break
        out[lim] = 100 * hits / max(wins, 1)
    by = {s: (d[d.symbol == s].r_multiple * d[d.symbol == s].usd_r).sum() for s in uni}
    print(f"  {k:9s} trades {len(d):4d}  total ${usd.sum():+7.0f}  max drawdown ${mdd:5.0f}  21-day windows hitting 20% {out[50]:3.0f}%  50% {out[125]:3.0f}%   by symbol " + "  ".join(f"{s} ${v:+.0f}" for s, v in by.items()))
