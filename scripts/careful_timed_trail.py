"""Owner 2026-11-16: "I would change lock to trailing SL; since most trades flip, the trailing SL can be TIMED instead
of price or % fall."  Spike Sell exactly as agreed (docs/fade_live_spike_prereg.md: resting sell 5% above the 00:02
price, isolated margin at up to 20x plus the extra margin, exchange stop +2.25%, no target, out after 24 h, first fill
wins, month off after 2 full stops, $1,000 floor), with the break-even "lock" replaced by a clock:

  TIMED TRAIL T / X   from T minutes after the fill the stop trails X above the lowest low since the fill (it only
                      ever moves down, never looser than the 2.25% stop). T = 0 is a plain trail from the fill.
  TIMED LOCK T        at T minutes the stop goes to break-even and from then on trails 5% above the lowest low; a
                      trade that is not in profit at T is bought back at once (the bot's own LOCK-at-market rule).
  baseline            exit C as agreed: break-even once price has fallen ~0.6%, then the 5% trail.

Per minute, as the bot does it (scripts/careful_be_trail.py): (a) a 1m high at/over the stop in force -> stop fill
(STRICT worse of the stop and the close +0.1%; LENIENT the stop +0.1%; at/over liquidation -> margin + extra lost);
(b) the stop is updated; (c) a close at/over the new stop -> bought back at the close +0.1%. An exit on the original
stop is a full STOP (counts for the month-off rule). Two implementations (minute loop / stop arrays) are asserted
equal on every trade of the full history, for every variant, at both margins.

    PYTHONPATH=. python scripts/careful_timed_trail.py     # -> out/careful/timed_trail.txt
"""
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_be_trail as BT  # noqa: E402
import careful_data as D  # noqa: E402
import careful_margin as M  # noqa: E402
import careful_sim_b as B  # noqa: E402

MARGINS = (0.10, 0.20)
VARIANTS = [("trail", t, x) for t in (0, 15, 30, 60, 120, 240, 480) for x in (0.0225, 0.03, 0.05)] + [("lock", t, 0.05) for t in (15, 30, 60, 120, 240, 480)]


def make(kind, T, X):
    def f1(sym, H, L, Cl, vb, le):
        e = Cl[2] * 1.05
        k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
        if k is None:
            return None
        margin, extra = 0.49 * vb, 0.25 * vb
        n, ld = B.sizing(sym, margin, e, extra)
        cap, Q = margin + extra, e * (1 + ld)
        raw = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
        first, be = e * 1.0225, e * (1 - 0.0012)
        stop, low = first, e
        last = min(2880, k + 1440)
        for j in range(k + 1, last + 1):
            if H[j] >= stop:
                x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
                if x >= Q:
                    return "LIQ", -cap, k, j
                return ("STOP" if stop == first else "TRAIL"), raw(x), k, j
            low = min(low, L[j])
            if j - k >= T:
                stop = min(stop, low * (1 + X)) if kind == "trail" else min(stop, be, low * (1 + X))
            if Cl[j] >= stop:
                return ("STOP" if stop == first else "TRAIL"), raw(Cl[j] * 1.001), k, j
        return "24h", raw(Cl[last] * 1.001), k, last

    def f2(sym, H, L, Cl, vb, le):
        e = Cl[2] * 1.05
        f = np.flatnonzero(H[3:1440] >= e * 1.0005)
        if not f.size:
            return None
        k = 3 + int(f[0])
        margin, extra = 0.49 * vb, 0.25 * vb
        n, ld = B.sizing(sym, margin, e, extra)
        cap, Q = margin + extra, e * (1 + ld)
        first, be = e * 1.0225, e * (1 - 0.0012)
        last = min(2880, k + 1440)
        h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
        lowrun = np.minimum(np.minimum.accumulate(l), e)
        lvl = lowrun * (1 + X) if kind == "trail" else np.minimum(be, lowrun * (1 + X))
        after = np.where(np.arange(1, len(h) + 1) >= T, np.minimum(first, lvl), first)     # the stop after this minute's update
        force = np.r_[first, after[:-1]]                                                # the stop in force during the minute
        hit = np.flatnonzero((h >= force) | (c >= after))
        pnl = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
        if not hit.size:
            return "24h", pnl(Cl[last] * 1.001), k, last
        i = int(hit[0])
        if h[i] >= force[i]:
            x = force[i] * 1.001 if le else max(force[i], c[i]) * 1.001
            if x >= Q:
                return "LIQ", -cap, k, k + 1 + i
            return ("STOP" if force[i] == first else "TRAIL"), pnl(x), k, k + 1 + i
        return ("STOP" if after[i] == first else "TRAIL"), pnl(c[i] * 1.001), k, k + 1 + i

    return f1, f2


def describe(led):
    d = pd.DataFrame(led, columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
    prev = d.bal - d.pnl
    r = d.pnl / prev
    return dict(n=len(d), win=(r > 0).mean(), aw=r[r > 0].mean(), al=r[r <= 0].mean(), stops=(d.how.isin(["STOP", "LIQ"])).mean(), hold=((d.j - d.k) / 60).median())


def job(arg):
    name, mf, le = arg
    f1, f2 = (BT.t1, BT.t2) if name == "baseline" else make(*name)
    days = D.load()
    a, b = M.account(days, f1, mf, le), M.account(days, f2, mf, le)
    assert len(a) == len(b) and all(x[:5] == y[:5] and abs(x[5] - y[5]) < 0.01 for x, y in zip(a, b)), arg
    full = M.stats(a)
    y26 = M.stats(M.account([d for d in days if d[0] >= pd.Timestamp("2026-01-01")], f2, mf, le))[0]
    ends = np.array([M.stats(M.account([d for d in days if d[0] >= mo.to_timestamp()], f2, mf, le))[0] for mo in pd.period_range("2025-01", "2026-10", freq="M")])
    return arg, dict(end=full[0], dd=full[2], y26=y26, under=int((ends < 1000).sum()), above=int((ends > D.START).sum()), med=float(np.median(ends)), **describe(a))


def main():
    names = ["baseline"] + VARIANTS
    args = [(nm, mf, le) for nm in names for mf in MARGINS for le in (False, True)]
    with Pool(min(len(args), os.cpu_count() or 2)) as p:
        res = dict(p.map(job, args))
    out = ["SPIKE SELL AS AGREED, THE BREAK-EVEN LOCK REPLACED BY A CLOCK. From $3,184, full history 2025-01-15..2026-11-14. STRICT .. LENIENT.",
           "CHECK PASSED: two implementations agree on every trade of the full history for every variant at both margins.",
           "win / avg win / avg loss are per trade as % of the balance before the trade (STRICT). 'starts' = 22 monthly starts run to 2026-11-14."]
    for mf in MARGINS:
        out.append(f"\n===== margin {mf:.0%} of the balance")
        out.append(f"{'exit':24} | {'end balance':>17} | {'deepest drop':>12} | {'2026 only':>15} | {'starts<$1,000':>13} | {'starts>$3,184':>13} | {'trades':>6} {'win':>4} {'avg win':>8} {'avg loss':>8} {'full stops':>10} {'median hold':>11}")
        for nm in names:
            s, ln = res[(nm, mf, False)], res[(nm, mf, True)]
            label = "exit C as agreed" if nm == "baseline" else (f"timed trail {nm[1]:3d}m / {nm[2]:.2%}" if nm[0] == "trail" else f"timed lock {nm[1]:3d}m + 5% trail")
            out.append(f"{label:24} | {s['end']:7,.0f} .. {ln['end']:7,.0f} | {s['dd']:4.0%} .. {ln['dd']:4.0%} | {s['y26']:6,.0f} .. {ln['y26']:6,.0f} | {s['under']:4d} .. {ln['under']:2d}    | "
                       f"{s['above']:4d} .. {ln['above']:2d}    | {s['n']:6d} {100 * s['win']:3.0f}% {100 * s['aw']:+7.2f}% {100 * s['al']:+7.2f}% {100 * s['stops']:9.0f}% {s['hold']:9.1f} h")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/timed_trail.txt").write_text(txt + "\n")
    pd.to_pickle(res, "out/careful/timed_trail.pkl")


if __name__ == "__main__":
    main()
