"""Owner 2026-11-15: "huge stops is the real pain point". 86% of Spike Sell's stop-losses are back under the stop
within 30 min (out/careful/stop_pain.txt) -- wicks. Two ways to sit through them at the SAME size:
  touch s      exchange stop s above the entry, triggered by any trade (as built: s = 2.25%)
  close s/h    the bot buys back when a 1m candle CLOSES above s (at that close +0.1%); an exchange stop at h guards
               against a jump (STRICT / LENIENT fill as usual). Liquidation ~ +5.1% with the 25% extra margin.
Everything else as built (target, 1% lock, 0.8% trail, 24 h). Per fill at a fixed $3,184 on the careful dataset
(Delta 1m, 48 h); chosen on 2025, judged on 2026 + real (never seen). Two implementations (P1, P2) must agree.

    PYTHONPATH=. python scripts/careful_stop_grid.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def p1(sym, H, L, Cl, le, kind, s, h=None):
    lim = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= lim * 1.0005), None)
    if k is None:
        return None
    e = lim
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    lossmax = margin + extra
    pl = lambda x: max(n * (-(x / e - 1) - 0.0002 - 0.0005 - 0.0005 * x / e), -lossmax)  # noqa: E731
    T = e * (1 - (500 / n + 0.0012))
    K = e * (1 - (200 / n + 0.0012))
    Q = e * (1 + ld)
    hard = e * (1 + (s if kind == "touch" else h))
    soft = e * (1 + s) if kind == "close" else None
    locked, low = False, e
    stop = hard
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            if not locked and x >= Q:
                return "LIQ", -lossmax, k, j
            return ("LOCKED" if locked else "STOP"), pl(x), k, j
        if not locked and soft is not None and Cl[j] >= soft:
            return "STOP", pl(Cl[j] * 1.001), k, j
        if L[j] <= T * 0.999:
            return "TARGET", n * (-(T / e - 1) - 0.0002 - 0.0005 - 0.0005 * T / e), k, j
        low = min(low, L[j])
        if not locked and L[j] <= 0.99 * e:
            locked, stop = True, min(stop, K)
            if Cl[j] >= stop:
                return "LOCKED", pl(Cl[j] * 1.001), k, j
        if locked:
            stop = min(stop, 1.008 * low)
            if Cl[j] >= stop:
                return "LOCKED", pl(Cl[j] * 1.001), k, j
    return "24h", pl(Cl[last] * 1.001), k, last


class P2Pos(Cc.Position):
    """Implementation C's position (closed-form sizing, its own tick) with the stop changed."""
    def __init__(self, sym, e, k, kind, s, h, le):
        super().__init__(sym, e, k, D.START, "sell", le, 0.0002, 0.0005, None)
        self.stop = e * (1 + (s if kind == "touch" else h))
        self.soft = e * (1 + s) if kind == "close" else None

    def tick(self, k, h, l, c):
        if not self.locked and self.soft is not None and h < self.stop and c >= self.soft:
            return "STOP", self.pnl(c * 1.001)
        return super().tick(k, h, l, c)


def p2(sym, H, L, Cl, le, kind, s, h=None):
    lim = Cl[2] * 1.05
    hits = np.nonzero(H[3:1440] >= lim * 1.0005)[0]
    if not len(hits):
        return None
    k = 3 + int(hits[0])
    pos = P2Pos(sym, lim, k, kind, s, h, le)
    pos.end = min(2880, k + 1440)
    for j in range(k + 1, 2881):
        res = pos.tick(j, H[j], L[j], Cl[j])
        if res:
            return res[0], res[1], k, j


GRID = [("touch", s, None) for s in (0.015, 0.0225, 0.03, 0.035, 0.04, 0.045)] + \
       [("close", s, h) for s in (0.0225, 0.03, 0.035) for h in (0.035, 0.045) if h > s]


def label(g):
    return f"touch stop +{g[1]:.2%}" if g[0] == "touch" else f"close > +{g[1]:.2%}, hard stop +{g[2]:.1%}"


def main():
    days = D.load()
    fills = [(d, s, H, L, Cl) for d, ps in days for r, s, H, L, Cl in ps]
    for le in (False, True):                             # checks: P1 == P2 everywhere; as built == implementation B
        for d, s, H, L, Cl in fills:
            b = B.first_trade(s, H, L, Cl, D.START, le)
            a = p1(s, H, L, Cl, le, "touch", 0.0225)
            assert (a is None) == (b is None) and (a is None or (a[0] == b["how"].replace("TIME", "24h") and abs(a[1] - b["pnl"]) < 0.01)), (d, s)
            for g in GRID:
                x, y = p1(s, H, L, Cl, le, *g), p2(s, H, L, Cl, le, *g)
                assert (x is None) == (y is None) and (x is None or (x[0] == y[0] and x[3] == y[3] and abs(x[1] - y[1]) < 0.01)), (d, s, g, x, y)
    print(f"CHECKS PASSED: P1 == P2 on every fill for all {len(GRID)} stop settings, both fills; as built == implementation B.\n")
    print("Per fill at a fixed $3,184, STRICT .. LENIENT. TRAIN = 2025, TEST = 2026 + real picks (never seen).")
    print(f"  {'stop':38s} | {'TRAIN avg/trade':>17s} | {'TEST avg/trade':>17s} | {'TEST total':>19s} | TEST stops, targets, liq (strict)")
    rows = []
    for g in GRID:
        out = {}
        for le in (False, True):
            r = [(d, p1(s, H, L, Cl, le, *g)) for d, s, H, L, Cl in fills]
            r = [(d, x) for d, x in r if x]
            tr = [x[1] for d, x in r if d < pd.Timestamp("2026-01-01")]
            te = [x for d, x in r if d >= pd.Timestamp("2026-01-01")]
            out[le] = (np.mean(tr), np.mean([x[1] for x in te]), sum(x[1] for x in te),
                       sum(x[0] == "STOP" for x in te), sum(x[0] == "TARGET" for x in te), sum(x[0] == "LIQ" for x in te), len(te))
        rows.append((g, out))
        s_, l_ = out[False], out[True]
        mark = "  <- as built" if g == ("touch", 0.0225, None) else ""
        print(f"  {label(g):38s} | {s_[0]:+7.0f} .. {l_[0]:+6.0f} | {s_[1]:+7.0f} .. {l_[1]:+6.0f} | {s_[2]:+8,.0f} .. {l_[2]:+8,.0f} | "
              f"{s_[3]:3d} stops, {s_[4]:3d} targets, {s_[5]} liq of {s_[6]}{mark}")
    best = max(rows, key=lambda r: r[1][False][0])
    print(f"\nBEST ON TRAIN (strict): {label(best[0])} -> TEST {best[1][False][1]:+.0f} .. {best[1][True][1]:+.0f} per trade "
          f"vs as built {rows[1][1][False][1]:+.0f} .. {rows[1][1][True][1]:+.0f}")


if __name__ == "__main__":
    main()
