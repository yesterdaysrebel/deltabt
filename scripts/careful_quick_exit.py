"""Owner 2026-11-15: "how about booking a quick profit per trade before it flips?" The price-target version is
scripts/careful_small_target.py (28 designs, none profitable). This is QUICK BY THE CLOCK: Spike Sell entry and size
as built, stop +2.25%, and the bot buys back at market N minutes after the fill (close x 1.001, taker) -- no target.
Also: buy back at the first 1m CLOSE that shows a net profit of at least $P (else the stop / 24 h).
Per pick at a fixed $3,184; careful dataset; 2025 vs 2026 + real; STRICT .. LENIENT stop fills.
Check: the time exit equals scripts/careful_wide_stop.w1 / w2 (stop 2.25%, hold N) on every non-liquidated trade.

    PYTHONPATH=. python scripts/careful_quick_exit.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_wide_stop as W  # noqa: E402


def q(sym, H, L, Cl, le, hold=None, book=None):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap = margin + extra
    raw = lambda x: n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e)  # noqa: E731
    stop, Q = e * 1.0225, e * (1 + ld)
    last = min(2880, k + (hold or 1440))
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            return ("LIQ", -cap, k, j, n) if x >= Q else ("STOP", max(raw(x), -cap), k, j, n)
        if book is not None and raw(Cl[j] * 1.001) >= book:
            return "BOOKED", raw(Cl[j] * 1.001), k, j, n
    return "TIME", max(raw(Cl[last] * 1.001), -cap), k, last, n


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    unit = W.unit(0.0225) if hasattr(W, "unit") else 0.0225 + 0.001 + 0.0012 + 0.0225 * 0.0005
    res = {}
    cfgs = [("time", N) for N in (1, 2, 3, 5, 10, 15, 30, 60)] + [("book", P) for P in (0, 25, 50, 100, 200)]
    for cfg in cfgs:
        for le in (False, True):
            out = []
            for d, s, H, L, Cl in picks:
                a = q(s, H, L, Cl, le, hold=cfg[1]) if cfg[0] == "time" else q(s, H, L, Cl, le, book=cfg[1])
                if a is None:
                    continue
                if cfg[0] == "time" and a[0] != "LIQ":
                    w1, w2 = W.w1(H, L, Cl, le, 0.0225, None, cfg[1]), W.w2(H, L, Cl, le, 0.0225, None, cfg[1])
                    assert abs(w1[1] - w2[1]) < 1e-9 and w1[3] == a[3] and abs(w1[1] * unit * a[4] - a[1]) < 0.01, (cfg, d, s, a, w1)
                out.append((d, a[0], a[1]))
            res[(cfg, le)] = pd.DataFrame(out, columns=["day", "how", "pnl"])
    print("CHECK PASSED: the time exits equal the wide-stop functions (two implementations) on every non-liquidated trade.\n")
    print("Average $ per trade at a fixed $3,184, STRICT .. LENIENT. As built: 2025 -94 .. -49, 2026 -94 .. -32.")
    print(f"  {'exit':44s} | {'2025':>14s} | {'2026 + real':>14s} | 2026 strict: in profit, stopped, avg win, avg loss")
    both = 0
    for cfg in cfgs:
        s, l_ = res[(cfg, False)], res[(cfg, True)]
        a, b = s[s.day < "2026"].pnl.mean(), l_[l_.day < "2026"].pnl.mean()
        te, tl = s[s.day >= "2026"], l_[l_.day >= "2026"]
        both += (a > 0) and (te.pnl.mean() > 0)
        w, lo = te[te.pnl > 0], te[te.pnl <= 0]
        nm = f"buy back {cfg[1]} min after the fill" if cfg[0] == "time" else f"buy back at the first close netting >= ${cfg[1]}"
        print(f"  {nm:44s} | {a:+5.0f} .. {b:+5.0f} | {te.pnl.mean():+5.0f} .. {tl.pnl.mean():+5.0f} | "
              f"{len(w) / len(te):4.0%}, {te.how.isin(['STOP', 'LIQ']).mean():4.0%}, {w.pnl.mean():+5.0f}, {lo.pnl.mean():+6.0f}")
    print(f"\nexits profitable in BOTH 2025 and 2026 (strict): {both} of {len(cfgs)}")


if __name__ == "__main__":
    main()
