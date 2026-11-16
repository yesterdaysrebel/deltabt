"""Owner 2026-11-16: "how about 30% margin and exit at 1% fall". careful_one_pct's trade (top pick, resting sell 5%
above the 00:02 price, take-profit = a resting buy 1% below the entry, stop 2.25%, 24 h) at 30% margin + 15.3% extra
(30% x 25/49, as in careful_margin_sweep). Per trade at $3,184 (with a bootstrap 90% range), the bot account (month
off after 2 full stops, $1,000 floor) over the full history, 2026 and the 22 monthly starts, and one trade a month.
p1 and p2 run every account and are asserted equal.

    docker exec -w /src -e PYTHONPATH=/src:/src/scripts deltabt-py python scripts/careful_30_one_pct.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_one_pct as O  # noqa: E402

CFG = (0.30, 0.30 * 25 / 49, 0.0225)


def account(days, le, fn, one_a_month=False):
    bal, busy, month, stops, done, out = D.START, -1, None, 0, False, []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops, done = m, 0, False
        if bal < 1000.0 or stops >= 2 or busy > t0 + 2 or done:
            continue
        q = picks[0]
        r = fn(q[1], q[2], q[3], q[4], bal, le, *CFG)
        if r is None:
            continue
        bal = max(bal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        done = one_a_month
        out.append((m, r[0], r[1], bal))
    return out


def checked(days, le, one=False):
    a, b = account(days, le, O.p1, one), account(days, le, O.p2, one)
    assert len(a) == len(b) and all(x[:2] == y[:2] and abs(x[2] - y[2]) < 0.01 for x, y in zip(a, b)), (le, one)
    return a


def main():
    days = D.load()
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    starts = sorted({d.strftime("%Y-%m") for d, _ in days})
    rng = np.random.default_rng(3)
    p0 = days[0][1][0]
    print(f"30% margin + 15.3% extra, take-profit at a 1% fall, stop 2.25%, 24 h; top pick only. STRICT / LENIENT.\n")
    for le in (False, True):
        tag = "LENIENT" if le else "STRICT "
        rs = [O.p1(p[0][1], p[0][2], p[0][3], p[0][4], D.START, le, *CFG) for d, p in days]
        df = pd.DataFrame([(r[0], r[1]) for r in rs if r], columns=["how", "pnl"])
        c = df.how.value_counts()
        w, lo = df[df.how == "TARGET"].pnl.mean(), df[df.how.isin(["STOP", "LIQ"])].pnl.mean()
        boot = [rng.choice(df.pnl.values, len(df)).mean() for _ in range(5000)]
        print(f"{tag} per trade at $3,184: {len(df)} trades; 1% fall first {c.get('TARGET', 0) / len(df):.0%}, stopped "
              f"{(c.get('STOP', 0) + c.get('LIQ', 0)) / len(df):.0%} (liquidated {c.get('LIQ', 0)}), 24 h {c.get('24h', 0)}; "
              f"avg win ${w:+,.0f}, avg stop ${lo:+,.0f}; break-even needs {-lo / (w - lo):.0%} wins; "
              f"avg per trade ${df.pnl.mean():+,.0f} (90% range {np.percentile(boot, 5):+,.0f} .. {np.percentile(boot, 95):+,.0f})")
        for one in (False, True):
            a = checked(days, le, one)
            led = pd.DataFrame(a, columns=["m", "how", "pnl", "bal"])
            mon = led.groupby("m").pnl.sum()
            b26 = checked(y26, le, one)
            ends = []
            for s in starts:
                x = account([d for d in days if d[0].strftime("%Y-%m") >= s], le, O.p1, one)
                ends.append(x[-1][3] if x else D.START)
            ends = np.array(ends)
            bal = np.array([D.START] + list(led.bal))
            print(f"   {'one trade a month' if one else 'every day (bot rules)':<22}: full history -> ${bal[-1]:,.0f} "
                  f"(lowest ${bal.min():,.0f}, {len(led)} trades, months up {int((mon > 0).sum())} of {len(mon)}, avg month ${mon.mean():+,.0f}) | "
                  f"2026 from Jan 1 -> ${b26[-1][3] if b26 else D.START:,.0f} | 22 monthly starts: median ${np.median(ends):,.0f}, "
                  f"under $1,000 {int((ends < 1000).sum())}, above $3,184 {int((ends > D.START).sum())}")
        print()
    print("CHECK PASSED: p1 and p2 agree on every trade of every account.")


if __name__ == "__main__":
    main()
