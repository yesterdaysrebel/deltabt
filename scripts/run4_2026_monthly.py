"""Owner 2026-11-15: "run simulator with all the selected rules in year 2026 every month". Run 4 as built (live/spike_live.py:
spike +5%, 49% margin at 20x, 25% extra margin from the fill, $500 target, stop +2.25% last-traded, lock +$200 after a
1% fall, 24 h, off until the 1st after 2 full stops, one position at a time), from $3,184 on 2026-01-01. Delta history
picks to 2026-10-27, then the real picks 2026-10-29..11-14. Fill rules and checks of scripts/clean_compare.py; STRICT and
LENIENT. The account logic is scripts/run4_pause_rules.account (self-checked against clean_compare.account).

    PYTHONPATH=. python scripts/run4_2026_monthly.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_pause_rules as P  # noqa: E402

CFG = P.CFG


def monthly(paths, lenient):
    """Month rows: start, trades, outcomes, skipped by the hard stop, P&L, end (same rules as P.account, logged)."""
    bal, busy, per, stops = C.START, -1, None, 0
    out: dict = {}
    by_day: dict = {}
    for p in paths:
        by_day.setdefault(p[0], []).append(p)
    for day in sorted(by_day):
        m = day.strftime("%Y-%m")
        row = out.setdefault(m, dict(start=bal, trades=0, TARGET=0, LOCKED=0, STOP=0, LIQ=0, h24=0, nofill=0, off=0, pnl=0.0))
        if m != per:
            per, stops = m, 0
        for _, sym, hi, lo, cl, k0 in by_day[day]:
            if stops >= 2:
                row["off"] += 1
                continue
            if bal < 50:
                continue
            r = C.short(sym, hi, lo, cl, k0, CFG["margin"] * bal, CFG["target"], "spike", CFG["lock"], CFG["stop"],
                        None, None, CFG["extra"] * bal, lenient=lenient)
            if r is None:
                row["nofill"] += 1
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
    return out


def main() -> None:
    C.checks()
    hist, *_ = C.paths_history()
    real = C.paths_real()
    paths = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    for le in (False, True):
        a = P.account(paths, le, ("month", 2))[0]
        rows = monthly(paths, le)
        assert abs(list(rows.values())[-1]["end"] - a) < 0.01, (a, list(rows.values())[-1]["end"])
    print("SELF-CHECK PASSED: the monthly log ends where run4_pause_rules.account ends, to the cent.\n")
    print(f"RUN 4 AS BUILT, 2026 month by month from ${C.START:,.0f} (history to Oct 27, then the real picks Oct 29 - Nov 14)\n")
    for le, name in ((False, "STRICT stop fills (worse of stop and minute close)"), (True, "LENIENT stop fills (at the stop price)")):
        rows = monthly(paths, le)
        print(name)
        print(f"  {'month':8s} {'start':>7s} {'trades':>6s} {'target':>6s} {'locked':>6s} {'stop':>4s} {'liq':>3s} {'24h':>3s} "
              f"{'no fill':>7s} {'off (hard stop)':>15s} {'P&L':>9s} {'end':>7s} {'month':>7s}")
        for m, r in rows.items():
            print(f"  {m:8s} {r['start']:7,.0f} {r['trades']:6d} {r['TARGET']:6d} {r['LOCKED']:6d} {r['STOP']:4d} {r['LIQ']:3d} {r['h24']:3d} "
                  f"{r['nofill']:7d} {r['off']:15d} {r['pnl']:+9,.0f} {r['end']:7,.0f} {100 * r['pnl'] / r['start']:+6.1f}%")
        tot = {k: sum(r[k] for r in rows.values()) for k in ("trades", "TARGET", "LOCKED", "STOP", "LIQ", "pnl")}
        last = list(rows.values())[-1]["end"]
        green = sum(r["pnl"] > 0 for r in rows.values())
        print(f"  2026 so far: {tot['trades']} trades ({tot['TARGET']} targets, {tot['LOCKED']} locked, {tot['STOP']} stops, {tot['LIQ']} liquidated), "
              f"P&L ${tot['pnl']:+,.0f}: ${C.START:,.0f} -> ${last:,.0f}; green months {green}/{len(rows)}\n")


if __name__ == "__main__":
    main()
