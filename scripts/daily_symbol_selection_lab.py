"""Does picking 'the profitable coins' each day work? (2026-10-05)

    PYTHONPATH=. .venv/bin/python scripts/daily_symbol_selection_lab.py \\
        out/sweep/five_min_arm_lab/symbol_screen

Owner's question after the 32-symbol screen (scripts/symbol_screen.py): rather
than a fixed list, select the coins that are profitable and re-select DAILY.

THE TEST, walk-forward, no look-ahead. Input: the screen's per-trade file for
each of the 32 symbols (the dry run's own spec, three exits on identical
entries). For each UTC day D:
  * score every symbol on what was KNOWN before D -- only trades that had
    already CLOSED in the lookback window [D - L, D);
  * pick symbols by the rule; "trade" = the entries that fell on day D in the
    picked symbols; their result is whatever those trades went on to do.
Selectors:
  profit  top-K by the strategy's summed R on that symbol over the lookback
  avg     top-K by its average R (needs >= 3 closed trades)
  winners every symbol whose lookback sum is positive
  cheap   top-K by lowest fee cost per R (the cost law; widest stops)
  worst   bottom-K by summed R (a control: if 'profit' works, this must fail)
Lookbacks 3, 7, 14, 30 days; K = 1, 3, 5; both hold-to-3R and the trail.

HOW IT IS JUDGED. Against (a) trading every symbol on the same days and
(b) picking the same number of symbols AT RANDOM each day, 400 times: the
p-value is the share of random pickers that did at least as well. There are
~50 selector cells per exit rule, so some will look good by luck: the grid is
shown whole, and the best cell of the FIRST half of the period is re-scored on
the SECOND half, which it never saw.

RESULT 2026-10-05 (out/sweep/five_min_arm_lab/symbol_screen/daily_selection_2026-10-05.txt),
4,950 trades, 32 symbols, 285 days; trading everything = -0.083R (hold-to-3R)
and -0.082R (trail) per trade:
  * PICKING RECENT WINNERS DOES NOT WORK. Under the trail all 12 'profit'
    cells are negative (-0.09 to -0.16R) and WORSE than trading everything;
    'profit' beat its mirror image 'worst' in 0 of 12 matched cells (gap
    -0.055R). Under hold-to-3R it is 7 of 12 (+0.026R): nothing. 'winners'
    (every coin with a positive lookback) = -0.08 to -0.11R. No 'profit',
    'avg' or 'winners' cell beats random picking at p <= 0.05, and the best
    first-half cell (profit K=3 30d, +0.109R) scored -0.042R in the second.
  * THE ONLY SELECTOR THAT HELPS IS 'CHEAP' -- the cost law, not profit.
    Under the trail, picking the 3-5 coins with the lowest fee cost per R
    gives about -0.02 to +0.02R per trade, beats random picking at p 0.00-0.03
    in five cells (two expected by luck) and is the same in both halves. It
    lifts the result to roughly BREAKEVEN, not to profit (t by day <= +0.5).
  * WHY: the loss IS the fee. All trades by fee cost per trade, hold-to-3R /
    trail net: under 0.03R -> +0.034R / -0.008R (843 trades); 0.03-0.06R ->
    -0.035 / -0.051; 0.06-0.10R -> -0.103 / -0.097; over 0.10R -> -0.176 /
    -0.142. Gross is about zero in every bucket.
  * AND THE CHEAP COINS ARE THE THIN ONES (BEATUSD, BLESSUSD, AIOUSD, AKEUSD,
    VELVETUSD, AINUSD ...), idle 30-80% of minutes, where the simulated fill is
    least trustworthy (live BEATUSD stops overshot by 0.25R). Breakeven in the
    backtest there is more likely a loss live.
"""
import glob, math, sys
import numpy as np, pandas as pd

D = 86400
T = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{sys.argv[1]}/trades/*.csv"))]).reset_index(drop=True)
T["day"] = T.entry_time // D
SYMS = sorted(T.symbol.unique())
RNG = np.random.default_rng(20261005)


def scores(rule, lookback):
    """{day: {symbol: (sum R, mean R, n, median cost)}} from trades CLOSED in [day - L, day)."""
    r, ex = T[f"r_{rule}"].to_numpy(), T[f"exit_{rule}"].to_numpy()
    out = {}
    for day in range(int(T.day.min()) + lookback, int(T.day.max()) + 1):
        m = (ex >= (day - lookback) * D) & (ex < day * D)
        g = T[m].assign(r=r[m]).groupby("symbol")
        out[day] = {s: (float(d.r.sum()), float(d.r.mean()), len(d), float(d.cost_per_r.median())) for s, d in g}
    return out


def pick(sc, selector, k):
    if selector == "profit":
        return [s for s, _ in sorted(sc.items(), key=lambda kv: -kv[1][0])[:k]]
    if selector == "worst":
        return [s for s, _ in sorted(sc.items(), key=lambda kv: kv[1][0])[:k]]
    if selector == "avg":
        ok = {s: v for s, v in sc.items() if v[2] >= 3}
        return [s for s, _ in sorted(ok.items(), key=lambda kv: -kv[1][1])[:k]]
    if selector == "winners":
        return [s for s, v in sc.items() if v[0] > 0]
    if selector == "cheap":
        ok = {s: v for s, v in sc.items() if v[2] >= 3}
        return [s for s, _ in sorted(ok.items(), key=lambda kv: kv[1][3])[:k]]
    raise ValueError(selector)


def evaluate(rule, lookback, selector, k, days=None, n_random=400):
    sc = scores(rule, lookback) if (rule, lookback) not in CACHE else CACHE[(rule, lookback)]
    CACHE[(rule, lookback)] = sc
    by_day = {d: g for d, g in T.groupby("day")}
    sel_r, all_r, sel_days, picks_per_day = [], [], [], []
    rnd = [[] for _ in range(n_random)]
    for day, s in sc.items():
        if days is not None and not (days[0] <= day < days[1]):
            continue
        g = by_day.get(day)
        if g is None or not s:
            continue
        chosen = pick(s, selector, k)
        if not chosen:
            continue
        picks_per_day.append(len(chosen))
        r = g[f"r_{rule}"].to_numpy(); sym = g.symbol.to_numpy()
        hit = np.isin(sym, chosen)
        sel_r += list(r[hit]); sel_days += [day] * int(hit.sum()); all_r += list(r)
        pool = list(s)                                   # symbols the selector could have picked
        for j in range(n_random):
            alt = RNG.choice(pool, size=min(len(chosen), len(pool)), replace=False)
            rnd[j] += list(r[np.isin(sym, alt)])
    if len(sel_r) < 20:
        return None
    sel = np.array(sel_r); daily = pd.Series(sel).groupby(np.array(sel_days)).sum()
    t = daily.mean() / (daily.std() / math.sqrt(len(daily))) if len(daily) > 2 and daily.std() > 0 else float("nan")
    rmeans = np.array([np.mean(x) for x in rnd if len(x) >= 20])
    p = float((rmeans >= sel.mean()).mean()) if len(rmeans) else float("nan")
    return dict(n=len(sel), mean=float(sel.mean()), total=float(sel.sum()), t=float(t), all=float(np.mean(all_r)),
                rand=float(rmeans.mean()) if len(rmeans) else float("nan"), p=p, picks=float(np.mean(picks_per_day)))


CACHE = {}
CELLS = [(sel, k) for sel in ("profit", "avg", "cheap", "worst") for k in (1, 3, 5)] + [("winners", 0)]
LOOKBACKS = (3, 7, 14, 30)
span = (int(T.day.min()), int(T.day.max()) + 1); mid = (span[0] + span[1]) // 2
print(f"{len(T)} trades, {len(SYMS)} symbols, {span[1] - span[0]} days. Every figure is net R per trade after fees.\n")
for rule in ("baseline", "trail"):
    label = {"baseline": "HOLD TO 3R", "trail": "TRAIL"}[rule]
    print(f"== {label}: all symbols, every day = {T[f'r_{rule}'].mean():+.3f}R per trade")
    print(f"{'selector':9s}{'K':>3s}{'lookback':>9s}{'trades':>8s}{'per 21d':>8s}{'avg R':>8s}{'total R':>9s}{'t by day':>9s}"
          f"{'all syms':>9s}{'random':>8s}{'p vs random':>12s}{'1st half':>9s}{'2nd half':>9s}")
    res = []
    for sel, k in CELLS:
        for lb in LOOKBACKS:
            e = evaluate(rule, lb, sel, k)
            if e is None:
                continue
            h1 = evaluate(rule, lb, sel, k, days=(span[0], mid), n_random=0)
            h2 = evaluate(rule, lb, sel, k, days=(mid, span[1]), n_random=0)
            res.append((sel, k, lb, e, h1, h2))
            f = lambda h: f"{h['mean']:+9.3f}" if h else f"{'—':>9s}"
            print(f"{sel:9s}{(k or '-'):>3}{lb:>8d}d{e['n']:8d}{e['n'] / (span[1] - span[0] - lb) * 21:8.0f}{e['mean']:+8.3f}{e['total']:+9.1f}"
                  f"{e['t']:+9.1f}{e['all']:+9.3f}{e['rand']:+8.3f}{e['p']:12.2f}{f(h1)}{f(h2)}")
    real = [x for x in res if x[0] != "worst"]
    pos = sum(1 for x in real if x[3]["mean"] > 0); sig = sum(1 for x in real if x[3]["p"] <= 0.05)
    print(f"  {len(real)} selector cells (excluding the 'worst' control): {pos} have a positive average, "
          f"{sig} beat random picking at p <= 0.05 (about {0.05 * len(real):.1f} expected by luck alone)")
    both = [x for x in real if x[4] and x[5]]
    best = max(both, key=lambda x: x[4]["mean"])
    print(f"  best cell of the FIRST half: {best[0]} K={best[1] or '-'} lookback {best[2]}d = {best[4]['mean']:+.3f}R "
          f"({best[4]['n']} trades) -> SECOND half {best[5]['mean']:+.3f}R ({best[5]['n']} trades)")
    h1 = np.array([x[4]["mean"] for x in both]); h2 = np.array([x[5]["mean"] for x in both])
    print(f"  across cells, first-half vs second-half average R: correlation {np.corrcoef(h1, h2)[0, 1]:+.2f}")
    pw = [(x[3]["mean"], y[3]["mean"]) for x in res for y in res
          if x[0] == "profit" and y[0] == "worst" and x[1] == y[1] and x[2] == y[2]]
    print(f"  'profit' beat its mirror image 'worst' in {sum(a > b for a, b in pw)} of {len(pw)} matched cells "
          f"(average gap {np.mean([a - b for a, b in pw]):+.3f}R)\n")
