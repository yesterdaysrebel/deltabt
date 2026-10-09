"""Exact engine re-run of the per-coin pause after a loss (2026-10-09).

    PYTHONPATH=. .venv/bin/python scripts/loss_pause_exact.py

WHY. scripts/loss_pause_lab.py filtered the uncapped trade list and found one
lead: on hold-to-3R, pausing a coin for 4-12h after a loss on it lifted net R
per trade from +0.044R to about +0.23R in both halves (family-wise p 0.043),
but failed its own second-half check. A filter cannot see that a skipped
trade frees the coin's slot for a different entry. This re-runs the rule
INSIDE the engine (RiskGates.pause_seconds / pause_after, off by default).

FIXED BEFORE THE RUN (owner approved 2026-10-09):
  * ONE cell: an 8-hour pause on the coin after a trade with net R < 0, timed
    from the exit bar. 8h is the middle of the 4-12h band that held in both
    halves; it is not re-chosen here.
  * Universe A (judged): BEATUSD, AKEUSD, BANKUSD, 2025-12-20..2026-09-30,
    filler bars dropped, ungated except one slot per coin, 72h hold,
    stop_fill ltp_close. Exits: hold-to-3R and the trail (0.5 / 0.5).
  * Arms per exit: no pause; loss pause 8h; win pause 8h (reported); and the
    CONTROL -- 200 runs pausing 8h after a RANDOM trade, with probability equal
    to the loss share of the no-pause run. Unpaired re-runs here swing by more
    than most effects (cooldown_bars 11 -> 12 moved sumR from -7.8 to +49.6),
    so the pause is judged against the same machinery firing at random, not
    against a single baseline run.

RULES, hold-to-3R on A (the lead under test):
  E1 loss-pause net R per trade beats the no-pause run AND at least 95% of
     the random-pause runs (p <= 0.05).
  E2 loss-pause beats the no-pause run in BOTH halves (split at the median
     entry time of the no-pause run).
  E1 and E2 = THE LEAD SURVIVES THE EXACT RE-RUN (still in-sample). Otherwise
  it is closed. The trail is reported under the same rules; it failed as the
  decision exit in the filter lab and nothing here reopens that.
  B (all 32 screen coins) is reported only: no pause, loss pause, win pause,
  40 random-pause runs.

FORWARD CHECK, registered now, run after 2026-12-31 only if E1 and E2 pass:
  universe A, 2026-10-01..2026-12-31, hold-to-3R, same arms. Confirmed if the
  loss pause beats no pause and beats random pausing at p <= 0.10. Nothing is
  built into the bot before that.

RESULT 2026-10-09 (out/sweep/five_min_arm_lab/loss_pause/exact_2026-10-09.txt):
THE LEAD IS CLOSED. No forward check.
  * A hold-to-3R: no pause +0.044R (428 trades); loss pause 8h -0.015R (398),
    halves -0.095 / +0.061; 32% of 200 random pauses did as well (p 0.32).
    E1 fail, E2 fail. The filter lab's +0.228R at 8h was the filter's own
    artefact: a skipped trade frees the coin, and the bot's next entry comes
    sooner than the filtered list assumed.
  * A trail: -0.033R -> -0.029R, p 0.19 vs random. Nothing.
  * Random pauses alone spread hold-to-3R from -0.151R to +0.049R (5-95%): the
    unpaired noise floor is wider than the effect the filter promised.
  * B (reported): the trail loss pause beat all 40 random runs (-0.079R vs
    -0.091R; no pause -0.097R) -- a ~0.02R loss reduction on a strategy still
    losing ~0.08R a trade. Not a filter worth building.
"""
import dataclasses, glob, sys
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

PAUSE = 8 * 3600; EXITS = {"hold-to-3R": {}, "trail": dict(trail_after_r=0.5, trail_r=0.5)}
SPEC = build_spec("manual_scalp_both_t3", 5); BOOKS = {}; FUND = {}


def load(universe):
    cat = ProductCatalog(); cache = {}; books = {}; fund = {}
    for s in universe:
        d = load_symbol(s); l = d["ltp"]; l = l[~((l.volume == 0) & (l.open == l.close) & (l.high == l.low))].reset_index(drop=True)
        d = dict(symbol=s, ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
        P, mk, tr = _resampled(d, 5, cache); C = _resampled(d, 1, cache)[0]
        books[s] = Book(symbol=s, bars=P, signals=rulecore.to_engine_signals(rulecore.compute(P, C, SPEC)),
                        costs=SymbolCosts.from_spec(cat.get(s)), mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"])
        fund[s] = d["funding"]
    return books, fund


def run(key, exit_name, mode, p=0.0, seed=0):
    books, fund = BOOKS[key], FUND[key]
    if mode == "none":
        sel = None
    elif mode == "loss":
        sel = lambda t: t.r_multiple < 0
    elif mode == "win":
        sel = lambda t: t.r_multiple > 0
    else:
        rng = np.random.default_rng(seed); sel = lambda t: rng.random() < p
    gates = replace(RiskGates.off(), max_open_positions=len(books), pause_seconds=PAUSE if sel else 0, pause_after=sel)
    params = replace(params_for(SPEC, 5, 72), stop_fill="ltp_close", **EXITS[exit_name])
    res = run_portfolio(books, params, gates, initial_capital=10_000.0, funding=fund)
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    return t.entry_time.to_numpy("int64"), t.r_multiple.to_numpy(float)


def _random(args):
    return run(*args)[1].mean()


def report(key, n_random, judged):
    print(f"\n==== {key}: {len(BOOKS[key])} coins ====")
    verdict = {}
    for ex in EXITS:
        et0, r0 = run(key, ex, "none"); split = np.median(et0); p = float((r0 < 0).mean())
        etl, rl = run(key, ex, "loss"); etw, rw = run(key, ex, "win")
        with ProcessPoolExecutor() as pool:
            null = np.array(list(pool.map(_random, [(key, ex, "random", p, 1000 + k) for k in range(n_random)])))
        def line(name, et, r):
            h1, h2 = r[et < split].mean(), r[et >= split].mean()
            return (f"  {name:24s} trades {len(r):5d}  net {r.mean():+.3f}R  win {100 * (r > 0).mean():5.1f}%  total {r.sum():+8.1f}R"
                    f"  halves {h1:+.3f} / {h2:+.3f}")
        print(f"\n  {ex}  (random pauses fire with p = {p:.3f}, the no-pause loss share; cooldown_bars {params_for(SPEC, 5, 72).cooldown_bars})")
        print(line("no pause", et0, r0)); print(line("loss pause 8h", etl, rl)); print(line("win pause 8h (reported)", etw, rw))
        pr = float((null >= rl.mean()).mean())
        print(f"  random pause 8h x{n_random}: mean {null.mean():+.3f}R, 5-95% {np.percentile(null, 5):+.3f} .. {np.percentile(null, 95):+.3f}; "
              f"loss pause beaten by {100 * pr:.1f}% of them (p {pr:.3f})")
        if judged:
            E1 = rl.mean() > r0.mean() and pr <= 0.05
            E2 = rl[etl < split].mean() > r0[et0 < split].mean() and rl[etl >= split].mean() > r0[et0 >= split].mean()
            verdict[ex] = (E1, E2)
            print(f"  E1 beats no pause and p <= 0.05 vs random: {'PASS' if E1 else 'fail'}   E2 beats no pause in both halves: {'PASS' if E2 else 'fail'}")
    return verdict


if __name__ == "__main__":
    BOOKS["A"], FUND["A"] = load(["BEATUSD", "AKEUSD", "BANKUSD"])
    v = report("A", 200, True)
    B = sorted(f.split("/")[-1][:-4] for f in glob.glob("out/sweep/five_min_arm_lab/symbol_screen/trades/*.csv"))
    BOOKS["B"], FUND["B"] = load(B)
    report("B", 40, False)
    h = v["hold-to-3R"]; t = v["trail"]
    print(f"\nVERDICT hold-to-3R (the lead): {'SURVIVES the exact re-run -- forward check after 2026-12-31' if all(h) else 'CLOSED'}"
          f"; trail {'passes' if all(t) else 'fails'} (reported)")
