"""Owner 2026-11-15: "how about same test with tight SL" -- scripts/margin_target_grid.py with stops +0.5% / +0.75% /
+1.0% (fill rules and checks of scripts/clean_compare.py; the +$200 lock still moves after a 1% fall).

    PYTHONPATH=. python scripts/tight_sl_grid.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import margin_target_grid as G  # noqa: E402


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    print(f"TIGHT STOPS. Spike +5%, 20x on the trading margin, lock +$200 after a 1% fall, off for the month after 2 full stops. Start ${C.START:,.0f}.\n")
    print(f"{'margin':>6s} {'extra':>5s} {'stop':>5s} {'target':>6s} | {'tgt':>4s} {'lock':>4s} {'stop':>4s} {'LIQ':>7s} | {'avg/tr':>7s} {'stop avg':>8s} "
          f"{'worst':>7s} {'green':>5s} | {'real':>6s} {'hist end':>8s} {'lowest':>6s} {'<$1k':>5s} {'up3m':>5s} {'med12m':>7s}")
    for margin, extras in ((0.50, (0.0, 0.25)), (0.75, (0.0, 0.20))):
        for extra in extras:
            for stop in (0.005, 0.0075, 0.01):
                for target in (300, 500, 700, 1000):
                    r = G.summary(hist, real, margin, target, stop, extra, 2)
                    print(f"{100 * margin:5.0f}% {100 * extra:4.0f}% {100 * stop:4.2f}% {target:6d} | {100 * r['tgt']:3.0f}% {100 * r['lck']:3.0f}% "
                          f"{100 * r['stp']:3.0f}% {100 * r['liq']:3.0f}%({r['liq_n']:3d}) | {r['avg']:+7.1f} {r['stop_avg']:8,.0f} {r['worst']:7,.0f} "
                          f"{r['green']:2d}/{r['months']:<2d} | {r['real']:6,.0f} {r['end']:8,.0f} {r['low']:6,.0f} {r['gone']:2d}/21 {r['up3']:2d}/21 {r['med12']:7,.0f}")


if __name__ == "__main__":
    main()
