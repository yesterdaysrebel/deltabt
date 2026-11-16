"""Owner 2026-11-16: "find high leverage, early exit strategy with same point, avg win greater than avg loss, per
trade more money". Same entry point as the spike fade (resting sell 5% above the 00:02 close, both picks, 617 fills,
2025-01 .. 2026-11), with a TIGHT stop so high leverage is possible, an early clock, and a target sized in stops.

Every cell is printed. R = result / stop distance: at a fixed share of the account risked per trade, money per trade
is proportional to R, so "more money per trade" means a higher R than the wide version (10% stop, 8 h: +0.08R).
Fills as docs/careful_sim_spec.md (stop: STRICT worse of stop and the minute's close +0.1%, LENIENT stop +0.1%; target
must trade 0.1% through; the stop wins a shared minute; clock exit 0.1% worse). The 24 h cells are asserted equal to
scripts/review_real_fees.py::trade. Before fees; the last column is after the real fees.

    PYTHONPATH=. python scripts/spike_tight.py         # -> out/careful/spike_tight.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import review_real_fees as V  # noqa: E402

ZERO = dict(fin=0.0, f_tp=0.0, f_mkt=0.0)
STOPS = (0.01, 0.015, 0.02, 0.03, 0.05)
MULTS = (None, 2, 3, 5)
CLOCKS = (30, 60, 120, 240, 480)
FEE_M, FEE_T = 0.000212, 0.000531


def trade(H, L, Cl, tgt, stop, minutes, fill):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if not f.size:
        return None
    k = 3 + int(f[0])
    last = min(2880, k + minutes)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    sp = e * (1 + stop)
    si = np.flatnonzero(h >= sp)
    a = int(si[0]) if si.size else 10 ** 9
    b = 10 ** 9
    if tgt is not None:
        ti = np.flatnonzero(l <= e * (1 - tgt) * 0.999)
        b = int(ti[0]) if ti.size else 10 ** 9
    if a <= b and a < 10 ** 9:
        x = (max(sp, c[a]) if fill == "STRICT" else sp) * 1.001
        return -(x / e - 1), "STOP"
    if b < 10 ** 9:
        return tgt, "TARGET"
    return -(Cl[last] * 1.001 / e - 1), "CLOCK"


def main():
    days = D.load()
    picks = [(d, p) for d, ps in days for p in ps]
    for d, p in picks[:300]:                                           # same mechanics as the validated simulator at 24 h
        for t, s in ((None, 0.03), (0.06, 0.03), (0.04, 0.02)):
            for fill in ("STRICT", "LENIENT"):
                a, b = trade(p[2], p[3], p[4], t, s, 1440, fill), V.trade(p[2], p[3], p[4], t, s, fill, ZERO)
                assert (a is None) == (b is None) and (a is None or abs(a[0] - b[1]) < 1e-12), (d, p[1], t, s, fill)
    out = ["SPIKE FADE ENTRY (+5%) WITH A TIGHT STOP, AN EARLY CLOCK AND A TARGET IN STOPS. Before fees. R = result / stop.",
           "leverage = the most that keeps Delta's liquidation 1.5 stops away (liquidation distance ~ 1/leverage - 2.5%).",
           f"{'stop':>5} {'target':>7} {'clock':>6} {'lev':>4} | {'win':>4} {'avg win':>8} {'avg loss':>8} | {'STRICT':>8} {'R':>7} {'t':>5} {'2025':>7} {'2026':>7} | {'LENIENT':>8} {'R':>7} {'t':>5} | {'strict after fees':>17} | flags"]
    rows = []
    for s in STOPS:
        lev = min(100, int(1 / (1.5 * s + 0.025)))
        for m in MULTS:
            for mins in CLOCKS:
                t = None if m is None else m * s
                res = {}
                for fill in ("STRICT", "LENIENT"):
                    r = [(d, trade(p[2], p[3], p[4], t, s, mins, fill)) for d, p in picks]
                    res[fill] = pd.DataFrame([(d, a[0], a[1]) for d, a in r if a], columns=["day", "ret", "how"])
                x, y = res["STRICT"], res["LENIENT"]
                tt = lambda z: (lambda g: g.mean() / (g.std(ddof=1) / np.sqrt(len(g))))(z.groupby("day").ret.sum())  # noqa: E731
                w, l = x.ret[x.ret > 0], x.ret[x.ret <= 0]
                y25, y26 = x.ret[x.day.dt.year == 2025].mean(), x.ret[x.day.dt.year >= 2026].mean()
                net = x.ret - FEE_M - np.where(x.how == "TARGET", FEE_M, FEE_T) * (1 - x.ret)
                flags = ("W>L " if w.mean() > -l.mean() else "") + ("strict>0 " if x.ret.mean() > 0 else "") + ("both years " if y25 > 0 and y26 > 0 else "") + \
                        ("beats wide " if x.ret.mean() / s > 0.082 else "")
                rows.append((s, m, mins, x.ret.mean() / s, y.ret.mean() / s, y25 > 0 and y26 > 0, w.mean() > -l.mean()))
                out.append(f"{s:5.1%} {('none' if m is None else f'{m}x stop'):>7} {mins:4d} m {lev:3d}x | {100 * (x.ret > 0).mean():3.0f}% {100 * w.mean():+7.2f}% {100 * l.mean():+7.2f}% | "
                           f"{100 * x.ret.mean():+7.3f}% {x.ret.mean() / s:+7.3f} {tt(x):+5.2f} {100 * y25:+6.2f}% {100 * y26:+6.2f}% | {100 * y.ret.mean():+7.3f}% {y.ret.mean() / s:+7.3f} {tt(y):+5.2f} | "
                           f"{100 * net.mean():+16.3f}% | {flags}")
    G = pd.DataFrame(rows, columns=["stop", "mult", "mins", "R_strict", "R_lenient", "both", "wl"])
    out.append(f"\n{len(G)} cells: avg win > avg loss in {int(G.wl.sum())}; STRICT > 0 in {int((G.R_strict > 0).sum())}; STRICT > 0 in both years in {int(((G.R_strict > 0) & G.both).sum())}; "
               f"STRICT R above the wide version's +0.082R in {int((G.R_strict > 0.082).sum())}; LENIENT > 0 in {int((G.R_lenient > 0).sum())}; best STRICT R {G.R_strict.max():+.3f}, best LENIENT R {G.R_lenient.max():+.3f}")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_tight.txt").write_text(txt + "\n")
    G.to_pickle("out/careful/spike_tight.pkl")


if __name__ == "__main__":
    main()
