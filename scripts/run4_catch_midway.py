"""Owner 2026-11-15: "Can we not catch it midway? At that point we know it's surely falling." -- after a full stop,
sell at market only once the fall is well under way: Y = 7.5/10/15% below the high since the stop, or back below the
stop price, or back below the original sell price. Same machinery and rules as scripts/run4_catch_fall.py.
Plus the core fact: from such a point, does the price fall a further 1.8% (≈ the target) before it rises 2.25% (the stop)?

    PYTHONPATH=. python scripts/run4_catch_midway.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_catch_fall as CF  # noqa: E402
import run4_margin75 as M  # noqa: E402
import run4_resell as RS  # noqa: E402

MODE = None                                              # ("off_top", y) | ("below", "stop") | ("below", "entry")


def trigger(p, r, lenient):
    day, sym, hi, lo, cl, k0 = p
    ks = r[3] + 1
    if MODE[0] == "off_top":
        return CF.fall_entry(hi, lo, cl, ks, MODE[1], lenient)
    e = cl[k0 - 1] * 1.05
    level = e * 1.0225 if MODE[1] == "stop" else e
    for k in range(ks, min(len(cl), 1440)):
        if lo[k] <= level:
            return k, (level if lenient else min(level, cl[k])) * 0.999
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
    if MODE is None or r[0] not in ("STOP", "LIQ") or stops_left <= 1 or r[3] >= 1439:
        return out
    b2 = size_bal if size_bal is not None else max(bal + r[1], 0.0)
    f = trigger(p, r, lenient) if b2 >= 50 else None
    if f:
        r2 = go(f[0] + 1, f[1], False, b2)
        if r2:
            out.append(r2)
    return out


def race(hi, lo, cl, k, px, down=0.018, up=0.0225):
    """From a sell at px after minute k: does a low reach px(1-down) before a high reaches px(1+up)? (+1 / -1 / 0)"""
    for j in range(k + 1, min(len(cl), k + 1441)):
        if hi[j] >= px * (1 + up):
            return -1
        if lo[j] <= px * (1 - down):
            return 1
    return 0


def main():
    global MODE
    C.checks()
    M.load_ranks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    allp = hist + real
    RS.legs = legs
    modes = [None, ("off_top", 0.03), ("off_top", 0.05), ("off_top", 0.075), ("off_top", 0.10), ("off_top", 0.15),
             ("below", "stop"), ("below", "entry")]
    lab = lambda m: "run 4 as built" if m is None else (f"{m[1]:.1%} off the high" if m[0] == "off_top" else f"back below our {m[1]} price")  # noqa: E731
    print("Once it is 'surely falling': from the sell point, does it fall another 1.8% (target) before it rises 2.25%")
    print("(stop)? Full stops in history + real picks, strict sell price:")
    for m in modes[1:]:
        MODE = m
        w = l_ = z = 0
        for p in allp:
            CF.Y = None
            ls = legs(p, C.START, None, False, 2, size_bal=C.START)
            if not ls or ls[0][0] not in ("STOP", "LIQ") or ls[0][3] >= 1439:
                continue
            f = trigger(p, ls[0], False)
            if f is None:
                continue
            res = race(p[2], p[3], p[4], f[0], f[1])
            w += res == 1
            l_ += res == -1
            z += res == 0
        n = w + l_ + z
        print(f"   {lab(m):26s} reached {n:3d} of 177 | falls 1.8% first {w:3d} ({w / max(n, 1):.0%}) | rises 2.25% first {l_:3d} | neither {z}")
    print("   (break-even needs about 2.25/(1.8+2.25) = 56% 'falls first' before costs; more after costs)\n")
    print("Per trade on a fixed $3,184, STRICT .. LENIENT:")
    for nm, ps in (("20 real picks", real), ("full history + real", allp)):
        print(f"  {nm}:")
        for m in modes:
            MODE = m
            o = []
            for le in (False, True):
                tot = re = 0.0
                n = t = s = 0
                for p in ps:
                    ls = legs(p, C.START, None, le, 2, size_bal=C.START)
                    tot += sum(r[1] for r in ls)
                    if len(ls) == 2:
                        re += ls[1][1]
                        n += 1
                        t += ls[1][0] == "TARGET"
                        s += ls[1][0] in ("STOP", "LIQ")
                o.append((tot, n, t, s, re))
            (a0, n0, t0, s0, r0), (a1, n1, t1, s1, r1) = o
            ex = "" if m is None else f" | re-entries {n0}..{n1}: {t0}..{t1} targets, {s0}..{s1} stops, re-entry P&L ${r0:+,.0f} .. ${r1:+,.0f}"
            print(f"    {lab(m):26s} total ${a0:+9,.0f} .. ${a1:+9,.0f}{ex}")
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    print("\nAs an account from $3,184 (fill-first, 2-stops hard stop, scaling), STRICT .. LENIENT:")
    for m in modes:
        MODE = m
        r = [RS.account(real, "x", le) for le in (False, True)]
        yy = [RS.account(y26, "x", le) for le in (False, True)]
        h = [RS.account(allp, "x", le) for le in (False, True)]
        print(f"   {lab(m):26s}: 20 real ${r[0]:,.0f} .. ${r[1]:,.0f} | 2026 ${yy[0]:,.0f} .. ${yy[1]:,.0f} | history + real ${h[0]:,.0f} .. ${h[1]:,.0f}")


if __name__ == "__main__":
    main()
