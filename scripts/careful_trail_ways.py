"""Owner 2026-11-15: "What is best way to trail?" Trailing families on the same Spike Sell fills (limit +5%, 49% at up
to 20x, +25% extra margin), all starting from the 2.25% stop, no fixed target, out after 24 h. The stop only moves
DOWN. Per minute, as the bot does it: (a) a 1m high at/over the stop in force -> stop fill (STRICT worse of the stop
and the close +0.1%, LENIENT the stop +0.1%; at/over liquidation -> margin + extra lost); (b) the stop is updated from
this minute's data; (c) if the minute closes at/over the new stop the bot buys back at the close +0.1%.
  pct t            from the fill: stop = lowest low x (1 + t)
  act A / t        once a 1m low reaches the price netting $A: stop -> break-even (nets $0), then lowest low x (1 + t)
  high M           once a low nets $50: break-even, then just above the highest high of the last M minutes (x 1.001)
  vol k            once a low nets $50: break-even, then lowest low + k x the average 1m range of the last 30 minutes
  step s           each time the best point nets another $s, the stop locks the previous step (first step: break-even)
Per pick at a fixed $3,184; 2025 vs 2026 + real. Two implementations (minute loop / precomputed stop arrays).

    PYTHONPATH=. python scripts/careful_trail_ways.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def px_net(e, n, usd):
    return e * (1 - (usd / n + 0.0012))


# ------------------------------------------------------------------ implementation 1: minute loop
def w1(sym, H, L, Cl, le, fam, p):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap, Q = margin + extra, e * (1 + ld)
    raw = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    stop, low, active = e * 1.0225, e, fam == "pct"
    act_px = px_net(e, n, p[0] if fam == "act" else 50.0)
    be = px_net(e, n, 0.0)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            return ("LIQ", -cap, k, j) if x >= Q else ("STOP", raw(x), k, j)
        low = min(low, L[j])
        if fam == "pct":
            stop = min(stop, low * (1 + p[0]))
        elif fam == "step":
            best = n * (-(low / e - 1) - 0.0007 - 0.0005)          # net at the best point (fees as in px_net)
            s = int(best // p[0])
            if s >= 1:
                stop = min(stop, px_net(e, n, (s - 1) * p[0]))
        else:
            if not active and L[j] <= act_px:
                active = True
            if active:
                stop = min(stop, be)
                if fam == "act":
                    stop = min(stop, low * (1 + p[1]))
                elif fam == "high":
                    hh = H[max(0, j - p[0] + 1):j + 1].max()
                    if np.isfinite(hh):
                        stop = min(stop, hh * 1.001)
                elif fam == "vol" and j >= 29:              # needs 30 minutes of data
                    a, b = H[j - 29:j + 1], L[j - 29:j + 1]
                    ok = np.isfinite(a) & np.isfinite(b)
                    atr = float(np.where(ok, a - b, 0.0).mean())
                    stop = min(stop, low + p[0] * atr)
        if Cl[j] >= stop:
            return "TRAIL", raw(Cl[j] * 1.001), k, j
    return "24h", raw(Cl[last] * 1.001), k, last


# ------------------------------------------------------------------ implementation 2: precomputed stop arrays
def w2(sym, H, L, Cl, le, fam, p):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    m_, x_ = D.START * 0.49, D.START * 0.25
    n, liq = Cc.notional_for(sym, m_, e)
    Q = e * (1 + liq + x_ / n)
    last = min(2880, k + 1440)
    idx = np.arange(k + 1, last + 1)
    h, l, c = H[idx], L[idx], Cl[idx]
    lowrun = np.minimum(e, np.minimum.accumulate(l))
    first = e * 1.0225
    cand = np.full(len(idx), np.inf)
    be = e * (1 - 0.0012)
    if fam == "pct":
        cand = lowrun * (1 + p[0])
    elif fam == "step":
        best = n * ((e - lowrun) / e - 0.0012)
        s = np.floor(best / p[0])
        cand = np.where(s >= 1, e * (1 - ((s - 1) * p[0] / n + 0.0012)), np.inf)
    else:
        A = p[0] if fam == "act" else 50.0
        hit = np.flatnonzero(l <= e * (1 - (A / n + 0.0012)))
        if hit.size:
            on = np.arange(len(idx)) >= hit[0]
            if fam == "act":
                extra = lowrun * (1 + p[1])
            elif fam == "high":
                hs = pd.Series(H[:last + 1]).rolling(p[0], min_periods=1).max().to_numpy()[idx]
                extra = np.where(np.isfinite(hs), hs * 1.001, np.inf)
            else:
                ok = np.isfinite(H[:last + 1]) & np.isfinite(L[:last + 1])
                rng = np.where(ok, H[:last + 1] - L[:last + 1], 0.0)
                atr = pd.Series(rng).rolling(30, min_periods=30).mean().to_numpy()[idx]
                extra = np.where(np.isfinite(atr), lowrun + p[0] * atr, np.inf)
            cand = np.where(on, np.minimum(be, extra), np.inf)
    after = np.minimum(first, np.minimum.accumulate(cand))
    before = np.concatenate(([first], after[:-1]))
    net = lambda x: max(n * ((e - x) / e - 0.0007 - 0.0005 * x / e), -(m_ + x_))  # noqa: E731
    A_, C_ = h >= before, c >= after
    hit = np.flatnonzero(A_ | C_)
    if hit.size == 0:
        return "24h", net(Cl[last] * 1.001), k, last
    i = int(hit[0])
    if A_[i]:
        x = before[i] * 1.001 if le else max(before[i], c[i]) * 1.001
        return ("LIQ", -(m_ + x_), k, k + 1 + i) if x >= Q else ("STOP", net(x), k, k + 1 + i)
    return "TRAIL", net(c[i] * 1.001), k, k + 1 + i


CFG = [(f"pct trail {t:.1%} from the fill", "pct", (t,)) for t in (0.005, 0.01, 0.015, 0.03)] + \
      [(f"after +${A}: break-even, then trail {t:.0%}", "act", (A, t)) for A in (50, 150, 300) for t in (0.01, 0.02, 0.03, 0.05)] + \
      [(f"after +$50: break-even, then last {M}-min high", "high", (M,)) for M in (5, 15, 30, 60, 120)] + \
      [(f"after +$50: break-even, then {kk} x 1m range", "vol", (kk,)) for kk in (5, 10, 20)] + \
      [(f"step ratchet ${s}", "step", (s,)) for s in (150, 250, 500)]


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    res = {}
    for name, fam, p in CFG:
        for le in (False, True):
            out = []
            for d, s, H, L, Cl in picks:
                a, b = w1(s, H, L, Cl, le, fam, p), w2(s, H, L, Cl, le, fam, p)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (name, d, s, a, b)
                if a:
                    out.append((d, a[0], a[1]))
            res[(name, le)] = pd.DataFrame(out, columns=["day", "how", "pnl"])
    print(f"CHECK PASSED: two implementations agree on every trade of all {len(CFG)} trailing methods, both fills.\n")
    print("Average $ per trade at a fixed $3,184 (617 fills), STRICT .. LENIENT.")
    print("Baselines: Spike Sell as built -94 .. -49 / -94 .. -32; quick book (first close >= $50) -62 .. -41 / -46 .. -22.")
    print(f"  {'trailing method':46s} | {'2025':>14s} | {'2026 + real':>14s} | all strict: win rate, avg win, avg loss, over +$1,000")
    rows = []
    for name, fam, p in CFG:
        s, l_ = res[(name, False)], res[(name, True)]
        a, b = s[s.day < "2026"].pnl.mean(), l_[l_.day < "2026"].pnl.mean()
        c, d2 = s[s.day >= "2026"].pnl.mean(), l_[l_.day >= "2026"].pnl.mean()
        w, lo = s[s.pnl > 0], s[s.pnl <= 0]
        rows.append((name, a, b, c, d2))
        print(f"  {name:46s} | {a:+5.0f} .. {b:+5.0f} | {c:+5.0f} .. {d2:+5.0f} | {len(w) / len(s):4.0%}, {w.pnl.mean():+6.0f}, {lo.pnl.mean():+6.0f}, {(s.pnl >= 1000).mean():4.0%}")
    best = max(rows, key=lambda r: r[1])
    print(f"\nprofitable in BOTH years (strict): {sum(r[1] > 0 and r[3] > 0 for r in rows)} of {len(rows)}; lenient: {sum(r[2] > 0 and r[4] > 0 for r in rows)}")
    print(f"BEST ON 2025 (strict): {best[0]}: 2025 {best[1]:+.0f} .. {best[2]:+.0f} -> 2026 {best[3]:+.0f} .. {best[4]:+.0f}")
    allavg = sorted(rows, key=lambda r: -(r[1] + r[3]))[:3]
    print("least bad over both years (strict): " + "; ".join(f"{r[0]} ({r[1]:+.0f} / {r[3]:+.0f})" for r in allavg))


if __name__ == "__main__":
    main()
