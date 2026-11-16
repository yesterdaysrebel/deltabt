"""Owner 2026-11-15: "How about with 50% margin" -- the one-big-trade rule (scripts/careful_big_one.py) at other sizes:
top pick only; MARGIN of the balance at up to 20x + EXTRA after the fill; stop 2.25%; take-profit at net +TARGET;
7 days off after a win; $1,000 floor. Per trade (fixed $3,184), as an account, and the one-time +$2,000 goal from all
615 start days (scripts/careful_goal_2000.py). Two implementations (careful_big_one.t1 / t2) checked on every trade.

    PYTHONPATH=. python scripts/careful_big_one_margin.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_big_one as BO  # noqa: E402
import careful_data as D  # noqa: E402
import careful_goal_2000 as G  # noqa: E402

CFGS = [(0.75, 0.20, 1000.0), (0.50, 0.25, 1000.0), (0.50, 0.25, 500.0)]


def main():
    days = D.load()
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    for mg, ex, tg in CFGS:
        BO.MARGIN, BO.EXTRA, BO.TARGET = mg, ex, tg
        for le in (False, True):
            for day, picks in days:
                p = picks[0]
                a, b = BO.t1(p[1], p[2], p[3], p[4], D.START, le), BO.t2(p[1], p[2], p[3], p[4], D.START, le)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:4] == b[2:4] and abs(a[1] - b[1]) < 0.01)), (mg, tg, day)
        p0 = days[0][1][0]
        n0 = BO.t1(p0[1], p0[2], p0[3], p0[4], D.START, False)[4]
        print(f"=== {mg:.0%} margin, +${tg:,.0f} target (position ~${n0:,.0f}; the target needs a {tg / n0 + 0.0012:.2%} fall, the stop is 2.25%) ===")
        for le in (False, True):
            rs = [BO.t1(p[0][1], p[0][2], p[0][3], p[0][4], D.START, le) for d, p in days]
            df = pd.DataFrame([(r[0], r[1]) for r in rs if r], columns=["how", "pnl"])
            c = df.how.value_counts()
            print(f"   per trade {'LENIENT' if le else 'STRICT '}: {len(df)} trades; target first {c.get('TARGET', 0) / len(df):.0%}, stop {c.get('STOP', 0) / len(df):.0%}, "
                  f"liquidated {c.get('LIQ', 0)}, 24 h {c.get('24h', 0)}; avg win ${df[df.how == 'TARGET'].pnl.mean():+,.0f}, "
                  f"avg stop ${df[df.how.isin(['STOP', 'LIQ'])].pnl.mean():+,.0f}; avg per trade ${df.pnl.mean():+,.0f}")
        cells = []
        for ps in (days, y26, real):
            a, b = BO.account(ps, False, False), BO.account(ps, True, False)
            cells.append(f"{(a[-1][4] if a else D.START):7,.0f} .. {(b[-1][4] if b else D.START):7,.0f}")
        print(f"   account from $3,184 (no month-off): full history {cells[0]} | 2026 from Jan 1 {cells[1]} | 20 real picks {cells[2]}")
        for le in (False, True):
            res = [G.run_75(days, i, le) for i in range(len(days))]
            g = [d for k, d in res if k == "GOAL"]
            f = [d for k, d in res if k == "FLOOR"]
            o = sum(k == "OPEN" for k, d in res)
            print(f"   one-time +$2,000 goal, {'LENIENT' if le else 'STRICT '}: reached first in {len(g)} of 615 starts ({len(g) / 615:.0%}), "
                  f"floor first in {len(f)} ({len(f) / 615:.0%}), neither yet {o}; median days to the goal {np.median(g) if g else float('nan'):.0f}, "
                  f"to the floor {np.median(f) if f else float('nan'):.0f}")
        print()


if __name__ == "__main__":
    main()
