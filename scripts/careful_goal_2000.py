"""Owner 2026-11-15: "we just need 2000 as profit". Start the 75% margin / +$1,000 rule (scripts/careful_big_one.py:
top pick only, stop 2.25%, 7 days off after a win) on a given day with $3,184 and run it until EITHER the account is
up +$2,000 (then stop for good) OR it falls under the $1,000 floor (the bot stops) -- from EVERY pick day of the
history as a start. How often is the goal reached first, and how long does it take? Same for exit C at 10% margin
(scripts/careful_be_trail.t1 scaled to 10%) for comparison. Trade functions are the two-way-checked ones.

    PYTHONPATH=. python scripts/careful_goal_2000.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_big_one as BO  # noqa: E402
import careful_data as D  # noqa: E402
import replay_bot as R  # noqa: E402

GOAL, FLOOR = 2000.0, 1000.0


def run_75(days, i0, le):
    bal, busy, until = D.START, -1, None
    for day, picks in days[i0:]:
        t0 = int(day.timestamp()) // 60
        if busy > t0 + 2 or (until is not None and day < until):
            continue
        r = BO.t1(picks[0][1], picks[0][2], picks[0][3], picks[0][4], bal, le)
        if r is None:
            continue
        bal = max(bal + r[1], 0.0)
        busy = t0 + r[3]
        if r[0] == "TARGET":
            until = day + pd.Timedelta(days=8)
        if bal >= D.START + GOAL:
            return "GOAL", (day - days[i0][0]).days
        if bal < FLOOR:
            return "FLOOR", (day - days[i0][0]).days
    return "OPEN", None


def run_c(days, i0, le):
    bal, busy, month, stops = D.START, -1, None, 0
    for day, picks in days[i0:]:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        if stops >= 2 or busy > t0 + 2:
            continue
        best = None
        for rank, sym, H, L, Cl in picks:
            r = R.ref_trade(sym, H, L, Cl, bal, le)
            if r and (best is None or r[2] < best[2]):
                best = r
        if best is None:
            continue
        bal = max(bal + best[1], 0.0)
        busy = t0 + best[3]
        stops += best[0] in ("STOP", "LIQ")
        if bal >= D.START + GOAL:
            return "GOAL", (day - days[i0][0]).days
        if bal < FLOOR:
            return "FLOOR", (day - days[i0][0]).days
    return "OPEN", None


def main():
    days = D.load()
    starts = range(0, len(days))
    print(f"From $3,184 on each of {len(days)} pick days ({days[0][0].date()} .. {days[-1][0].date()}): run until +$2,000 "
          f"(stop for good) or under $1,000 (the bot stops).\n")
    for nm, fn in (("75% margin, +$1,000 target, week off after a win", run_75), ("exit C at 10% margin (as approved)", run_c)):
        for le in (False, True):
            res = [fn(days, i, le) for i in starts]
            g = [d for k, d in res if k == "GOAL"]
            f = [d for k, d in res if k == "FLOOR"]
            o = sum(k == "OPEN" for k, d in res)
            done = len(g) + len(f)
            print(f"{nm}, {'LENIENT' if le else 'STRICT '}: reached +$2,000 first in {len(g)} starts ({len(g) / done:.0%} of those that ended), "
                  f"hit the floor first in {len(f)} ({len(f) / done:.0%}); still neither by Nov 14: {o}")
            if g:
                print(f"     days to +$2,000: median {np.median(g):.0f}, fastest {min(g)}, slowest {max(g)}")
            if f:
                print(f"     days to the floor: median {np.median(f):.0f}, fastest {min(f)}")
        print()


if __name__ == "__main__":
    main()
