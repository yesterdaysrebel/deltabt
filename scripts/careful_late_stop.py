"""Owner 2026-11-15: "I have seen people having no SL first and when things go south they apply SL or cut the trade."
Mechanical versions, all with the QUICK BOOK exit (first 1m close netting >= $50), Spike Sell entry and size as built
(limit +5%, 49% at up to 20x, +25% extra margin -> liquidation ~ +5.1%, always on, loses margin + extra):
  grace N / S    no stop order for the first N minutes; then an exchange stop S above the entry (touch; STRICT = worse
                 of the stop and the close +0.1%, LENIENT = the stop +0.1%); if the close of minute N is already at /
                 over S the bot cuts there (close +0.1%)
  cut-close X    no stop order; the bot buys back when a 1m candle CLOSES X or more above the entry (close +0.1%)
  underwater U/S no stop (or stop S); from U minutes after the fill, the bot cuts at the first 1m close above the entry
Per minute: liquidation (1m high) -> active stop -> quick book -> the cut rules -> 24 h.
Per pick at a fixed $3,184; careful dataset; 2025 vs 2026 + real. Two implementations (function / state machine).

    PYTHONPATH=. python scripts/careful_late_stop.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_quick_exit as QE  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def f1(sym, H, L, Cl, le, N=0, S=None, X=None, U=None):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap, Q = margin + extra, e * (1 + ld)
    raw = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    stop = None if S is None else e * (1 + S)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        age = j - k
        if H[j] >= Q:
            return "LIQ", -cap, k, j
        if stop is not None and age > N and H[j] >= stop:
            return "STOP", raw(stop * 1.001 if le else max(stop, Cl[j]) * 1.001), k, j
        out = raw(Cl[j] * 1.001)
        if out >= 50:
            return "BOOKED", out, k, j
        if stop is not None and N > 0 and age == N and Cl[j] >= stop:
            return "CUT", out, k, j
        if X is not None and Cl[j] >= e * (1 + X):
            return "CUT", out, k, j
        if U is not None and age >= U and Cl[j] > e:
            return "CUT", out, k, j
    return "24h", raw(Cl[last] * 1.001), k, last


class Trade:
    """Second implementation: explicit state, closed-form sizing."""
    def __init__(self, sym, entry, k, le, N, S, X, U):
        self.e, self.k, self.le, self.N, self.X, self.U = entry, k, le, N, X, U
        self.m, self.x = 0.49 * D.START, 0.25 * D.START
        self.n, liq = Cc.notional_for(sym, self.m, entry)
        self.liq = entry * (1 + liq + self.x / self.n)
        self.stop = None if S is None else entry * (1 + S)

    def worth(self, px):
        return max(self.n * ((self.e - px) / self.e - 0.0002 - 0.0005 - 0.0005 * px / self.e), -(self.m + self.x))

    def on_minute(self, j, hi, cl):
        age = j - self.k
        if hi >= self.liq:
            return "LIQ", -(self.m + self.x)
        stop_live = self.stop is not None and age > self.N
        if stop_live and hi >= self.stop:
            return "STOP", self.worth((self.stop if self.le else max(self.stop, cl)) * 1.001)
        now = self.worth(cl * 1.001)
        if now >= 50:
            return "BOOKED", now
        grace_ends_bad = self.stop is not None and self.N > 0 and age == self.N and cl >= self.stop
        bad_close = self.X is not None and cl >= self.e * (1 + self.X)
        stale = self.U is not None and age >= self.U and cl > self.e
        if grace_ends_bad or bad_close or stale:
            return "CUT", now
        return None


def f2(sym, H, L, Cl, le, N=0, S=None, X=None, U=None):
    e = Cl[2] * 1.05
    hit = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if hit.size == 0:
        return None
    k = 3 + int(hit[0])
    t = Trade(sym, e, k, le, N, S, X, U)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        r = t.on_minute(j, H[j], Cl[j])
        if r:
            return r[0], r[1], k, j
    return "24h", t.worth(Cl[last] * 1.001), k, last


CFG = [("stop 2.25% from the start (quick book as tested)", dict(N=0, S=0.0225))] + \
      [(f"no stop for {N:2d} min, then stop {S:.2%}", dict(N=N, S=S)) for S in (0.0225, 0.035) for N in (2, 5, 10, 30)] + \
      [(f"no stop; cut on a close {X:.2%} against", dict(X=X)) for X in (0.01, 0.015, 0.0225, 0.03)] + \
      [(f"no stop; cut if losing after {U:2d} min", dict(U=U)) for U in (5, 10, 30, 60)] + \
      [(f"stop 2.25%; cut if losing after {U:2d} min", dict(N=0, S=0.0225, U=U)) for U in (5, 10, 30, 60)] + \
      [("no stop at all (liquidation only)", dict())]


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    res = {}
    for name, kw in CFG:
        for le in (False, True):
            out = []
            for d, s, H, L, Cl in picks:
                a, b = f1(s, H, L, Cl, le, **kw), f2(s, H, L, Cl, le, **kw)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (name, d, s, a, b)
                if a:
                    out.append((d, a[0], a[1]))
            res[(name, le)] = pd.DataFrame(out, columns=["day", "how", "pnl"])
    base = res[(CFG[0][0], False)]
    for d, s, H, L, Cl in picks[:400]:                    # the baseline equals the quick-book function tested before
        a, b = f1(s, H, L, Cl, False, N=0, S=0.0225), QE.q(s, H, L, Cl, False, book=50)
        assert (a is None) == (b is None) and (a is None or abs(a[1] - b[1]) < 0.01 or b[0] == "LIQ")
    print(f"CHECK PASSED: two implementations agree on every trade of all {len(CFG)} versions, both fills.\n")
    print("Average $ per trade at a fixed $3,184 (617 fills), STRICT .. LENIENT.")
    print(f"  {'version':50s} | {'2025':>14s} | {'2026 + real':>14s} | all strict: booked, stopped/cut, liquidated | avg loss on a stop/cut/liq")
    both = 0
    for name, kw in CFG:
        s, l_ = res[(name, False)], res[(name, True)]
        a, b = s[s.day < "2026"].pnl.mean(), l_[l_.day < "2026"].pnl.mean()
        c, d2 = s[s.day >= "2026"].pnl.mean(), l_[l_.day >= "2026"].pnl.mean()
        both += (a > 0) and (c > 0)
        bad = s[s.how.isin(["STOP", "CUT", "LIQ"])]
        print(f"  {name:50s} | {a:+5.0f} .. {b:+5.0f} | {c:+5.0f} .. {d2:+5.0f} | {(s.how == 'BOOKED').mean():4.0%}, {s.how.isin(['STOP', 'CUT']).mean():4.0%}, "
              f"{(s.how == 'LIQ').mean():4.0%}                    | ${bad.pnl.mean():+,.0f}")
    print(f"\nversions profitable in BOTH 2025 and 2026 (strict): {both} of {len(CFG)}")


if __name__ == "__main__":
    main()
