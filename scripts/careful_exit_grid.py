"""Owner 2026-11-15: "find a way to solve the stop-loss problem" -- the payoff side: if a 2.25% stop on a coin that
swings 10% a day is mostly noise, make each stop SMALL and each win BIG. Entry as built (limit +5%), same size.
  stop s     1% / 1.5% / 2.25% / 3% above the entry (touch, STRICT / LENIENT fill)
  target     +$500 net (as built, ~1.8%) | a fall of 3 / 5 / 8 / 12% | none (out after 24 h)
  manage     'lock' = as built (after a 1% fall the stop locks +$200, then trails 0.8% above the low) | 'none'
Per pick at a fixed $3,184; TRAIN 2025, TEST 2026 + real. Two implementations (X1 here, X2 on implementation C's
Position) must agree on every trade.

    PYTHONPATH=. python scripts/careful_exit_grid.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def x1(sym, H, L, Cl, le, s, t, mode):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap = margin + extra
    pl = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    T = e * (1 - (500 / n + 0.0012)) if t == "usd" else (None if t is None else e * (1 - t))
    K, Q = e * (1 - (200 / n + 0.0012)), e * (1 + ld)
    stop, locked, low = e * (1 + s), False, e
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            if not locked and x >= Q:
                return "LIQ", -cap, k, j
            return ("LOCKED" if locked else "STOP"), pl(x), k, j
        if T is not None and L[j] <= T * 0.999:
            return "TARGET", pl(T), k, j
        if mode == "lock":
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


def x2(sym, H, L, Cl, le, s, t, mode):
    e = Cl[2] * 1.05
    hit = np.nonzero(H[3:1440] >= e * 1.0005)[0]
    if not len(hit):
        return None
    k = 3 + int(hit[0])
    pos = Cc.Position(sym, e, k, D.START, "sell" if mode == "lock" else "resell", le, 0.0002, 0.0005, min(2880, k + 1440))
    pos.stop = e * (1 + s)
    if t != "usd":
        pos.target = None if t is None else e * (1 - t)
    elif mode != "lock":
        pos.target = e * (1 - (500 / pos.n + 0.0012))
    for m in range(k + 1, 2881):
        res = pos.tick(m, H[m], L[m], Cl[m])
        if res:
            return res[0].replace("TIME", "24h"), res[1], k, m


GRID = [(s, t, m) for m in ("lock", "none") for s in (0.01, 0.015, 0.0225, 0.03) for t in ("usd", 0.03, 0.05, 0.08, 0.12, None)]


def label(c):
    s, t, m = c
    tt = "+$500" if t == "usd" else ("none, 24 h" if t is None else f"{t:.0%} fall")
    return f"stop +{s:.2%}, target {tt:10s}, {'lock+trail' if m == 'lock' else 'no lock'}"


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    res = {}
    for cfg in GRID:
        for le in (False, True):
            out = []
            for d, sym, H, L, Cl in picks:
                a, b = x1(sym, H, L, Cl, le, *cfg), x2(sym, H, L, Cl, le, *cfg)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (cfg, d, sym, a, b)
                if a:
                    out.append((d, a[0], a[1]))
            res[(cfg, le)] = pd.DataFrame(out, columns=["day", "how", "pnl"])
    print(f"CHECK PASSED: two implementations agree on every trade of all {len(GRID)} exit designs, both fills.\n")
    print("Per pick at a fixed $3,184, avg $ per trade STRICT .. LENIENT. TRAIN = 2025 (321 fills), TEST = 2026 + real (296).")
    print(f"  {'exit':56s} | {'TRAIN':>15s} | {'TEST':>15s} | TEST: stops, win rate, avg win, avg stop (strict)")
    rows = []
    for cfg in GRID:
        s, l_ = res[(cfg, False)], res[(cfg, True)]
        trs, tes, trl, tel = s[s.day < "2026"], s[s.day >= "2026"], l_[l_.day < "2026"], l_[l_.day >= "2026"]
        st = tes[tes.how.isin(["STOP", "LIQ"])]
        w = tes[tes.pnl > 0]
        rows.append((cfg, trs.pnl.mean(), trl.pnl.mean(), tes.pnl.mean(), tel.pnl.mean()))
        print(f"  {label(cfg):56s} | {trs.pnl.mean():+6.0f} .. {trl.pnl.mean():+5.0f} | {tes.pnl.mean():+6.0f} .. {tel.pnl.mean():+5.0f} | "
              f"{len(st) / len(tes):4.0%} stops, {len(w) / len(tes):4.0%} win, {w.pnl.mean():+6.0f}, {st.pnl.mean():+7.0f}"
              + ("  <- as built" if cfg == (0.0225, "usd", "lock") else ""))
    good = [r for r in rows if r[1] > 0]
    print(f"\ndesigns positive on TRAIN strict: {len(good)} of {len(rows)}; of those positive on TEST strict: {sum(r[3] > 0 for r in good)}, "
          f"TEST lenient: {sum(r[4] > 0 for r in good)}")
    best = max(rows, key=lambda r: r[1])
    print(f"BEST ON TRAIN (strict): {label(best[0])}: TRAIN {best[1]:+.0f} .. {best[2]:+.0f} -> TEST {best[3]:+.0f} .. {best[4]:+.0f}")
    bt = max(rows, key=lambda r: r[3])
    print(f"best on TEST (hindsight, do not trust): {label(bt[0])}: TRAIN {bt[1]:+.0f} .. {bt[2]:+.0f}, TEST {bt[3]:+.0f} .. {bt[4]:+.0f}")


if __name__ == "__main__":
    main()
