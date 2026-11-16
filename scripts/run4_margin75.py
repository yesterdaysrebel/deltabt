"""Owner 2026-11-15: "Test same rules with 75% margin. My idea is entry right and exit quick hitting target". The final
run-4 rules (spike +5%, 20x, +25% extra margin at the fill, stop +2.25% last-traded, target +$500 and lock +$200 scaled
by balance/$3,184, trail 0.8% after the lock, 24 h, off for the month after 2 full stops, one position at a time) with the
trading margin at 75%. Two resting orders at 75% need 150% of the wallet, so Delta would refuse the second:
  (a) 75%, first pick only        -- placeable
  (b) 75%, both picks resting     -- NOT placeable on Delta; reference only
  (c) 49%, both picks (as built)  -- reference
Fill model and checks: scripts/clean_compare.py; trade function: scripts/run4_trailing.short (verified 604/604 by
scripts/verify_independent.py --final; the 75% case is cross-checked below too).

    PYTHONPATH=. python scripts/run4_margin75.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_trailing as T  # noqa: E402
import verify_independent as V  # noqa: E402

TRAIL, TARGET, LOCK, EXTRA = 0.008, 500.0, 200.0, 0.25
RANK: dict = {}


def load_ranks():
    """(day, symbol) -> rank (1 = the day's top gainer). History: by the previous-day change in the pick table
    (scripts/fade_ladder_5x.trades); real picks: the paper runner's own ranks (out/real_pick_ranks.json, from the bot DB).
    FIX 2026-11-15: the first version took the alphabetically first coin, not rank 1."""
    import json
    import fade_ladder_5x as L5
    d = L5.trades()
    for day, g in d.groupby("date"):
        for k, (_, x) in enumerate(g.sort_values("prev_change", ascending=False).iterrows()):
            RANK[(pd.Timestamp(day), x.symbol)] = k + 1
    for day, sym, rank in json.load(open("out/real_pick_ranks.json")):
        RANK[(pd.Timestamp(day), sym)] = rank


def account(paths, lenient, margin, first_only, log=False):
    bal, low, busy, per, stops = C.START, C.START, -1, None, 0
    rows: dict = {}
    by_day: dict = {}
    for p in paths:
        by_day.setdefault(p[0], []).append(p)
    for day in sorted(by_day):
        m = day.strftime("%Y-%m")
        if m != per:
            per, stops = m, 0
        row = rows.setdefault(m, dict(start=bal, trades=0, pnl=0.0, TARGET=0, LOCKED=0, STOP=0, LIQ=0, minutes=[]))
        picks = sorted(by_day[day], key=lambda x: RANK.get((pd.Timestamp(x[0]), x[1]), 9))
        if first_only:
            picks = picks[:1]                            # the best-ranked eligible pick: the one resting order
        for _, sym, hi, lo, cl, k0 in picks:
            if stops >= 2 or bal < 50:
                continue
            k = bal / C.START
            r = T.short(sym, hi, lo, cl, k0, margin * bal, TARGET * k, LOCK * k, EXTRA * bal, lenient, TRAIL, None)
            if r is None:
                continue
            t0 = day.value // 60_000_000_000
            if t0 + r[2] < busy:
                continue
            busy = t0 + r[3]
            bal = max(bal + r[1], 0.0)
            row["trades"] += 1
            row["pnl"] += r[1]
            if r[0] in row:
                row[r[0]] += 1
            if r[0] == "TARGET":
                row["minutes"].append(r[3] - r[2])
            stops += r[0] in ("STOP", "LIQ")
        row["end"] = bal
        low = min(low, bal)
    return bal, low, rows


VARIANTS = [("(a) 75%, first pick only (placeable)", 0.75, True),
            ("(b) 75%, both picks (NOT placeable)", 0.75, False),
            ("(c) 49%, both picks (as built)", 0.49, False)]


def main() -> None:
    C.checks()
    load_ranks()
    hist, *_ = C.paths_history()
    missing = [x for x in hist + C.paths_real() if (pd.Timestamp(x[0]), x[1]) not in RANK]
    assert not missing, missing[:5]
    print(f"RANKS LOADED for every pick ({len(RANK)}).")
    real = sorted(C.paths_real(), key=lambda x: x[0])
    # cross-check the 75% trade function against the independent re-implementation (fixed $3,184 per trade)
    same = 0
    for day, sym, hi, lo, cl, k0 in hist + real:
        a = T.short(sym, hi, lo, cl, k0, 0.75 * C.START, TARGET, LOCK, EXTRA * C.START, False, TRAIL, None)
        b = V.trade(sym, hi, lo, cl, k0, 0.75 * C.START, EXTRA * C.START, TARGET, LOCK, TRAIL)
        assert (a is None and b is None) or (a[0] == b[0] and a[3] == b[3] and abs(a[1] - b[1]) < 0.05), (sym, day, a, b)
        same += a is not None
    print(f"CROSS-CHECK PASSED: 75% margin, {same} trades identical in both simulators.\n")
    pos = {m: C.size("XUSD", m * C.START, 1.0)[0] for m in (0.49, 0.75)}
    for m, p in pos.items():
        print(f"  {int(100 * m)}% margin on $3,184: position ${p:,.0f}; +$500 target = a {100 * (TARGET / p + C.MAKER + C.FEE + C.SPREAD / 2):.2f}% fall; "
              f"a full stop costs about ${p * (0.0225 + 0.0017):,.0f}")
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    print(f"\nFrom ${C.START:,.0f}; STRICT .. LENIENT stop fills\n")
    print(f"  {'variant':38s} | {'20 real picks':>17s} | {'2026 to Nov 14':>17s} | {'history end':>15s} | {'starts <$1k':>11s} | {'median 12 mo':>15s} | {'median minutes to target (2026)':>32s}")
    for name, mg, first in VARIANTS:
        c = {}
        for le in (False, True):
            rb = account(real, le, mg, first)[0]
            yb, _, rows = account(y26, le, mg, first)
            hb = account(hist, le, mg, first)[0]
            gone = 0
            b12 = []
            for mo in pd.period_range("2025-02", "2026-10", freq="M"):
                s = mo.to_timestamp()
                sub = [x for x in hist if x[0] >= s]
                gone += account(sub, le, mg, first)[1] < 1000
                if s + pd.Timedelta(days=365) <= hist[-1][0]:
                    b12.append(account([x for x in sub if x[0] < s + pd.Timedelta(days=365)], le, mg, first)[0])
            mins = [x for r in rows.values() for x in r["minutes"]]
            c[le] = (rb, yb, hb, gone, np.median(b12), np.median(mins) if mins else float("nan"))
        s_, l_ = c[False], c[True]
        print(f"  {name:38s} | {s_[0]:7,.0f} .. {l_[0]:7,.0f} | {s_[1]:7,.0f} .. {l_[1]:7,.0f} | {s_[2]:6,.0f} .. {l_[2]:6,.0f} | "
              f"{s_[3]:4d} .. {l_[3]:2d} | {s_[4]:6,.0f} .. {l_[4]:6,.0f} | {s_[5]:13.0f} .. {l_[5]:.0f}")
    print("\n2026 MONTH BY MONTH, (a) 75% first pick only vs (c) 49% as built  [P&L -> end, targets/locked/stops]")
    for le, nm in ((False, "STRICT"), (True, "LENIENT")):
        a = account(y26, le, 0.75, True)[2]
        cc = account(y26, le, 0.49, False)[2]
        print(f"  {nm}")
        for mo in a:
            x, y = a[mo], cc[mo]
            print(f"    {mo}  (a) {x['pnl']:+7,.0f} -> {x['end']:6,.0f} ({x['TARGET']}T {x['LOCKED']}L {x['STOP'] + x['LIQ']}S)"
                  f"    (c) {y['pnl']:+7,.0f} -> {y['end']:6,.0f} ({y['TARGET']}T {y['LOCKED']}L {y['STOP'] + y['LIQ']}S)")


if __name__ == "__main__":
    main()
