"""Owner 2026-11-16: "find another strategy, you have the data -- losers should not equal winners in number or in
size; high leverage, quick exit". The one effect in the data is the give-back after a top gainer spikes. This asks
whether a BIGGER spike gives back more reliably: the resting sell sits 5 / 7 / 10 / 15 / 20 / 30% above the 00:02
close, with a 5% or 10% stop and a 1 / 2 / 4 / 8 hour clock, no target.

DISCOVERY = the two daily picks (careful dataset). CHECK = the coin-days of scripts/spike_breadth.py that the picks
never used (3rd-5th gainers, other gainers, non-gainers), same rule, never looked at for any entry but +5%.
Before fees; STRICT and LENIENT fills as scripts/spike_flip_back.py (asserted equal to it at +5% / 10% / 8 h).

    PYTHONPATH=. python scripts/spike_size.py          # -> out/careful/spike_size.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_breadth import PICKS, arrays  # noqa: E402
from spike_flip_back import trade as base  # noqa: E402

OFFS, STOPS, HOURS = (0.05, 0.07, 0.10, 0.15, 0.20, 0.30), (0.05, 0.10), (1, 2, 4, 8)


def trade(H, L, Cl, off, stop, hours, fill):
    e = Cl[2] * (1 + off)
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if not f.size:
        return None
    k = 3 + int(f[0])
    last = min(2880, k + hours * 60)
    h, c = H[k + 1:last + 1], Cl[k + 1:last + 1]
    sp = e * (1 + stop)
    hit = np.flatnonzero(h >= sp)
    if hit.size:
        j = int(hit[0])
        return -((max(sp, c[j]) if fill == "STRICT" else sp) * 1.001 / e - 1)
    return -(Cl[last] * 1.001 / e - 1)


def table(name, sets, out):
    out.append(f"\n===== {name}")
    out.append(f"{'entry':>6} {'stop':>5} {'clock':>5} | {'trades':>6} {'win':>4} {'avg win':>8} {'avg loss':>8} | {'STRICT':>8} {'R':>6} {'t':>5} {'2025':>7} {'2026':>7} | {'LENIENT':>8} {'t':>5} | flags")
    res = {}
    for off in OFFS:
        for stop in STOPS:
            for hours in HOURS:
                r = {f: pd.DataFrame([(d, x) for d, a in sets for x in [trade(*a, off, stop, hours, f)] if x is not None], columns=["day", "ret"]) for f in ("STRICT", "LENIENT")}
                x, y = r["STRICT"], r["LENIENT"]
                if len(x) < 30:
                    out.append(f"{off:6.0%} {stop:5.0%} {hours:3d} h | {len(x):6d}  -")
                    continue
                tt = lambda z: (lambda g: g.mean() / (g.std(ddof=1) / np.sqrt(len(g))))(z.groupby("day").ret.sum())  # noqa: E731
                w, l = x.ret[x.ret > 0], x.ret[x.ret <= 0]
                y25, y26 = x.ret[x.day.dt.year == 2025].mean(), x.ret[x.day.dt.year >= 2026].mean()
                win = (x.ret > 0).mean()
                flags = ("win>55% " if win > 0.55 else "") + ("W>L " if w.mean() > -l.mean() else "") + ("both years " if y25 > 0 and y26 > 0 else "")
                res[(off, stop, hours)] = (len(x), win, w.mean(), l.mean(), x.ret.mean(), tt(x), y25, y26, y.ret.mean())
                out.append(f"{off:6.0%} {stop:5.0%} {hours:3d} h | {len(x):6d} {100 * win:3.0f}% {100 * w.mean():+7.2f}% {100 * l.mean():+7.2f}% | {100 * x.ret.mean():+7.3f}% {x.ret.mean() / stop:+6.3f} {tt(x):+5.2f} "
                           f"{100 * y25:+6.2f}% {100 * y26:+6.2f}% | {100 * y.ret.mean():+7.3f}% {tt(y):+5.2f} | {flags}")
    return res


def main():
    picks = [(d, (p[2], p[3], p[4])) for d, ps in D.load() for p in ps]
    for d, a in picks:                                                 # same mechanics as the validated function
        x, b = trade(*a, 0.05, 0.10, 8, "STRICT"), base(*a, None, 8, "STRICT")
        assert (x is None) == (b is None) and (x is None or abs(x - b[0]) < 1e-12)
    P = pd.read_csv(PICKS, parse_dates=["day"])
    chk = {g: [] for g in ("RANK 3-5", "OTHER GAINERS", "NON-GAINERS")}
    for x in P.itertuples():
        a = arrays(x.symbol, int(x.time))
        if a is not None and np.isfinite(a[2][2]):
            chk[x.group].append((x.day, a))
    out = ["DOES A BIGGER SPIKE GIVE BACK MORE RELIABLY? Resting sell X% above the 00:02 close, no target. Before fees. R = result / stop."]
    res = {"picks": table(f"DISCOVERY: the two daily picks ({len(picks)} coin-days)", picks, out)}
    res["r35"] = table(f"CHECK: 3rd-5th gainers ({len(chk['RANK 3-5'])} coin-days, untouched)", chk["RANK 3-5"], out)
    rest = chk["OTHER GAINERS"] + chk["NON-GAINERS"]
    res["rest"] = table(f"CHECK: other gainers and non-gainers ({len(rest)} coin-days, untouched)", rest, out)
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_size.txt").write_text(txt + "\n")
    pd.to_pickle(res, "out/careful/spike_size.pkl")


if __name__ == "__main__":
    main()
