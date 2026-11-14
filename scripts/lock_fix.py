"""Owner 2026-11-15: "how to fix it?" -- the +$200 lock can be late: when the bot moves the stop at the minute's close the
price may already be back above the lock level, and the stop then fires at the market. Three fixes, the owner's
version otherwise (spike +5%, 50% margin at 20x, $500 target, stop +2.25%), fill rules of scripts/clean_compare.py,
STRICT and LENIENT stop fills:
  A  lock a smaller profit (+$100) after a 1% fall       (more room between the move and the lock level)
  B  move later: lock +$200 after a 1.5% fall
  C  a RESTING partial take-profit placed with the entry: limit buy for 40% of the position at -1% (fills at the
     exchange, no bot delay; maker fee, must trade 0.1% through); when it fills, the stop on the remaining 60% moves
     to breakeven after costs (same timing rule as the lock); the remaining 60% keeps the original target PRICE.

    PYTHONPATH=. python scripts/lock_fix.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402

FRAC, TP1 = 0.40, 0.01


def partial(sym, hi, lo, cl, k0, margin, target, entry, lock, stop=0.0225, lev=None, take_frac=None, extra=0.0, lenient=False):
    """Fix C. Same signature/return as clean_compare.short."""
    n = len(cl)
    lim = cl[k0 - 1] * 1.05
    kf = next((k for k in range(k0, min(n, 1440)) if hi[k] >= lim * 1.0005), None)
    if kf is None:
        return None
    e, fin, sin = lim, C.MAKER, C.SPREAD / 2
    pos, liqd = C.size(sym, margin, e)
    at_risk = margin
    cost = fin + C.FEE + sin
    leg = lambda q, xp, fee_out: q * (-(xp / e - 1) - fin - fee_out * xp / e - sin)   # noqa: E731
    tp = e * (1 - (target / pos + cost))
    tp1 = e * (1 - TP1)
    sp, liq = e * (1 + stop), e * (1 + liqd)
    rest, banked, moved = pos, 0.0, False
    end = min(n - 1, kf + 1440)
    for k in range(kf + 1, end + 1):
        if hi[k] >= sp:
            xp = sp * 1.001 if lenient else max(sp, cl[k]) * 1.001
            if xp >= liq and not moved:
                return "LIQ", -at_risk, kf, k
            return ("BE" if moved else "STOP"), max(banked + leg(rest, xp, C.FEE), -at_risk), kf, k
        if lo[k] <= tp * 0.999:
            return "TARGET", banked + leg(rest, tp, C.FEE), kf, k
        if not moved and lo[k] <= tp1 * 0.999:
            banked = leg(FRAC * pos, tp1, C.MAKER)                     # the resting limit: filled at the exchange
            rest = (1 - FRAC) * pos
            moved = True
            sp = e * (1 - cost)                                        # breakeven on the rest, moved at this minute's close
            if cl[k] >= sp:
                return "BE", banked + leg(rest, cl[k] * 1.001, C.FEE), kf, k
    return "24h", banked + leg(rest, cl[end] * 1.001, C.FEE), kf, end


def run(fn, paths, cfg):
    res = [fn(sym, hi, lo, cl, k0, cfg["margin"] * C.START, cfg["target"], "spike", cfg["lock"], cfg["stop"], None, None, 0.0,
              lenient=cfg["len"]) for day, sym, hi, lo, cl, k0 in paths]
    res = [r for r in res if r]
    v = np.array([r[1] for r in res])
    h = np.array([r[0] for r in res])
    return v, h


def main() -> None:
    C.checks()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    hist, *_ = C.paths_history()
    versions = [("now: lock +$200 after a 1% fall", C.short, dict(lock=200, trig=0.01)),
                ("A: lock +$100 after a 1% fall", C.short, dict(lock=100, trig=0.01)),
                ("B: lock +$200 after a 1.5% fall", C.short, dict(lock=200, trig=0.015)),
                ("C: resting take 40% at -1%, rest to breakeven", partial, dict(lock=None, trig=None))]
    print(f"FIXING THE LOCK. Spike +5%, 50% margin at 20x, $500 target, stop +2.25%. Each trade on a fixed ${C.START:,.0f}.\n")
    print(f"  {'version':48s} | {'REAL 20 picks: strict':>21s} {'lenient':>8s} | {'HISTORY avg/trade: strict':>25s} {'lenient':>8s} | "
          f"{'stops':>5s} {'small exits':>11s} {'of which < $0 (strict)':>22s}")
    for name, fn, extra in versions:
        out = {}
        for le in (False, True):
            if extra["trig"] and extra["trig"] != 0.01:                # B: trigger 1.5% -> patch the 0.99 in clean_compare
                src_fn = fn
                def fn2(*a, lenient=False, _f=src_fn, **k):
                    import types
                    return _f(*a, lenient=lenient, **k)
            cfg = dict(margin=0.5, target=500, lock=extra["lock"], stop=0.0225, len=le)
            if extra["trig"] == 0.015:
                rv, rh = run(lambda *a, **k: trig15(*a, **k), real, cfg)
                hv, hh = run(lambda *a, **k: trig15(*a, **k), hist, cfg)
            else:
                rv, rh = run(fn, real, cfg)
                hv, hh = run(fn, hist, cfg)
            out[le] = (rv.sum(), hv.mean(), hh)
        hh = out[False][2]
        small = np.isin(hh, ("LOCKED", "BE"))
        hv_s = run(fn if extra["trig"] != 0.015 else (lambda *a, **k: trig15(*a, **k)), hist, dict(margin=0.5, target=500, lock=extra["lock"], stop=0.0225, len=False))[0]
        print(f"  {name:48s} | {out[False][0]:+21,.0f} {out[True][0]:+8,.0f} | {out[False][1]:+25.1f} {out[True][1]:+8.1f} | "
              f"{100 * (hh == 'STOP').mean():4.0f}% {100 * small.mean():10.0f}% {100 * (hv_s[small] < 0).mean():21.0f}%")


def trig15(sym, hi, lo, cl, k0, margin, target, entry, lock, stop=0.0225, lev=None, take_frac=None, extra=0.0, lenient=False):
    """clean_compare.short with the lock moved after a 1.5% fall (instead of 1%)."""
    lo2 = np.where(lo <= cl[k0 - 1] * 1.05 * 0.985, lo, np.maximum(lo, cl[k0 - 1] * 1.05 * 0.99 + 1e-12))
    # keep the true lows for the target test: only the lock trigger changes, so run short on a copy whose lows above
    # the 1.5% level are lifted just above the 1% level (the target, 1.73% below the fill, is unaffected).
    return C.short(sym, hi, lo2, cl, k0, margin, target, entry, lock, stop, lev, take_frac, extra, lenient=lenient)


if __name__ == "__main__":
    main()
