"""Owner 2026-11-16: "we just need 1 trade per month". careful_big_one's trade (top pick, resting sell 5% above the
00:02 price, stop 2.25%, take-profit at net +$500, 24 h): the FIRST fill of each calendar month only, win or lose, then
nothing until next month. $1,000 floor. Margins 75 / 50 / 10%. The real 23 months (t1 and t2 asserted equal), plus
5,000 resampled 12-month years (each month = a randomly chosen history month, its first fill, sized
at the current balance).

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/careful_one_a_month.py
"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_one_pct  # noqa: E402,F401  (loads Delta's live terms into D.PRODUCTS)
import careful_big_one as BO  # noqa: E402

CFGS = [(0.75, 0.20), (0.50, 0.25), (0.10, 0.051)]


def first_fills(days):
    """Per month, the pick days in order (the bot takes the first top pick that fills)."""
    by = {}
    for d, p in days:
        by.setdefault(d.strftime("%Y-%m"), []).append((d, p[0]))
    return by


def month_trade(entries, bal, le, fn):
    for d, q in entries:
        r = fn(q[1], q[2], q[3], q[4], bal, le)
        if r is not None:
            return d, q[1], r
    return None


def main():
    days = D.load()
    by = first_fills(days)
    months = sorted(by)
    for mg, ex in CFGS:
        BO.MARGIN, BO.EXTRA, BO.TARGET = mg, ex, 500.0
        print(f"=== {mg:.0%} margin, one trade a month, +$500 target, stop 2.25% ===")
        for le in (False, True):
            bal, lines, wins, losses = D.START, [], 0, 0
            for m in months:
                if bal < 1000.0:
                    lines.append(f"{m} under $1,000 - stopped")
                    break
                a, b = month_trade(by[m], bal, le, BO.t1), month_trade(by[m], bal, le, BO.t2)
                assert (a is None) == (b is None) and (a is None or (a[2][0] == b[2][0] and abs(a[2][1] - b[2][1]) < 0.01)), (mg, le, m)
                if a is None:
                    lines.append(f"{m} no fill")
                    continue
                d, sym, r = a
                bal = max(bal + r[1], 0.0)
                wins += r[0] == "TARGET"
                losses += r[0] in ("STOP", "LIQ")
                lines.append(f"{m} {d:%d} {sym:<11} {r[0]:<6} {r[1]:+7,.0f} -> {bal:7,.0f}")
            print(f"  {'LENIENT' if le else 'STRICT '}: {wins} wins, {losses} losses; end ${bal:,.0f}")
            if mg == 0.75 and not le:
                print("     " + "\n     ".join(lines))
        # resampled years: each month's trade = a random month's first-fill trade of the history, at the current balance
        rng = np.random.default_rng(7)
        for le in (False, True):
            ends = []
            for _ in range(5000):
                bal = D.START
                for _ in range(12):
                    if bal < 1000.0:
                        break
                    r = month_trade(by[months[rng.integers(len(months))]], bal, le, BO.t1)
                    if r:
                        bal = max(bal + r[2][1], 0.0)
                ends.append(bal)
            ends = np.array(ends)
            print(f"  5,000 random 12-month years, {'LENIENT' if le else 'STRICT '}: up after a year {np.mean(ends > D.START):.0%}; "
                  f"under $1,000 {np.mean(ends < 1000):.0%}; median end ${np.median(ends):,.0f}; average end ${ends.mean():,.0f}; "
                  f"made $6,000 (=$500 x 12) {np.mean(ends >= D.START + 6000 - 1):.1%}")
        print()
    print("CHECK PASSED: t1 and t2 agree on every month of every real account.")


if __name__ == "__main__":
    main()
