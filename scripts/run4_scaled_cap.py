"""Owner 2026-11-15: "Lets test it" (target and lock that scale with the balance) and "you also did not test stop
trading when 2000$ profit reached". Run 4 as built (scripts/run4_pause_rules.CFG) with two optional changes:
  SCALED   target and lock dollars x balance / $3,184 (= the same price fall at any balance: ~1.8% / ~0.8%)
  CAP      off for the rest of the month once the month's P&L reaches +$2,000 (on top of the 2-stops rule)
Self-check first: fixed $ and no cap must equal run4_pause_rules.account (itself checked against clean_compare).

    PYTHONPATH=. python scripts/run4_scaled_cap.py
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


def run(paths, lenient, scaled, cap, log=False):
    bal, low, busy, per, stops, m_start = C.START, C.START, -1, None, 0, C.START
    rows: dict = {}
    by_day: dict = {}
    for p in paths:
        by_day.setdefault(p[0], []).append(p)
    for day in sorted(by_day):
        m = day.strftime("%Y-%m")
        if m != per:
            per, stops, m_start = m, 0, bal
        row = rows.setdefault(m, dict(start=bal, trades=0, TARGET=0, LOCKED=0, STOP=0, LIQ=0, h24=0, off=0, pnl=0.0, why=""))
        for _, sym, hi, lo, cl, k0 in by_day[day]:
            capped = cap is not None and bal - m_start >= cap
            if stops >= 2 or capped:
                row["off"] += 1
                row["why"] = row["why"] or ("2 stops" if stops >= 2 else f"+${cap:,.0f} reached")
                continue
            if bal < 50:
                continue
            k = bal / C.START if scaled else 1.0
            r = C.short(sym, hi, lo, cl, k0, CFG["margin"] * bal, CFG["target"] * k, "spike", CFG["lock"] * k, CFG["stop"],
                        None, None, CFG["extra"] * bal, lenient=lenient)
            if r is None:
                continue
            t0 = day.value // 60_000_000_000
            if t0 + r[2] < busy:
                continue
            busy = t0 + r[3]
            bal = max(bal + r[1], 0.0)
            row["trades"] += 1
            row["pnl"] += r[1]
            row["h24" if r[0] == "24h" else r[0]] += 1
            stops += r[0] in ("STOP", "LIQ")
        row["end"] = bal
        low = min(low, bal)
    return bal, low, rows


VARIANTS = [("as built: fixed $500 / $200, no cap", False, None),
            ("SCALED target and lock, no cap", True, None),
            ("fixed $, + stop at +$2,000 a month", False, 2000.0),
            ("SCALED + stop at +$2,000 a month", True, 2000.0)]


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    for paths in (y26, hist):
        for le in (False, True):
            a = run(paths, le, False, None)
            b = P.account(paths, le, ("month", 2))
            assert abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01, (a[:2], b)
    print("SELF-CHECK PASSED: the as-built variant equals run4_pause_rules.account to the cent.\n")
    print(f"A) 2026 MONTH BY MONTH from ${C.START:,.0f} (history to Oct 27, then the real picks Oct 29 - Nov 14). End balance per month:\n")
    for le, name in ((False, "STRICT"), (True, "LENIENT")):
        print(f"  {name}")
        res = {v[0]: run(y26, le, v[1], v[2]) for v in VARIANTS}
        months = list(res[VARIANTS[0][0]][2])
        print(f"  {'month':8s} " + " ".join(f"{v[0][:34]:>36s}" for v in VARIANTS))
        for m in months:
            cells = []
            for v in VARIANTS:
                r = res[v[0]][2].get(m)
                cells.append(f"{r['pnl']:+7,.0f} -> {r['end']:6,.0f} ({r['TARGET']}T {r['LOCKED']}L {r['STOP']}S)".rjust(36) if r else " " * 36)
            print(f"  {m:8s} " + " ".join(cells))
        print(f"  {'END':8s} " + " ".join(f"{res[v[0]][0]:36,.0f}" for v in VARIANTS))
        print(f"  {'green':8s} " + " ".join(f"{sum(r['pnl'] > 0 for r in res[v[0]][2].values()):>33d}/{len(months):<2d}" for v in VARIANTS))
        print()
    print("B) FULL HISTORY 2025-01..2026-10 and 21 MONTHLY STARTS (STRICT .. LENIENT)")
    print(f"  {'variant':38s} | {'history end':>15s} | {'starts under $1,000':>19s} | {'ahead after 3 mo':>16s} | {'median after 12 mo':>18s}")
    for name, sc, cap in VARIANTS:
        cells = {}
        for le in (False, True):
            hb = run(hist, le, sc, cap)[0]
            gone = up3 = 0
            b12 = []
            for m in pd.period_range("2025-02", "2026-10", freq="M"):
                s = m.to_timestamp()
                sub = [x for x in hist if x[0] >= s]
                gone += run(sub, le, sc, cap)[1] < 1000
                up3 += run([x for x in sub if x[0] < s + pd.Timedelta(days=90)], le, sc, cap)[0] > C.START
                if s + pd.Timedelta(days=365) <= hist[-1][0]:
                    b12.append(run([x for x in sub if x[0] < s + pd.Timedelta(days=365)], le, sc, cap)[0])
            cells[le] = (hb, gone, up3, np.median(b12))
        s_, l_ = cells[False], cells[True]
        print(f"  {name:38s} | {s_[0]:6,.0f} .. {l_[0]:6,.0f} | {s_[1]:8d} .. {l_[1]:2d}/21 | {s_[2]:6d} .. {l_[2]:2d}/21 | {s_[3]:7,.0f} .. {l_[3]:7,.0f}")


if __name__ == "__main__":
    main()
