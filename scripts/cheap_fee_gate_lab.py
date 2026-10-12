"""Does taking only CHEAP trades survive realistic spreads? (2026-10-12)

    PYTHONPATH=. .venv/bin/python scripts/cheap_fee_gate_lab.py

WHY. Owner approved, after the research so far found exactly one rule that
helps this strategy: trade cheaply. Trades whose fee cost per R was under
0.03R made +0.034R (hold-to-3R) against -0.083R for all; the cheapest 10% of
trades made +0.112R (356 trades, p 0.005 vs random, not distinguishable from
zero). Cost per R = round trip / stop width, so "cheap" means a WIDE STOP, and
those trades sit on thin coins -- where the flat 2 bps slippage the backtest
assumes is least true (BEATUSD quotes 22.7 bps).

THE RULE: take a trade only if its cost per R (known before entry) is <= 0.03,
through the engine's own path (RiskGates.entry_filter, right after the existing
0.15 gate). 0.03 was READ FROM DATA ALREADY SEEN, so this stage can only show
whether the rule SURVIVES exact simulation with realistic costs. It cannot
prove it. A pass leads to stage 2 (an older, untouched window), asked for
separately.

COSTS. Per coin, slippage = max(2 bps, half the quoted spread in the
2026-10-05 ticker snapshot); STRESS = max(2 bps, the whole spread). One snapshot
is a rough guide, hence the stress.

ARMS, per universe x exit x cost level:
  base     the 0.15 gate only (today's backtest rule)
  gate     cost per R <= 0.03 (0.02 and 0.05 reported)
  random   the 0.15 gate, then refuse entries AT RANDOM at the rate the 0.03
           gate refused them in the same setting -- 200 runs (prod universe)
           / 100 runs (32 coins). A gate is judged against the same machinery
           firing at random, never against one baseline run.

RULES, written before the run. Judged on the 32 coins, hold-to-3R, normal costs,
0.03 gate:
  S1 net R per trade of the gated trades > 0
  S2 beats the random-refusal runs at p <= 0.05
  S3 net R per trade > 0 in BOTH halves (split at the base run's median entry)
  SURVIVES = S1 and S2 and S3. Otherwise the cheap-trade idea is closed.
Reported, not judged: the trail, the prod universe, the 2x spread stress, 0.02
and 0.05.

RESULT 2026-10-12 (out/sweep/five_min_arm_lab/cheap_gate/result_2026-10-12.txt):
SURVIVES by the letter of the rule -- and is weak.
  * 32 coins, hold-to-3R, normal costs: base -0.094R (4,928 trades); 0.03 gate
    +0.027R (713 trades, 76/month), halves +0.037 / +0.022; beaten by 4% of 100
    random-refusal runs (p 0.04; those runs took 821-871 trades, so the control
    is matched). S1, S2, S3 pass.
  * But +0.027R has a standard error of 0.067 (t +0.41): not distinguishable
    from zero. It beats random refusal because random picks carry average
    costs (0.078R vs 0.024R) -- the cost law again.
  * Knife-edge: 0.02 gate -0.064R, 0.05 gate -0.032R; the one positive
    threshold is the one read from the data.
  * 2x spreads: 0.03 gate -0.042R (p 0.23). The trail loses at every threshold.
  * Prod 3 coins: 0.03 gate +0.259R on 100 trades, but the first half holds only
    a handful of them (+2.137R) and the second +0.073R; 2x spreads +0.026R.
  Stage 2 (older untouched window) is the only test that can decide it.
"""
import dataclasses, glob, json, pathlib, sys
from concurrent.futures import ProcessPoolExecutor
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

SPEC = build_spec("manual_scalp_both_t3", 5)
EXITS = {"hold-to-3R": {}, "trail": dict(trail_after_r=0.5, trail_r=0.5)}
SPREAD = {r["symbol"]: float(r["spread_bps"]) for r in
          json.load(open("out/sweep/five_min_arm_lab/symbol_screen/tickers_2026-10-05.json"))}
SLIP = {"normal": lambda s: max(2.0, SPREAD[s] / 2), "2x spread": lambda s: max(2.0, SPREAD[s])}
UNIVERSES = {"32 coins": sorted(pathlib.Path(f).stem for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv")),
             "prod 3": ["BEATUSD", "AKEUSD", "BANKUSD"]}
N_RANDOM = {"32 coins": 100, "prod 3": 200}
DATA, SIGS, BOOKS = {}, {}, {}


def load(syms):
    cat = ProductCatalog(); cache = {}
    for s in syms:
        if s in DATA:
            continue
        d = load_symbol(s); l = d["ltp"]; l = l[~((l.volume == 0) & (l.open == l.close) & (l.high == l.low))].reset_index(drop=True)
        DATA[s] = dict(symbol=s, ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
        P, mk, tr = _resampled(DATA[s], 5, cache); C = _resampled(DATA[s], 1, cache)[0]
        SIGS[s] = (P, mk, tr, rulecore.to_engine_signals(rulecore.compute(P, C, SPEC)), SymbolCosts.from_spec(cat.get(s)))


def books(uni, cost):
    out = {}
    for s in UNIVERSES[uni]:
        P, mk, tr, sig, c = SIGS[s]
        out[s] = Book(symbol=s, bars=P, signals=sig, costs=replace(c, slippage_bps=SLIP[cost](s)), mark=mk,
                      tradable=tr, fill_ltp=DATA[s]["ltp"], fill_mark=DATA[s]["mark"])
    return out


def run(uni, cost, ex, keep=None):
    bks = BOOKS[(uni, cost)]
    gates = replace(RiskGates.off(), max_open_positions=len(bks), entry_filter=keep)
    params = replace(params_for(SPEC, 5, 72), stop_fill="ltp_close", **EXITS[ex])
    res = run_portfolio(bks, params, gates, initial_capital=10_000.0, funding={s: DATA[s]["funding"] for s in bks})
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    return t.entry_time.to_numpy("int64"), t.r_multiple.to_numpy(float), t.cost_per_r.to_numpy(float)


def _random(args):
    uni, cost, ex, k, seed = args
    rng = np.random.default_rng(seed)
    return run(uni, cost, ex, keep=lambda s, ts, c: rng.random() < k)[1].mean()


def gate(x, counter):
    def keep(s, ts, c):
        counter[0] += 1; ok = c <= x; counter[1] += ok
        return ok
    return keep


def report(uni, cost, ex, judged):
    et0, r0, c0 = run(uni, cost, ex); split = np.median(et0)
    print(f"\n  {ex}, {cost} costs")
    def line(name, et, r):
        h1, h2 = r[et < split], r[et >= split]
        days = (et.max() - et.min()) / 86400 if len(et) > 1 else 1
        return (f"    {name:22s} trades {len(r):5d} ({30 * len(r) / max(days, 1):5.1f}/month)  net {r.mean():+.3f}R  win {100 * (r > 0).mean():5.1f}%"
                f"  total {r.sum():+8.1f}R  halves {h1.mean():+.3f} / {h2.mean():+.3f}")
    print(line("base (0.15 gate)", et0, r0) + f"  avg cost {c0.mean():.3f}R")
    out = None
    for x in (0.03, 0.02, 0.05):
        cnt = [0, 0]; et, r, c = run(uni, cost, ex, keep=gate(x, cnt))
        print(line(f"cost per R <= {x}", et, r) + f"  avg cost {c.mean():.3f}R")
        if x == 0.03:
            k = cnt[1] / max(cnt[0], 1); g = (et, r)
    with ProcessPoolExecutor() as pool:
        null = np.array(list(pool.map(_random, [(uni, cost, ex, k, 5000 + j) for j in range(N_RANDOM[uni])])))
    et, r = g; p = float((null >= r.mean()).mean())
    print(f"    random refusal (keep {100 * k:.0f}% of entries) x{N_RANDOM[uni]}: mean {null.mean():+.3f}R, 5-95% {np.percentile(null, 5):+.3f} .. "
          f"{np.percentile(null, 95):+.3f}; the 0.03 gate is beaten by {100 * p:.1f}% of them (p {p:.3f})")
    if judged:
        S1 = r.mean() > 0; S2 = p <= 0.05; S3 = r[et < split].mean() > 0 and r[et >= split].mean() > 0
        print(f"    S1 net > 0: {'PASS' if S1 else 'fail'}   S2 p <= 0.05 vs random: {'PASS' if S2 else 'fail'}   "
              f"S3 > 0 in both halves: {'PASS' if S3 else 'fail'}")
        out = S1 and S2 and S3
    return out


if __name__ == "__main__":
    load(sorted(set(UNIVERSES["32 coins"]) | set(UNIVERSES["prod 3"])))
    for uni in UNIVERSES:
        for cost in SLIP:
            BOOKS[(uni, cost)] = books(uni, cost)
    verdict = None
    for uni in UNIVERSES:
        print(f"\n==== {uni} ====")
        for ex in EXITS:
            for cost in SLIP:
                v = report(uni, cost, ex, judged=(uni == "32 coins" and ex == "hold-to-3R" and cost == "normal"))
                verdict = v if v is not None else verdict
    print(f"\nVERDICT: {'SURVIVES -- stage 2 (older window) needs its own yes' if verdict else 'CLOSED'}")
