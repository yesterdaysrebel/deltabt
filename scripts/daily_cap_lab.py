"""Does a daily trade cap raise the win rate? (2026-10-09)

    PYTHONPATH=. .venv/bin/python scripts/daily_cap_lab.py

WHY. Owner: "in prod we will take only 2 trades per day". The engine's own
RiskGates.max_trades_per_day (UTC day, as the live engine) on the prod
universe (BEATUSD, AKEUSD, BANKUSD), 2025-12-20..2026-09-30, filler bars
dropped, hold-to-3R and the trail; caps 20 (none), 3, 2, 1. Plus a subset
check on the uncapped list: the first 2 entries of each day against 2 random
entries of the same day (4,000 draws).

RESULT 2026-10-09 (out/sweep/five_min_arm_lab/daily_cap/result_2026-10-09.txt):
a cap does not raise the win rate. Hold-to-3R +0.044R uncapped -> -0.084R at
2/day (win 29% -> 26%); trail -0.028R -> -0.048R. The first 2 of a day did no
better than 2 random ones (hold-to-3R -0.004R vs +0.097R; trail -0.046R vs
-0.035R). A cap selects by the clock. The dry run's own 46 closed trades
agreed (win rate lower under the cap; p 0.24-0.42 vs random) -- too few to
decide; that check read prod data and is not in this public repo.
"""
import dataclasses, sys, pathlib
from dataclasses import replace
import numpy as np, pandas as pd
sys.path.insert(0, ".")
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from deltabt.data.quality import tradable_mask
from deltabt.harness import _resampled, load_symbol, params_for
from deltabt.portfolio import Book, RiskGates, run_portfolio
U = ["BEATUSD", "AKEUSD", "BANKUSD"]; cat = ProductCatalog(); cache = {}; spec = build_spec("manual_scalp_both_t3", 5)
def clean(d):
    l = d["ltp"]; l = l[~((l.volume == 0) & (l.open == l.close) & (l.high == l.low))].reset_index(drop=True)
    return dict(symbol=d["symbol"], ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
DATA = {s: clean(load_symbol(s)) for s in U}
books = {}
for s, d in DATA.items():
    P, mk, tr = _resampled(d, 5, cache); C = _resampled(d, 1, cache)[0]
    books[s] = Book(symbol=s, bars=P, signals=rulecore.to_engine_signals(rulecore.compute(P, C, spec)), costs=SymbolCosts.from_spec(cat.get(s)),
                    mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
def run(cap, **extra):
    params = replace(params_for(spec, 5, 72), stop_fill="ltp_close", **extra)
    res = run_portfolio(books, params, replace(RiskGates.off(), max_open_positions=3, max_trades_per_day=cap),
                        initial_capital=10_000.0, funding={s: DATA[s]["funding"] for s in books})
    return pd.DataFrame([dataclasses.asdict(t) for t in res.trades])
EXITS = {"hold-to-3R": {}, "trail": dict(trail_after_r=0.5, trail_r=0.5)}
rcol = None
for ex, kw in EXITS.items():
    print(f"\n== {ex} ==")
    for cap in (20, 3, 2, 1):
        t = run(cap, **kw)
        if rcol is None:
            rcol = next(c for c in ("r_multiple", "net_r", "r") if c in t.columns)
        r = t[rcol].astype(float); day = t.entry_time // 86400
        blk = pd.qcut(t.entry_time.rank(method="first"), 4, labels=False)
        print(f"cap {cap:2d}/day: trades {len(t):4d} ({len(t)/day.nunique():.2f}/active day)  net {r.mean():+.3f}R  "
              f"win {100*(r>0).mean():.0f}%  3R hit {100*(r>=2).mean():.1f}%  sum {r.sum():+.1f}R  "
              f"blocks {' '.join(f'{x:+.2f}' for x in r.groupby(blk).mean())}")
        if cap == 20:
            base = t.assign(r=r, day=day)
    # day-matched random 2/day from the uncapped list vs first-2-of-day from it
    first2 = base.sort_values("entry_time").groupby("day").head(2); k = first2.groupby("day").size()
    rng = np.random.default_rng(7); draws = []
    groups = {d: g.r.to_numpy() for d, g in base.groupby("day")}
    for _ in range(4000):
        draws.append(np.mean(np.concatenate([rng.choice(groups[d], n, replace=False) for d, n in k.items()])))
    print(f"subset check: first 2 of each day {first2.r.mean():+.3f}R (n {len(first2)}) vs random 2 of the same days {np.mean(draws):+.3f}R; "
          f"p(random >= first-2) {np.mean(np.array(draws) >= first2.r.mean()):.2f}")
