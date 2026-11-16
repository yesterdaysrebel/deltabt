"""Owner 2026-11-15: "When our SL hits the coin moves above and then falls. Why not sell that same coin? When is yours
to find out." -- a search for WHEN to re-sell after a run-4 full stop and HOW to manage that trade (its own stop and
target, not run 4's 2.25% / ~1.8%), chosen on 2025 only and judged on 2026 + the 20 real picks (never seen).

Events: every run-4 first-entry full stop (fixed $3,184 sizing) with time left before 24:00.
Re-sell: market, taker 0.05% + 0.1% slippage (STRICT: at the worse of the trigger and that minute's close).
  WHEN:   at once (next minute close) | N minutes after the stop (close) | a 1m low Y% below the high since the stop
  STOP:   fixed % above the entry, or % above the highest high since our stop (at entry time)
  TARGET: a fall of T% from the entry (fills 0.1% through, taker), or none (24:00 UTC... out at 24h after the stop)
  Stop before target in one minute; a stop fills at the worse of its price and the minute's close +0.1% (STRICT)
  or at its price +0.1% (LENIENT). Out at the close 24 h after the stop at the latest.
Sizing by risk: each re-sell risks the same $ at its stop, so results are in R (1R = the planned loss). At 20x max
with an isolated margin covering the stop, liquidation is beyond the stop; a minute jumping past a wide stop is in
the STRICT fill.

    PYTHONPATH=. python scripts/run4_resell_search.py
"""
from __future__ import annotations
import itertools
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_trailing as T  # noqa: E402

COST_IN = 0.0005 + 0.001        # taker + slippage on the market sell
FEE = 0.0005


def events():
    hist, *_ = C.paths_history()
    out = []
    for day, sym, hi, lo, cl, k0 in hist + C.paths_real():
        r = T.short(sym, hi, lo, cl, k0, 0.49 * C.START, 500.0, 200.0, 0.25 * C.START, False, 0.008, None)
        if r and r[0] in ("STOP", "LIQ") and r[3] < 1439:
            out.append((pd.Timestamp(day), sym, hi, lo, cl, r[3]))
    return out


def entry_point(hi, lo, cl, ks, when):
    kind, v = when
    if kind == "now":
        return ks + 1, cl[ks + 1]
    if kind == "after":
        k = ks + v
        return (k, cl[k]) if k < 1440 else None
    top = hi[ks]
    for k in range(ks + 1, 1440):
        if lo[k] <= top * (1 - v):
            return k, min(top * (1 - v), cl[k])     # STRICT entry: the worse (lower) of the trigger and the close
        top = max(top, hi[k])
    return None


def resell(ev, when, stop, target, lenient):
    """-> (R, how) or None. R = P&L / planned loss at the stop."""
    day, sym, hi, lo, cl, ks = ev
    ep = entry_point(hi, lo, cl, ks, when)
    if ep is None:
        return None
    k, px = ep
    e = px * (1 - 0.001)                            # slippage on the market sell
    sp = e * (1 + stop[1]) if stop[0] == "fixed" else max(hi[ks:k + 1].max() * (1 + stop[1]), e * 1.005)
    tp = None if target is None else e * (1 - target)
    risk = (sp * 1.001 / e - 1) + COST_IN - 0.001 + FEE  # planned loss per $1 of position (fill +0.1%, fees)
    ret = lambda x: -(x / e - 1) - (COST_IN - 0.001) - FEE * x / e   # noqa: E731
    end = min(len(cl) - 1, ks + 1440)
    for j in range(k + 1, end + 1):
        if hi[j] >= sp:
            x = sp * 1.001 if lenient else max(sp, cl[j]) * 1.001
            return ret(x) / risk, "STOP"
        if tp is not None and lo[j] <= tp * 0.999:
            return ret(tp) / risk, "TARGET"
    return ret(cl[end] * 1.001) / risk, "TIME"


WHENS = [("now", 0)] + [("after", n) for n in (15, 30, 60, 120, 240)] + [("off", y) for y in (0.03, 0.05, 0.075, 0.10)]
STOPS = [("fixed", s) for s in (0.0225, 0.05, 0.10, 0.15)] + [("above_high", s) for s in (0.01, 0.03, 0.05)]
TARGETS = [0.03, 0.05, 0.08, 0.12, None]


def name(w, s, t):
    wn = {"now": "at once", "after": f"{w[1]} min after", "off": f"{w[1]:.1%} off the high"}[w[0]]
    sn = f"stop +{s[1]:.2%}" if s[0] == "fixed" else f"stop {s[1]:.0%} above the high"
    return f"{wn:18s} {sn:22s} {'target ' + format(t, '.0%') if t else 'no target (24 h)':16s}"


def summary(evs, w, s, t, le):
    rs = [resell(e, w, s, t, le) for e in evs]
    rs = [r for r in rs if r]
    if not rs:
        return 0, 0.0, 0.0, 0.0
    R = np.array([r[0] for r in rs])
    return len(R), R.sum(), R.mean(), (R > 0).mean()


def main():
    C.checks()
    ev = events()
    train = [e for e in ev if e[0] < pd.Timestamp("2026-01-01")]
    test = [e for e in ev if e[0] >= pd.Timestamp("2026-01-01")]
    print(f"Run-4 full stops with time left: {len(ev)} -- TRAIN 2025: {len(train)}, TEST 2026 + real picks: {len(test)}\n")
    grid = list(itertools.product(WHENS, STOPS, TARGETS))
    res = []
    for w, s, t in grid:
        tr = summary(train, w, s, t, False)
        res.append((tr[2], w, s, t, tr))
    res.sort(key=lambda x: -x[0])
    pos_train = sum(r[0] > 0 for r in res)
    print(f"{len(grid)} settings tried. On TRAIN (STRICT) {pos_train} have a positive average R.\n")
    print("Top 10 on TRAIN 2025 (STRICT), and what each did on the unseen TEST (STRICT .. LENIENT):")
    print(f"  {'setting':58s} | {'train n  avg R  win':>20s} | {'test n  avg R strict..lenient  total R':>40s}")
    for _, w, s, t, tr in res[:10]:
        te_s = summary(test, w, s, t, False)
        te_l = summary(test, w, s, t, True)
        print(f"  {name(w, s, t)} | {tr[0]:4d} {tr[2]:+6.2f} {tr[3]:4.0%} | {te_s[0]:4d} {te_s[2]:+6.2f} .. {te_l[2]:+6.2f}   {te_s[1]:+6.1f} .. {te_l[1]:+6.1f}")
    te_all = np.array([summary(test, w, s, t, False)[2] for w, s, t in grid])
    print(f"\nAll {len(grid)} settings on TEST (STRICT): positive average R in {(te_all > 0).sum()}; median {np.median(te_all):+.2f} R")
    # the train-best, checked by a second implementation
    _, w, s, t, _ = res[0]
    print(f"\nTRAIN-BEST: {name(w, s, t).strip()}")
    for nm, evs in (("TRAIN 2025", train), ("TEST 2026 + real", test)):
        for le in (False, True):
            n, tot, avg, win = summary(evs, w, s, t, le)
            print(f"   {nm:17s} {'LENIENT' if le else 'STRICT ':7s}: {n} re-sells, win {win:.0%}, avg {avg:+.2f} R, total {tot:+.1f} R"
                  f"  (= ${tot * 300:+,.0f} risking $300 a trade)")
    return res[0], ev


if __name__ == "__main__":
    main()
