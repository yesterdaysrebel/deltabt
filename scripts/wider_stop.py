"""Owner 2026-11-15: "for the ones where SL hits can we not move SL?" -- moving the stop AWAY (more room) needs more
isolated margin at 20x or liquidation comes first; adding it when the price nears the stop is the same as having it in
place from the fill. So: wider stops 3% / 4% / 5% with extra margin keeping liquidation >= 2% beyond the stop.
Everything else as the owner's version (spike +5%, 50% trading margin at 20x, lock +$200 after a 1% fall).
STRICT and LENIENT stop fills (scripts/real_trades_only.py), checks of scripts/clean_compare.py first.

    PYTHONPATH=. python scripts/wider_stop.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import real_trades_only as RT  # noqa: E402


def main() -> None:
    C.checks()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    hist, *_ = C.paths_history()
    pos = C.size("XUSD", 0.5 * C.START, 1.0)[0]
    print(f"WIDER STOP + EXTRA MARGIN. Spike +5%, ${0.5 * C.START:,.0f} trading margin at 20x (${pos:,.0f} position), lock +$200 "
          f"after a 1% fall. Start ${C.START:,.0f}.\n")
    rows = [(0.0225, 0.0), (0.0225, 0.25)]
    for stop in (0.03, 0.04, 0.05):
        extra = max(0.0, (stop + 0.02 - 0.025) * pos / C.START)
        rows.append((stop, round(extra, 2)))
    for target in (500, 700):
        print(f"TARGET ${target}")
        print(f"  {'stop':>5s} {'extra':>5s} {'margin used':>11s} {'liq at':>6s} | {'REAL: fixed size total':>23s} {'account, no hard stop':>22s} | "
              f"{'HISTORY: avg/trade':>19s} {'stop avg':>8s} {'account end':>17s}")
        for stop, extra in rows:
            cfg = dict(entry="spike", margin=0.5, target=target, lock=200, stop=stop, extra=extra)
            out = {}
            for key, fn in (("s", RT.STRICT), ("l", RT.lenient)):
                rr = [RT.one(fn, p, cfg, C.START) for p in real]
                rr = [r for r in rr if not isinstance(r, str)]
                hh = [RT.one(fn, p, cfg, C.START) for p in hist]
                hh = [r for r in hh if not isinstance(r, str)]
                v = np.array([r[1] for r in hh]); h = np.array([r[0] for r in hh])
                acc_r = RT.account(fn, real, cfg)[1]
                acc_h = RT.account(fn, hist, cfg)[1]
                out[key] = (sum(r[1] for r in rr), acc_r, v.mean(), v[h == "STOP"].mean(), acc_h)
            s, l = out["s"], out["l"]
            print(f"  {100 * stop:4.2f}% {100 * extra:4.0f}% {100 * (0.5 + extra):10.0f}% {100 * (0.025 + extra * C.START / pos):5.2f}% | "
                  f"{s[0]:+10,.0f} .. {l[0]:+10,.0f} {s[1]:10,.0f} .. {l[1]:9,.0f} | {s[2]:+8.1f} .. {l[2]:+7.1f} {s[3]:8,.0f} {s[4]:7,.0f} .. {l[4]:7,.0f}")
        print()
    print("THE STOPPED REAL TRADES: how far they ran after the fill (strict, stop 2.25%)")
    for p in real:
        day, sym, hi, lo, cl, k0 = p
        r = RT.one(RT.STRICT, p, dict(entry="spike", margin=0.5, target=500, lock=200, stop=0.0225), C.START)
        if isinstance(r, str) or r[0] != "STOP":
            continue
        e = cl[k0 - 1] * 1.05
        kf = r[2]
        peak = max(hi[kf + 1:min(len(hi), kf + 1441)])
        k_peak = kf + 1 + int(np.argmax(hi[kf + 1:min(len(hi), kf + 1441)]))
        low_after = min(lo[kf + 1:min(len(lo), kf + 1441)])
        print(f"  {day.strftime('%m-%d')} {sym[:-3]:8s} fill {e:.6g} at {C.Q.hm(kf)}: highest after the fill {peak:.6g} ({100 * (peak / e - 1):+.1f}%) at "
              f"{C.Q.hm(k_peak)}; lowest {low_after:.6g} ({100 * (low_after / e - 1):+.1f}%)")


if __name__ == "__main__":
    main()
