"""Owner 2026-11-15: "Yes test it" -- run 4 as built, with the hard stop as a PAUSE instead of "off until the 1st".
Same trades (scripts/clean_compare.short, run 4's exact settings in scripts/run4_final_numbers.CFG), same account rules
(compounding, one position at a time); only the stop rule changes. Self-check first: this file's own account with the
monthly rule must equal clean_compare.account to the cent.

    PYTHONPATH=. python scripts/run4_pause_rules.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_final_numbers as F  # noqa: E402

CFG = F.CFG


def account(paths, lenient, rule):
    """rule: ("month", n) off until the 1st after n full stops in the month; ("pause", n, days) after n full stops
    (counted since the last pause ended) no entries for `days` days; ("window", n, days, pause) n full stops within a
    rolling `days` -> pause `pause` days; ("none",)."""
    bal, low, busy = C.START, C.START, -1
    per, stops, resume_at, recent = None, 0, None, []
    by_day: dict = {}
    for p in paths:
        by_day.setdefault(p[0], []).append(p)
    for day in sorted(by_day):
        if rule[0] == "month":
            m = day.strftime("%Y-%m")
            if m != per:
                per, stops = m, 0
            if stops >= rule[1]:
                continue
        elif rule[0] in ("pause", "window") and resume_at is not None:
            if day < resume_at:
                continue
            resume_at, stops, recent = None, 0, []
        if bal < 50:
            continue
        for _, sym, hi, lo, cl, k0 in by_day[day]:
            if rule[0] == "month" and stops >= rule[1]:
                break
            if resume_at is not None:
                break
            r = C.short(sym, hi, lo, cl, k0, CFG["margin"] * bal, CFG["target"], "spike", CFG["lock"], CFG["stop"],
                        None, None, CFG["extra"] * bal, lenient=lenient)
            if r is None:
                continue
            t0 = day.value // 60_000_000_000
            if t0 + r[2] < busy:
                continue
            busy = t0 + r[3]
            bal = max(bal + r[1], 0.0)
            full = r[0] in ("STOP", "LIQ")
            if rule[0] == "month":
                stops += full
            elif rule[0] == "pause" and full:
                stops += 1
                if stops >= rule[1]:
                    resume_at = day + pd.Timedelta(days=rule[2])
            elif rule[0] == "window" and full:
                recent = [d for d in recent if d > day - pd.Timedelta(days=rule[2])] + [day]
                if len(recent) >= rule[1]:
                    resume_at = day + pd.Timedelta(days=rule[3])
        low = min(low, bal)
    return bal, low


RULES = [("now: off until the 1st after 2 stops", ("month", 2)),
         ("pause 7 days after 2 stops", ("pause", 2, 7)),
         ("pause 14 days after 2 stops", ("pause", 2, 14)),
         ("pause 3 days after 2 stops", ("pause", 2, 3)),
         ("pause 7 days after 3 stops", ("pause", 3, 7)),
         ("2 stops within 7 days -> pause 7 days", ("window", 2, 7, 7)),
         ("no hard stop", ("none",))]


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    for paths in (real, hist):
        for le in (False, True):
            a = account(paths, le, ("month", 2))
            b = F.account(paths, le, 2)
            assert abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01, (a, b)
            a = account(paths, le, ("none",))
            b = F.account(paths, le, None)
            assert abs(a[0] - b[0]) < 0.01, (a, b)
    print("SELF-CHECK PASSED: this account reproduces clean_compare.account to the cent (monthly rule and no rule).\n")
    print(f"RUN 4 AS BUILT from ${C.START:,.0f}; only the hard-stop rule changes. STRICT .. LENIENT stop fills.\n")
    print(f"  {'rule':40s} | {'20 real picks':>17s} | {'history end':>15s} | {'21 starts: <$1,000':>18s} {'ahead 3 mo':>11s} "
          f"{'median 3 mo':>15s} {'median 12 mo':>15s} {'median low':>15s}")
    for name, rule in RULES:
        cells = {}
        for le in (False, True):
            rb = account(real, le, rule)[0]
            hb = account(hist, le, rule)[0]
            gone = up3 = 0
            b3, b12, lows = [], [], []
            for m in pd.period_range("2025-02", "2026-10", freq="M"):
                s = m.to_timestamp()
                sub = [x for x in hist if x[0] >= s]
                e, lo = account(sub, le, rule)
                gone += lo < 1000
                lows.append(lo)
                x3 = account([x for x in sub if x[0] < s + pd.Timedelta(days=90)], le, rule)[0]
                up3 += x3 > C.START
                b3.append(x3)
                if s + pd.Timedelta(days=365) <= hist[-1][0]:
                    b12.append(account([x for x in sub if x[0] < s + pd.Timedelta(days=365)], le, rule)[0])
            cells[le] = (rb, hb, gone, up3, np.median(b3), np.median(b12), np.median(lows))
        s_, l_ = cells[False], cells[True]
        print(f"  {name:40s} | {s_[0]:7,.0f} .. {l_[0]:7,.0f} | {s_[1]:6,.0f} .. {l_[1]:6,.0f} | {s_[2]:7d} .. {l_[2]:2d}/21 "
              f"{s_[3]:4d} .. {l_[3]:2d} | {s_[4]:6,.0f} .. {l_[4]:6,.0f} | {s_[5]:6,.0f} .. {l_[5]:6,.0f} | {s_[6]:6,.0f} .. {l_[6]:6,.0f}")


if __name__ == "__main__":
    main()
