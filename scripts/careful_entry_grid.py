"""Owner 2026-11-15: "find a way to solve the stop-loss problem". ENTRY designs (the untested side): the stops are
coins that keep running after we sell INTO the spike. Same size (49% at up to 20x, +25% extra), same exits (target
+$500 net, 1% lock, 0.8% trail, 24 h) -- only the entry (and, for 'high', where the stop sits) changes:
  limit L      resting sell L above the 00:02 price (as built: L = 5%); stop +2.25%
  rev L/r/fixed  once a 1m high is >= L above 00:02: sell at market at the first 1m CLOSE that is r below the highest
               high since then (close x 0.999, taker), before 24:00; stop +2.25% above the sell
  rev L/r/high   same entry; stop 0.25% above that highest high (skipped if that is more than 4% above the sell)
Per pick at a fixed $3,184 on the careful dataset; TRAIN 2025, TEST 2026 + real. STRICT .. LENIENT stop fills.
Two implementations: G1 (here) vs implementation B (limit) / implementation C's Position (rev) -- must agree.

    PYTHONPATH=. python scripts/careful_entry_grid.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def manage(sym, H, L, Cl, k, e, stop, in_cost, le):
    """The as-built exits from a short at e opened in minute k. -> (how, pnl, k, j)"""
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap = margin + extra
    c = in_cost + 0.0005
    pl = lambda x: max(n * (-(x / e - 1) - in_cost - 0.0005 * x / e), -cap)  # noqa: E731
    T, K, Q = e * (1 - (500 / n + c)), e * (1 - (200 / n + c)), e * (1 + ld)
    locked, low = False, e
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            if not locked and x >= Q:
                return "LIQ", -cap, k, j
            return ("LOCKED" if locked else "STOP"), pl(x), k, j
        if L[j] <= T * 0.999:
            return "TARGET", pl(T), k, j
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


def g1(sym, H, L, Cl, le, cfg):
    ref = Cl[2]
    if cfg[0] == "limit":
        e = ref * (1 + cfg[1])
        k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
        return None if k is None else manage(sym, H, L, Cl, k, e, e * 1.0225, 0.0007, le)
    _, lv, r, sm = cfg
    k0 = next((j for j in range(3, 1440) if H[j] >= ref * (1 + lv)), None)
    if k0 is None:
        return None
    top = -np.inf
    for j in range(k0, 1440):
        top = max(top, H[j])
        if Cl[j] <= top * (1 - r):
            e = Cl[j] * 0.999
            stop = e * 1.0225 if sm == "fixed" else top * 1.0025
            if stop / e - 1 > 0.04:
                return None
            return manage(sym, H, L, Cl, j, e, stop, 0.0005, le)
    return None


def g2(sym, H, L, Cl, le, cfg):
    """Independent: limit via implementation B (its own loop; the 00:02 price rescaled so its 5% limit is ours);
    rev via implementation C's Position with vectorised entry detection."""
    if cfg[0] == "limit":
        c2 = Cl.copy()
        c2[2] = Cl[2] * (1 + cfg[1]) / 1.05
        t = B.first_trade(sym, H, L, c2, D.START, le)
        return None if t is None else (t["how"].replace("TIME", "24h"), t["pnl"], t["k"], t["j"])
    _, lv, r, sm = cfg
    hit = np.nonzero(H[3:1440] >= Cl[2] * (1 + lv))[0]
    if not len(hit):
        return None
    k0 = 3 + int(hit[0])
    runmax = np.maximum.accumulate(H[k0:1440])
    trig = np.nonzero(Cl[k0:1440] <= runmax * (1 - r))[0]
    if not len(trig):
        return None
    j = k0 + int(trig[0])
    e = Cl[j] * 0.999
    stop = e * 1.0225 if sm == "fixed" else runmax[trig[0]] * 1.0025
    if stop / e - 1 > 0.04:
        return None
    pos = Cc.Position(sym, e, j, D.START, "sell", le, 0.0005, 0.0, min(2880, j + 1440))
    pos.stop = stop
    for m in range(j + 1, 2881):
        res = pos.tick(m, H[m], L[m], Cl[m])
        if res:
            return res[0], res[1], j, m


GRID = [("limit", x) for x in (0.03, 0.05, 0.075, 0.10, 0.15)] + \
       [("rev", lv, r, sm) for lv in (0.05, 0.075, 0.10) for r in (0.01, 0.02, 0.03) for sm in ("fixed", "high")]


def label(c):
    return f"limit +{c[1]:.1%}" if c[0] == "limit" else f"rev +{c[1]:.1%}, {c[2]:.0%} off the high, stop {'+2.25%' if c[3] == 'fixed' else 'above the high'}"


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    res = {}
    for cfg in GRID:
        for le in (False, True):
            out = []
            for d, s, H, L, Cl in picks:
                a, b = g1(s, H, L, Cl, le, cfg), g2(s, H, L, Cl, le, cfg)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (cfg, d, s, a, b)
                if a:
                    out.append((d, a[0], a[1]))
            res[(cfg, le)] = pd.DataFrame(out, columns=["day", "how", "pnl"])
    print(f"CHECK PASSED: two implementations agree on every trade of all {len(GRID)} entry designs, both fills.\n")
    print("Per pick at a fixed $3,184, STRICT .. LENIENT. TRAIN = 2025, TEST = 2026 + real picks.")
    print(f"  {'entry':58s} | {'TRAIN n  avg/trade':>24s} | {'TEST n   avg/trade':>24s} | {'TEST total':>19s} | TEST stops (strict)")
    rows = []
    for cfg in GRID:
        s, l_ = res[(cfg, False)], res[(cfg, True)]
        trs, tes = s[s.day < "2026"], s[s.day >= "2026"]
        trl, tel = l_[l_.day < "2026"], l_[l_.day >= "2026"]
        st = tes.how.isin(["STOP", "LIQ"]).mean() if len(tes) else 0
        rows.append((cfg, trs.pnl.mean(), tes.pnl.mean(), tel.pnl.mean()))
        print(f"  {label(cfg):58s} | {len(trs):4d} {trs.pnl.mean():+7.0f} .. {trl.pnl.mean():+6.0f} | {len(tes):4d} {tes.pnl.mean():+7.0f} .. {tel.pnl.mean():+6.0f} | "
              f"{tes.pnl.sum():+8,.0f} .. {tel.pnl.sum():+8,.0f} | {st:4.0%}" + ("  <- as built" if cfg == ("limit", 0.05) else ""))
    best = max(rows, key=lambda r: r[1])
    print(f"\nBEST ON TRAIN (strict): {label(best[0])}: TRAIN {best[1]:+.0f} -> TEST {best[2]:+.0f} .. {best[3]:+.0f} per trade")
    pos = [r for r in rows if r[1] > 0]
    print(f"designs with a positive TRAIN average (strict): {len(pos)}; of those positive on TEST (strict): {sum(r[2] > 0 for r in pos)}")


if __name__ == "__main__":
    main()
