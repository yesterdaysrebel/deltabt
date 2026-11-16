"""Owner 2026-11-15: "coins hit SL and that spike will settle right? why not catch that falling spike?" -- after a
full stop (STOP/LIQ), track the high since the stop; when a 1m low falls Y% below that high, sell at market (STRICT:
the worse of the trigger and that minute's close, -0.1%; LENIENT: the trigger -0.1%; taker 0.05%), only before 24:00
UTC of the pick day, one such re-entry per pick; then the same run-4 rules (scripts/run4_resell.short_from, checked
there against the tested and the independent trade functions). Its stop counts toward the 2-stops monthly hard stop.
First: what do stopped coins actually do after the stop?

    PYTHONPATH=. python scripts/run4_catch_fall.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_margin75 as M  # noqa: E402
import run4_resell as RS  # noqa: E402

Y = None


def fall_entry(hi, lo, cl, ks, y, lenient):
    """First minute >= ks (before 24:00) whose low is y below the high since ks. -> (minute, sell price) or None."""
    top = -np.inf
    for k in range(ks, min(len(cl), 1440)):
        if lo[k] <= top * (1 - y):                       # the high of EARLIER minutes (this minute's order unknown)
            trig = top * (1 - y)
            return k, (trig if lenient else min(trig, cl[k])) * 0.999
        top = max(top, hi[k])
    return None


def legs(p, bal, mode, lenient, stops_left, size_bal=None):
    day, sym, hi, lo, cl, k0 = p

    def go(ks, limit, maker, b):
        k = b / C.START
        return RS.short_from(sym, hi, lo, cl, ks, limit, maker, 0.49 * b, 500 * k, 200 * k, 0.25 * b, lenient)

    r = go(k0, cl[k0 - 1] * 1.05, True, bal)
    if r is None:
        return []
    out = [r]
    if Y is None or r[0] not in ("STOP", "LIQ") or stops_left <= 1 or r[3] >= 1439:
        return out
    b2 = size_bal if size_bal is not None else max(bal + r[1], 0.0)
    if b2 < 50:
        return out
    f = fall_entry(hi, lo, cl, r[3] + 1, Y, lenient)
    if f:
        r2 = go(f[0] + 1, f[1], False, b2)
        if r2:
            out.append(r2)
    return out


def main():
    global Y
    C.checks()
    M.load_ranks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    allp = hist + real
    RS.legs = legs                                       # the account in run4_resell now uses these legs
    # 1. what stopped coins do after the stop (strict, fixed $3,184)
    rows = []
    for p in allp:
        Y = None
        ls = legs(p, C.START, None, False, 2)
        if ls and ls[0][0] in ("STOP", "LIQ") and ls[0][3] < 1439:
            _, s, hi, lo, cl, k0 = p
            ks, xp = ls[0][3], ls[0][4]
            after_hi = np.max(hi[ks:1440])
            top_k = ks + int(np.argmax(hi[ks:1440]))
            after_lo = np.min(lo[top_k:1440])
            rows.append((after_hi / xp - 1, 1 - after_lo / after_hi, cl[1439] / xp - 1, 1 - after_lo / xp))
    a = np.array(rows)
    print(f"Full stops with time left in the day: {len(a)}")
    print(f"  after the stop the coin went on up a further: median {np.median(a[:, 0]):.1%}, mean {a[:, 0].mean():.1%}, "
          f"over 10% in {(a[:, 0] > .10).mean():.0%}")
    print(f"  from that top it fell back (low after the top, before 24:00): median {np.median(a[:, 1]):.1%}; "
          f"at least 5% in {(a[:, 1] >= .05).mean():.0%}, at least 10% in {(a[:, 1] >= .10).mean():.0%}")
    print(f"  at 24:00 vs the stop price: below it in {(a[:, 2] < 0).mean():.0%} (median {np.median(a[:, 2]):+.1%})")
    print("  -> it often DOES settle -- the question is whether a sell AFTER the top catches enough of it.\n")
    mg = C.START
    print("Per trade on a fixed $3,184, STRICT .. LENIENT (re-entry sized on $3,184 too):")
    for nm, ps in (("20 real picks", real), ("full history + real", allp)):
        print(f"  {nm}:")
        for y in (None, 0.01, 0.02, 0.03, 0.05):
            Y = y
            o = []
            for le in (False, True):
                tot = re = 0.0
                n = t = s = 0
                for p in ps:
                    ls = legs(p, mg, None, le, 2, size_bal=mg)
                    tot += sum(r[1] for r in ls)
                    if len(ls) == 2:
                        re += ls[1][1]
                        n += 1
                        t += ls[1][0] == "TARGET"
                        s += ls[1][0] in ("STOP", "LIQ")
                o.append((tot, n, t, s, re))
            (a0, n0, t0, s0, r0), (a1, n1, t1, s1, r1) = o
            lab = "run 4 as built" if y is None else f"sell {y:.0%} off the top"
            ex = "" if y is None else f" | re-entries {n0}..{n1}: {t0}..{t1} targets, {s0}..{s1} stops, re-entry P&L ${r0:+,.0f} .. ${r1:+,.0f}"
            print(f"    {lab:22s} total ${a0:+9,.0f} .. ${a1:+9,.0f}{ex}")
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    print("\nAs an account from $3,184 (fill-first, 2-stops hard stop, scaling), STRICT .. LENIENT:")
    for y in (None, 0.01, 0.02, 0.03, 0.05):
        Y = y
        r = [RS.account(real, "x", le) for le in (False, True)]
        yy = [RS.account(y26, "x", le) for le in (False, True)]
        h = [RS.account(allp, "x", le) for le in (False, True)]
        lab = "as built" if y is None else f"{y:.0%} off the top"
        print(f"   {lab:15s}: 20 real picks ${r[0]:,.0f} .. ${r[1]:,.0f} | 2026 to Nov 14 ${yy[0]:,.0f} .. ${yy[1]:,.0f} | history + real ${h[0]:,.0f} .. ${h[1]:,.0f}")


if __name__ == "__main__":
    main()
