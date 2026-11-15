"""Run 4 exactly as built (live/spike_live.py, option A): 49% margin at 20x, extra 25% isolated margin from the fill,
spike +5%, $500 target, stop +2.25% (last traded), lock +$200 after a 1% fall, 24 h, off for the month after 2 full
stops, one position at a time. Fill rules and checks of scripts/clean_compare.py; STRICT and LENIENT stop fills.

    PYTHONPATH=. python scripts/run4_final_numbers.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402

CFG = dict(entry="spike", margin=0.49, target=500, lock=200, stop=0.0225, extra=0.25)


def per_trade(paths, lenient):
    out = []
    for day, sym, hi, lo, cl, k0 in paths:
        r = C.short(sym, hi, lo, cl, k0, CFG["margin"] * C.START, 500, "spike", 200, 0.0225, None, None,
                    CFG["extra"] * C.START, lenient=lenient)
        if r:
            out.append((day, sym, r))
    return out


def account(paths, lenient, off):
    orig = C.short
    C.short = lambda *a, **k: orig(*a, lenient=lenient, **k)
    try:
        return C.account(paths, CFG, months_off_after=off)
    finally:
        C.short = orig


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    print(f"RUN 4 AS BUILT, from ${C.START:,.0f}: 49% margin at 20x + 25% extra margin from the fill (option A)\n")
    for label, paths in (("20 REAL PICKS 2026-10-29..11-14", real), ("FULL HISTORY 2025-01..2026-10 (1,022 picks)", hist)):
        print(label)
        for le, name in ((False, "STRICT"), (True, "LENIENT")):
            res = per_trade(paths, le)
            v = np.array([r[1] for *_, r in res]); h = np.array([r[0] for *_, r in res])
            mo = pd.Series(v).groupby([d.strftime("%Y-%m") for d, *_ in res]).sum()
            print(f"  {name:7s}: fills {len(v):3d} | target {np.sum(h == 'TARGET'):3d} locked {np.sum(h == 'LOCKED'):3d} stop {np.sum(h == 'STOP'):3d} "
                  f"LIQUIDATED {np.sum(h == 'LIQ'):2d} 24h {np.sum(h == '24h'):2d} | avg ${v.mean():+7.1f}/trade, sum ${v.sum():+9,.0f} | "
                  f"avg full stop ${v[h == 'STOP'].mean():,.0f}, worst ${v.min():,.0f} | green months {(mo > 0).sum()}/{len(mo)}")
            a_off, low_off = account(paths, le, 2)
            a_no, low_no = account(paths, le, None)
            print(f"           account: with the 2-stops/month hard stop ${a_off:,.0f} (lowest ${low_off:,.0f}) | "
                  f"no hard stop ${a_no:,.0f} (lowest ${low_no:,.0f})")
        print()
    print("21 MONTHLY STARTS (history), with the hard stop")
    for le, name in ((False, "STRICT"), (True, "LENIENT")):
        gone = up3 = 0
        b12 = []
        for m in pd.period_range("2025-02", "2026-10", freq="M"):
            s = m.to_timestamp()
            sub = [x for x in hist if x[0] >= s]
            gone += account(sub, le, 2)[1] < 1000
            up3 += account([x for x in sub if x[0] < s + pd.Timedelta(days=90)], le, 2)[0] > C.START
            if s + pd.Timedelta(days=365) <= hist[-1][0]:
                b12.append(account([x for x in sub if x[0] < s + pd.Timedelta(days=365)], le, 2)[0])
        print(f"  {name:7s}: fell under $1,000 in {gone}/21; ahead after 3 months in {up3}/21; median balance after 12 months ${np.median(b12):,.0f}")


if __name__ == "__main__":
    main()
