"""Owner 2026-11-15: "how about a trailing stop loss per trade instead of a lock?" Spike Sell entry and size as built
(limit +5%, 49% at up to 20x, +25% extra margin); careful dataset (Delta 1m, 48 h). No lock. The stop TRAILS t above
the lowest low since the fill, from the first minute:
  start '2.25%'  stop = min(entry x 1.0225, lowest x (1 + t))      (the fixed stop until the trail is tighter)
  start 'pure'   stop = lowest x (1 + t), starting at entry x (1 + t)
  target         +$500 net (as built) or none (the trail is the only exit; out after 24 h)
Per minute, as the bot does it: (a) a 1m high at/over the stop set by EARLIER lows -> stop fill (STRICT: worse of the
stop and the close, +0.1%; LENIENT: the stop +0.1%); (b) target 0.1% through; (c) the low updates the trail; if the
minute CLOSES at/over the new stop the bot buys back at the close +0.1%. A fill at/over liquidation loses margin+extra.
Per pick at a fixed $3,184; 2025 vs 2026 + real. Two implementations (loop / vectorised) must agree.

    PYTHONPATH=. python scripts/careful_trailing.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402


def setup(sym, H, Cl):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    return e, k, n, ld, margin + extra


def t1(sym, H, L, Cl, le, t, start, target):
    su = setup(sym, H, Cl)
    if su is None:
        return None
    e, k, n, ld, cap = su
    pl = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    tp = e * (1 - (500 / n + 0.0012)) if target else None
    Q = e * (1 + ld)
    stop = e * (1.0225 if start == "2.25%" else 1 + t)
    low = e
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            return ("LIQ", -cap, k, j) if x >= Q else ("TRAIL", pl(x), k, j)
        if tp is not None and L[j] <= tp * 0.999:
            return "TARGET", pl(tp), k, j
        low = min(low, L[j])
        stop = min(stop, low * (1 + t))
        if Cl[j] >= stop:
            return "TRAIL", pl(Cl[j] * 1.001), k, j
    return "24h", pl(Cl[last] * 1.001), k, last


def t2(sym, H, L, Cl, le, t, start, target):
    su = setup(sym, H, Cl)
    if su is None:
        return None
    e, k, n, ld, cap = su
    last = min(2880, k + 1440)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    first = e * (1.0225 if start == "2.25%" else 1 + t)
    after = np.minimum(first, np.minimum(e, np.minimum.accumulate(l)) * (1 + t))    # the stop after each minute's low
    before = np.concatenate(([first], after[:-1]))                                   # the stop in force during the minute
    A = h >= before
    Bm = (l <= e * (1 - (500 / n + 0.0012)) * 0.999) if target else np.zeros(len(h), bool)
    Cm = c >= after
    hit = np.flatnonzero(A | Bm | Cm)
    net = lambda x: max(n * ((e - x) / e - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    if hit.size == 0:
        return "24h", net(Cl[last] * 1.001), k, last
    i = int(hit[0])
    if A[i]:
        x = before[i] * 1.001 if le else max(before[i], c[i]) * 1.001
        return ("LIQ", -cap, k, k + 1 + i) if x >= e * (1 + ld) else ("TRAIL", net(x), k, k + 1 + i)
    if Bm[i]:
        return "TARGET", net(e * (1 - (500 / n + 0.0012))), k, k + 1 + i
    return "TRAIL", net(c[i] * 1.001), k, k + 1 + i


GRID = [(t, st, tg) for tg in (True, False) for st in ("2.25%", "pure") for t in (0.005, 0.008, 0.01, 0.015, 0.0225, 0.03)
        if not (st == "pure" and t == 0.0225)]


def label(c):
    t, st, tg = c
    return f"trail {t:.2%}, start {'2.25% stop' if st == '2.25%' else 'pure trail':10s}, {'target +$500' if tg else 'no target'}"


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    res = {}
    for cfg in GRID:
        for le in (False, True):
            out = []
            for d, s, H, L, Cl in picks:
                a, b = t1(s, H, L, Cl, le, *cfg), t2(s, H, L, Cl, le, *cfg)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (cfg, d, s, a, b)
                if a:
                    out.append((d, a[0], a[1]))
            res[(cfg, le)] = pd.DataFrame(out, columns=["day", "how", "pnl"])
    print(f"CHECK PASSED: two implementations agree on every trade of all {len(GRID)} trailing designs, both fills.\n")
    print("Average $ per trade at a fixed $3,184, STRICT .. LENIENT. As built (lock + 0.8% trail after it): 2025 -94 .. -49, 2026 -94 .. -32.")
    print(f"  {'design':52s} | {'2025':>14s} | {'2026 + real':>14s} | 2026 strict: win rate, avg win, avg loss, targets")
    both = 0
    for cfg in GRID:
        s, l_ = res[(cfg, False)], res[(cfg, True)]
        a, b = s[s.day < "2026"].pnl.mean(), l_[l_.day < "2026"].pnl.mean()
        te, tl = s[s.day >= "2026"], l_[l_.day >= "2026"]
        both += (a > 0) and (te.pnl.mean() > 0)
        w, lo = te[te.pnl > 0], te[te.pnl <= 0]
        print(f"  {label(cfg):52s} | {a:+5.0f} .. {b:+5.0f} | {te.pnl.mean():+5.0f} .. {tl.pnl.mean():+5.0f} | "
              f"{len(w) / len(te):4.0%}, {w.pnl.mean():+5.0f}, {lo.pnl.mean():+6.0f}, {(te.how == 'TARGET').mean():4.0%}")
    print(f"\ndesigns profitable in BOTH 2025 and 2026 (strict): {both} of {len(GRID)}")


if __name__ == "__main__":
    main()
