"""Owner 2026-11-15: "stops should not reach with lock, or even when price moves against the lock should adjust and cut
loose" -- run 4 as now built (scaled target and lock, 2-stops/month limit, extra margin, spike +5%, 49% at 20x) plus:
  TRAIL d    after the +$200 lock is in, the stop follows the price down: at each minute's close it moves to
             min(stop, lowest low since the fill x (1 + d)); never up. (Delta trailing stops can do this natively.)
  BE 0.5%    after a 0.5% fall the stop moves to breakeven (entry + costs); the +$200 lock still follows at 1%.
Same fill rules as scripts/clean_compare.py (STRICT / LENIENT). Self-check first: no trail and no BE must equal
clean_compare.short trade by trade.

    PYTHONPATH=. python scripts/run4_trailing.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_pause_rules as P  # noqa: E402

CFG = P.CFG


def short(sym, hi, lo, cl, k0, margin, target, lock, extra, lenient, trail=None, be=None):
    """clean_compare.short (spike entry, 20x) with an optional trailing stop after the lock and an earlier breakeven."""
    n = len(cl)
    lim = cl[k0 - 1] * 1.05
    kf = next((k for k in range(k0, min(n, 1440)) if hi[k] >= lim * 1.0005), None)
    if kf is None:
        return None
    e, fin, sin = lim, C.MAKER, C.SPREAD / 2
    pos, liqd = C.size(sym, margin, e)
    liqd += extra / pos
    at_risk = margin + extra
    cost = fin + C.FEE + sin
    pnl = lambda xp: max(pos * (-(xp / e - 1) - fin - C.FEE * xp / e - sin), -at_risk)   # noqa: E731
    tp = e * (1 - (target / pos + cost))
    sp, liq = e * (1 + CFG["stop"]), e * (1 + liqd)
    lock_px = e * (1 - (lock / pos + cost))
    be_px = e * (1 - cost)
    stage, low = 0, e                                  # 0 = original stop, 1 = breakeven, 2 = locked
    end = min(n - 1, kf + 1440)
    for k in range(kf + 1, end + 1):
        if hi[k] >= sp:
            xp = sp * 1.001 if lenient else max(sp, cl[k]) * 1.001
            how = "LIQ" if (xp >= liq and stage == 0) else ("STOP" if stage == 0 else ("BE" if stage == 1 else "LOCKED"))
            return how, (-at_risk if how == "LIQ" else pnl(xp)), kf, k
        if lo[k] <= tp * 0.999:
            return "TARGET", pos * (-(tp / e - 1) - fin - C.FEE * tp / e - sin), kf, k
        low = min(low, lo[k])
        if be is not None and stage == 0 and lo[k] <= e * (1 - be):
            stage, sp = 1, min(sp, be_px)
            if cl[k] >= sp:
                return "BE", pnl(cl[k] * 1.001), kf, k
        if stage < 2 and lo[k] <= e * 0.99:
            stage, sp = 2, min(sp, lock_px)
            if cl[k] >= sp:
                return "LOCKED", pnl(cl[k] * 1.001), kf, k
        if trail is not None and stage == 2:
            sp = min(sp, low * (1 + trail))
            if cl[k] >= sp:
                return "LOCKED", pnl(cl[k] * 1.001), kf, k
    return "24h", pnl(cl[end] * 1.001), kf, end


def account(paths, lenient, trail, be, log=False):
    bal, low_b, busy, per, stops = C.START, C.START, -1, None, 0
    rows: dict = {}
    counts = dict(TARGET=0, LOCKED=0, BE=0, STOP=0, LIQ=0, h24=0)
    by_day: dict = {}
    for p in paths:
        by_day.setdefault(p[0], []).append(p)
    for day in sorted(by_day):
        m = day.strftime("%Y-%m")
        if m != per:
            per, stops = m, 0
        row = rows.setdefault(m, dict(start=bal, trades=0, pnl=0.0, TARGET=0, LOCKED=0, BE=0, STOP=0, LIQ=0))
        for _, sym, hi, lo, cl, k0 in by_day[day]:
            if stops >= 2 or bal < 50:
                continue
            k = bal / C.START
            r = short(sym, hi, lo, cl, k0, CFG["margin"] * bal, CFG["target"] * k, CFG["lock"] * k, CFG["extra"] * bal,
                      lenient, trail, be)
            if r is None:
                continue
            t0 = day.value // 60_000_000_000
            if t0 + r[2] < busy:
                continue
            busy = t0 + r[3]
            bal = max(bal + r[1], 0.0)
            row["trades"] += 1
            row["pnl"] += r[1]
            key = "h24" if r[0] == "24h" else r[0]
            counts[key] += 1
            if key in row:
                row[key] += 1
            stops += r[0] in ("STOP", "LIQ")
        row["end"] = bal
        low_b = min(low_b, bal)
    return bal, low_b, rows, counts


VARIANTS = [("as built (lock at 1%, no trail)", None, None),
            ("A: trail 0.5% after the lock", 0.005, None),
            ("A: trail 0.8% after the lock", 0.008, None),
            ("A: trail 1.0% after the lock", 0.010, None),
            ("B: breakeven after 0.5%, lock at 1%", None, 0.005),
            ("C: breakeven 0.5% + trail 0.8%", 0.008, 0.005)]


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    for day, sym, hi, lo, cl, k0 in hist[:400]:
        for le in (False, True):
            a = short(sym, hi, lo, cl, k0, 1560.16, 500, 200, 796.0, le)
            b = C.short(sym, hi, lo, cl, k0, 1560.16, 500, "spike", 200, 0.0225, None, None, 796.0, lenient=le)
            assert (a is None and b is None) or (a[0] == b[0] and abs(a[1] - b[1]) < 0.01 and a[3] == b[3]), (sym, day, a, b)
    print("SELF-CHECK PASSED: with no trail and no early breakeven this simulator equals clean_compare.short trade by trade.\n")
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    print(f"RUN 4 (scaled, 2-stops/month limit, extra margin) from ${C.START:,.0f}; STRICT .. LENIENT\n")
    print(f"  {'variant':38s} | {'20 real picks':>17s} | {'2026 (to Nov 14)':>17s} | {'history end':>15s} | {'starts <$1k':>11s} | "
          f"{'med 12 mo':>15s} | {'2026 outcomes (strict): target / locked / breakeven / STOP':>55s}")
    for name, tr, be in VARIANTS:
        c = {}
        for le in (False, True):
            rb = account(real, le, tr, be)[0]
            yb, _, _, cnt = account(y26, le, tr, be)
            hb = account(hist, le, tr, be)[0]
            gone = 0
            b12 = []
            for m in pd.period_range("2025-02", "2026-10", freq="M"):
                s = m.to_timestamp()
                sub = [x for x in hist if x[0] >= s]
                gone += account(sub, le, tr, be)[1] < 1000
                if s + pd.Timedelta(days=365) <= hist[-1][0]:
                    b12.append(account([x for x in sub if x[0] < s + pd.Timedelta(days=365)], le, tr, be)[0])
            c[le] = (rb, yb, hb, gone, np.median(b12), cnt)
        s_, l_ = c[False], c[True]
        cnt = s_[5]
        print(f"  {name:38s} | {s_[0]:7,.0f} .. {l_[0]:7,.0f} | {s_[1]:7,.0f} .. {l_[1]:7,.0f} | {s_[2]:6,.0f} .. {l_[2]:6,.0f} | "
              f"{s_[3]:4d} .. {l_[3]:2d} | {s_[4]:6,.0f} .. {l_[4]:6,.0f} | {cnt['TARGET']:>10d} / {cnt['LOCKED']:6d} / {cnt['BE']:9d} / {cnt['STOP'] + cnt['LIQ']:4d}")
    best = [v for v in VARIANTS if v[0].startswith("C")][0]
    for le, nm in ((False, "STRICT"), (True, "LENIENT")):
        _, _, rows, _ = account(y26, le, best[1], best[2])
        print(f"\n  2026 month by month, {best[0]}, {nm}: " + "  ".join(f"{m[5:]} {r['pnl']:+,.0f}->{r['end']:,.0f}" for m, r in rows.items()))


if __name__ == "__main__":
    main()
