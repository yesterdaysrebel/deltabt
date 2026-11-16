"""Owner 2026-11-15: "how do we avoid liquidation with my chosen version?" -- the chosen version (spike +5%, 50% of the
balance as margin at 20x, $500 target, lock +$200 after a 1% fall, off for the month after 2 full stops) with
(a) EXTRA isolated margin added after the fill (same contracts, same target and stop; liquidation further out; a
liquidation then loses margin + extra) and (b) tighter stops. The fill rules of scripts/clean_compare.py, its
checks run first.

    PYTHONPATH=. python scripts/avoid_liquidation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    pos0 = C.size("XUSD", 0.5 * C.START, 1.0)[0]
    print(f"YOUR VERSION: spike +5%, ${0.5 * C.START:,.0f} margin at 20x = ${pos0:,.0f} position on ${C.START:,.0f}, $500 target, "
          "lock +$200 after a 1% fall, off for the month after 2 full stops.\n")
    print(f"{'stop':>6s} {'extra':>6s} {'liq. at':>7s} | {'target':>6s} {'locked':>6s} {'stop':>5s} {'LIQ':>4s} | {'avg/trade':>9s} {'stop avg':>8s} "
          f"{'worst':>7s} {'green mo':>8s} | {'real picks':>10s} {'history end':>11s} {'lowest':>7s} {'starts <$1k':>11s} {'up 3 mo':>7s} {'med 12 mo':>9s}")
    for stop in (0.0225, 0.0175, 0.015, 0.01):
        for extra in (0.0, 0.25, 0.45):
            cfg = dict(entry="spike", margin=0.5, target=500, lock=200, stop=stop, extra=extra)
            res = [(day, C.short(sym, hi, lo, cl, k0, 0.5 * C.START, 500, "spike", 200, stop, None, None, extra * C.START))
                   for day, sym, hi, lo, cl, k0 in hist]
            res = [(dd, r) for dd, r in res if r]
            v = np.array([r[1] for _, r in res]); h = np.array([r[0] for _, r in res])
            mo = pd.Series(v).groupby([dd.strftime("%Y-%m") for dd, _ in res]).sum()
            rb, _ = C.account(real, cfg, months_off_after=2)
            hb, hl = C.account(hist, cfg, months_off_after=2)
            gone = up3 = 0
            b12 = []
            for m in pd.period_range("2025-02", "2026-10", freq="M"):
                s = m.to_timestamp()
                sub = [x for x in hist if x[0] >= s]
                gone += C.account(sub, cfg, months_off_after=2)[1] < 1000
                up3 += C.account([x for x in sub if x[0] < s + pd.Timedelta(days=90)], cfg, months_off_after=2)[0] > C.START
                if s + pd.Timedelta(days=365) <= hist[-1][0]:
                    b12.append(C.account([x for x in sub if x[0] < s + pd.Timedelta(days=365)], cfg, months_off_after=2)[0])
            print(f"{100 * stop:5.2f}% {100 * extra:5.0f}% {100 * (0.025 + extra * C.START / pos0):6.2f}% | {100 * (h == 'TARGET').mean():5.0f}% "
                  f"{100 * (h == 'LOCKED').mean():5.0f}% {100 * (h == 'STOP').mean():4.0f}% {100 * (h == 'LIQ').mean():3.0f}% | {v.mean():+9.1f} "
                  f"{v[h == 'STOP'].mean():8,.0f} {v.min():7,.0f} {(mo > 0).sum():>4d}/{len(mo):<3d} | {rb:10,.0f} {hb:11,.0f} {hl:7,.0f} "
                  f"{gone:6d}/21 {up3:4d}/21 {np.median(b12):9,.0f}")


if __name__ == "__main__":
    main()
