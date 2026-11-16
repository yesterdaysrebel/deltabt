"""Owner 2026-11-15: "With 50% margin how often do we reach 1000 profit". The 50% rule (careful_big_one: top pick,
50% margin + 25% extra, stop 2.25%, take-profit at net +$1,000, 7 days off after a win, month off after 2 stops,
$1,000 floor) run from each of the 615 pick days with $3,184 and NOT stopped at the first win: how many +$1,000 wins
before the account hits the floor. Computed with both trade implementations (t1, t2) and asserted equal.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/careful_repeat_50.py
"""
import sys
from pathlib import Path
from collections import Counter
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_one_pct  # noqa: E402,F401  (loads Delta's live terms into D.PRODUCTS)
import careful_big_one as BO  # noqa: E402

BO.MARGIN, BO.EXTRA, BO.TARGET = 0.50, 0.25, 1000.0


def main():
    days = D.load()
    n = len(days)
    print(f"50% margin, +$1,000 take-profit, kept running from each of {n} start days with $3,184:\n")
    for le in (False, True):
        wins, trades, dead, life, ends, first_win = [], [], 0, [], [], 0
        for i in range(n):
            a = BO.account(days[i:], le, True, BO.t1)
            b = BO.account(days[i:], le, True, BO.t2)
            assert len(a) == len(b) and all(x[:3] == y[:3] and abs(x[3] - y[3]) < 0.01 for x, y in zip(a, b)), i
            w = sum(r[2] == "TARGET" for r in a)
            wins.append(w)
            trades.append(len(a))
            first_win += bool(a) and a[0][2] == "TARGET"
            end = a[-1][4] if a else D.START
            ends.append(end)
            if end < 1000.0:
                dead += 1
                life.append((a[-1][0] - days[i][0].date()).days)
        c = Counter(wins)
        print(f"  {'LENIENT' if le else 'STRICT '}: account finished (under $1,000) in {dead} of {n} starts ({dead / n:.0%}), "
              f"median {np.median(life):.0f} days after the start")
        print(f"           first trade is a +$1,000 win: {first_win / n:.0%}")
        print("           +$1,000 wins before the end: " + ", ".join(f"{k}: {c[k] / n:.0%}" for k in sorted(c)))
        print(f"           average wins {np.mean(wins):.2f} in {np.mean(trades):.1f} trades; "
              f"average end balance ${np.mean(ends):,.0f} (start $3,184)\n")
    print("CHECK PASSED: t1 and t2 agree on every trade of every run.")


if __name__ == "__main__":
    main()
