"""Owner 2026-11-16: "How about with 75% margin make 100 a trade, when we reach 500 stop the bot".
careful_big_one's trade at 75% margin + 20% extra, take-profit at net +$100, stop 2.25%, 24 h, top pick only, no pause
after a win. From each of the 615 pick days with $3,184: trade until the account is up +$500 (bot stops for good) or
under the $1,000 floor (variant: quit when down $500). Month off after 2 full stops. Both trade implementations
(t1, t2) run every account and are asserted equal.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/careful_small_75.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_one_pct  # noqa: E402,F401  (loads Delta's live terms into D.PRODUCTS)
import careful_big_one as BO  # noqa: E402

BO.MARGIN, BO.EXTRA, BO.TARGET = 0.75, 0.20, 100.0
GOAL = 500.0


def run(days, i0, le, fn, quit_at):
    wal, busy, month, stops, out = D.START, -1, None, 0, []
    for day, picks in days[i0:]:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        if stops >= 2 or busy > t0 + 2:
            continue
        q = picks[0]
        r = fn(q[1], q[2], q[3], q[4], wal, le)
        if r is None:
            continue
        wal = max(wal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        out.append((r[0], round(r[1], 2)))
        days_in = (day - days[i0][0]).days
        if wal >= D.START + GOAL:
            return "GOAL", days_in, wal, out
        if wal < 1000.0 or wal <= quit_at:
            return "LOST", days_in, wal, out
    return "OPEN", None, wal, out


def main():
    days = D.load()
    n = len(days)
    p0 = days[0][1][0]
    n0 = BO.t1(p0[1], p0[2], p0[3], p0[4], D.START, False)[4]
    print(f"75% margin (position ~${n0:,.0f}), take-profit at net +$100 (a {100 / n0 + 0.0012:.2%} fall), stop 2.25%; "
          f"bot stops for good at +$500.\n")
    for le in (False, True):
        rs = [BO.t1(p[0][1], p[0][2], p[0][3], p[0][4], D.START, le) for d, p in days]
        df = pd.DataFrame([(r[0], r[1]) for r in rs if r], columns=["how", "pnl"])
        c = df.how.value_counts()
        w, lo = df[df.how == "TARGET"].pnl.mean(), df[df.how.isin(["STOP", "LIQ"])].pnl.mean()
        print(f"PER TRADE at $3,184, {'LENIENT' if le else 'STRICT '}: {len(df)} trades; +$100 first {c.get('TARGET', 0) / len(df):.0%}, "
              f"stopped {(c.get('STOP', 0) + c.get('LIQ', 0)) / len(df):.0%} (liquidated {c.get('LIQ', 0)}); avg win ${w:+,.0f}, "
              f"avg stop ${lo:+,.0f}; break-even needs {-lo / (w - lo):.0%} wins; avg per trade ${df.pnl.mean():+,.0f}")
    print()
    for label, quit_at in (("ends at the bot's $1,000 floor", 0.0), ("quit when down $500", D.START - GOAL)):
        print(f"ONE-TIME +$500 from each of {n} start days, {label}:")
        for le in (False, True):
            res = []
            for i in range(n):
                a, b = run(days, i, le, BO.t1, quit_at), run(days, i, le, BO.t2, quit_at)
                assert a[0] == b[0] and a[1] == b[1] and len(a[3]) == len(b[3]) and all(
                    x[0] == y[0] and abs(x[1] - y[1]) < 0.01 for x, y in zip(a[3], b[3])), (i, le)
                res.append(a)
            g = [r for r in res if r[0] == "GOAL"]
            lst = [r for r in res if r[0] == "LOST"]
            avg = np.mean([r[2] - D.START for r in res])
            print(f"   {'LENIENT' if le else 'STRICT '}: +$500 first {len(g) / n:.0%} (median {np.median([r[1] for r in g]):.0f} days, "
                  f"{np.median([len(r[3]) for r in g]):.0f} trades) | lost first {len(lst) / n:.0%} "
                  f"(avg loss ${np.mean([r[2] - D.START for r in lst]) if lst else 0:+,.0f}) | not decided {(n - len(g) - len(lst)) / n:.0%} "
                  f"| avg result per start ${avg:+,.0f}")
        print()
    print("CHECK PASSED: t1 and t2 agree on every trade of every run.")


if __name__ == "__main__":
    main()
