"""Owner 2026-11-16: "How about one trade of 500 and we stop for the month". careful_big_one's trade (top pick, resting
sell 5% above the 00:02 price, MARGIN of the balance at up to 20x + extra, stop 2.25%, 24 h) with a take-profit at net
+$500; after a take-profit no more trades that calendar month. Month off after 2 full stops, $1,000 floor.
Margins 10 / 25 / 50 / 75%. Full history and the 22 monthly starts; t1 and t2 asserted equal on every account.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/careful_500_month.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_one_pct  # noqa: E402,F401  (loads Delta's live terms into D.PRODUCTS)
import careful_big_one as BO  # noqa: E402

CFGS = [(0.10, 0.051), (0.25, 0.25 * 25 / 49), (0.50, 0.25), (0.75, 0.20)]


def account(days, le, fn):
    bal, busy, month, stops, won, out = D.START, -1, None, 0, False, []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops, won = m, 0, False
        if bal < 1000.0 or won or stops >= 2 or busy > t0 + 2:
            continue
        q = picks[0]
        r = fn(q[1], q[2], q[3], q[4], bal, le)
        if r is None:
            continue
        bal = max(bal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        won = r[0] == "TARGET"
        out.append((m, r[0], r[1], bal))
    return out


def main():
    days = D.load()
    starts = sorted({d.strftime("%Y-%m") for d, _ in days})
    for mg, ex in CFGS:
        BO.MARGIN, BO.EXTRA, BO.TARGET = mg, ex, 500.0
        p0 = days[0][1][0]
        n0 = BO.t1(p0[1], p0[2], p0[3], p0[4], D.START, False)[4]
        print(f"=== {mg:.0%} margin (position ~${n0:,.0f}): +$500 needs a {500 / n0 + 0.0012:.2%} fall, stop 2.25% ===")
        for le in (False, True):
            rs = [BO.t1(p[0][1], p[0][2], p[0][3], p[0][4], D.START, le) for d, p in days]
            df = pd.DataFrame([(r[0], r[1]) for r in rs if r], columns=["how", "pnl"])
            c = df.how.value_counts()
            lo = df[df.how.isin(["STOP", "LIQ"])].pnl.mean()
            a, b = account(days, le, BO.t1), account(days, le, BO.t2)
            assert len(a) == len(b) and all(x[:2] == y[:2] and abs(x[2] - y[2]) < 0.01 for x, y in zip(a, b)), (mg, le)
            led = pd.DataFrame(a, columns=["m", "how", "pnl", "bal"])
            mon = led.groupby("m").agg(pnl=("pnl", "sum"), win=("how", lambda s: (s == "TARGET").any()))
            ends = []
            for s in starts:
                x = account([d for d in days if d[0].strftime("%Y-%m") >= s], le, BO.t1)
                ends.append(x[-1][3] if x else D.START)
            ends = np.array(ends)
            print(f"  {'LENIENT' if le else 'STRICT '} per trade at $3,184: +$500 first {c.get('TARGET', 0) / len(df):.0%}, stop {(c.get('STOP', 0) + c.get('LIQ', 0)) / len(df):.0%}, "
                  f"24 h {c.get('24h', 0) / len(df):.0%}; avg stop ${lo:+,.0f}; avg per trade ${df.pnl.mean():+,.0f}")
            print(f"          full history from $3,184 -> ${a[-1][3] if a else D.START:,.0f}; months traded {len(mon)}, "
                  f"months with the +$500 win {int(mon.win.sum())}, months up {int((mon.pnl > 0).sum())}, down {int((mon.pnl < 0).sum())}; "
                  f"avg month ${mon.pnl.mean():+,.0f}")
            print(f"          22 monthly starts: median end ${np.median(ends):,.0f}; under $1,000 {int((ends < 1000).sum())}; "
                  f"above $3,184 {int((ends > D.START).sum())}")
        print()
    print("CHECK PASSED: t1 and t2 agree on every trade of every account.")


if __name__ == "__main__":
    main()
