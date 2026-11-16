"""Owner 2026-11-15: "one for sure trade with 75% margin, let it run till 1000 profit and stop the bot for a week".
  entry    resting limit sell 5% above the 00:02 price, the day's BEST-RANKED pick only (75% margin cannot rest twice)
  size     75% of the balance as isolated margin at up to 20x (Delta per-coin limits), +20% of the balance as extra
           margin after the fill (what the wallet has left after Delta's fee reserve) -> liquidation further out
  exits    take-profit where the trade nets +$1,000 (fills 0.1% through it); stop +2.25% (STRICT: worse of the stop and
           the minute's close +0.1%; LENIENT: the stop +0.1%; at/over liquidation: margin + extra lost); out after 24 h
  pause    after a take-profit: no trading for 7 days. Option: month off after 2 full stops. Floor $1,000.
Two implementations of the trade (loop / vectorised with closed-form sizing) must agree on every trade.

    PYTHONPATH=. python scripts/careful_big_one.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402

MARGIN, EXTRA, TARGET, STOP = 0.75, 0.20, 1000.0, 0.0225


def t1(sym, H, L, Cl, bal, le):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    m, x = MARGIN * bal, EXTRA * bal
    n, ld = B.sizing(sym, m, e, x)
    cap, Q = m + x, e * (1 + ld)
    pl = lambda p: max(n * (-(p / e - 1) - 0.0007 - 0.0005 * p / e), -cap)  # noqa: E731
    tp, sp = e * (1 - (TARGET / n + 0.0012)), e * (1 + STOP)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= sp:
            p = sp * 1.001 if le else max(sp, Cl[j]) * 1.001
            return ("LIQ", -cap, k, j, n) if p >= Q else ("STOP", pl(p), k, j, n)
        if L[j] <= tp * 0.999:
            return "TARGET", pl(tp), k, j, n
    return "24h", pl(Cl[last] * 1.001), k, last, n


def t2(sym, H, L, Cl, bal, le):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    m, x = bal * MARGIN, bal * EXTRA
    n, liq = Cc.notional_for(sym, m, e)
    Q = e * (1 + liq + x / n)
    last = min(2880, k + 1440)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    tp, sp = e * (1 - (TARGET / n + 0.0012)), e * (1 + STOP)
    si, ti = np.flatnonzero(h >= sp), np.flatnonzero(l <= tp * 0.999)
    a = int(si[0]) if si.size else 10 ** 9
    b = int(ti[0]) if ti.size else 10 ** 9
    net = lambda p: max(n * ((e - p) / e - 0.0007 - 0.0005 * p / e), -(m + x))  # noqa: E731
    if a <= b and a < 10 ** 9:
        p = sp * 1.001 if le else max(sp, c[a]) * 1.001
        return ("LIQ", -(m + x), k, k + 1 + a, n) if p >= Q else ("STOP", net(p), k, k + 1 + a, n)
    if b < 10 ** 9:
        return "TARGET", net(tp), k, k + 1 + b, n
    return "24h", net(Cl[last] * 1.001), k, last, n


def account(days, le, month_off, fn=t1, floor=1000.0, pause_days=7):
    bal, busy, month, stops, until, out = D.START, -1, None, 0, None, []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        if bal < floor or (month_off and stops >= 2) or busy > t0 + 2 or (until is not None and day < until):
            continue
        rank, sym, H, L, Cl = picks[0]                      # the best-ranked pick only
        r = fn(sym, H, L, Cl, bal, le)
        if r is None:
            continue
        bal = max(bal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        if r[0] == "TARGET":
            until = day + pd.Timedelta(days=pause_days + 1)  # the next 7 days off
        out.append((day.date(), sym, r[0], r[1], bal, r[4]))
    return out


def main():
    days = D.load()
    for le in (False, True):
        for day, picks in days:
            rank, sym, H, L, Cl = picks[0]
            a, b = t1(sym, H, L, Cl, D.START, le), t2(sym, H, L, Cl, D.START, le)
            assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:4] == b[2:4] and abs(a[1] - b[1]) < 0.01)), (day, sym, a, b)
        for mo in (True, False):
            a, b = account(days, le, mo, t1), account(days, le, mo, t2)
            assert len(a) == len(b) and all(x[:3] == y[:3] and abs(x[3] - y[3]) < 0.01 for x, y in zip(a, b))
    print("CHECK PASSED: two implementations agree on every trade and every account.\n")
    # per trade, fixed $3,184, every best-ranked pick that fills
    print("PER TRADE at a fixed $3,184 (every top pick that fills, no pause), STRICT .. LENIENT:")
    for le in (False, True):
        rs = [(d, t1(p[0][1], p[0][2], p[0][3], p[0][4], D.START, le)) for d, p in days]
        rs = [(d, r) for d, r in rs if r]
        df = pd.DataFrame([(d, r[0], r[1], r[4]) for d, r in rs], columns=["day", "how", "pnl", "notional"])
        c = df.how.value_counts()
        print(f"   {'LENIENT' if le else 'STRICT '}: {len(df)} trades; reached +$1,000 {c.get('TARGET', 0)} ({c.get('TARGET', 0) / len(df):.0%}), "
              f"stopped {c.get('STOP', 0)} ({c.get('STOP', 0) / len(df):.0%}), liquidated {c.get('LIQ', 0)}, 24 h {c.get('24h', 0)}; "
              f"avg win ${df[df.how == 'TARGET'].pnl.mean():+,.0f}, avg stop ${df[df.how.isin(['STOP', 'LIQ'])].pnl.mean():+,.0f}; "
              f"avg per trade ${df.pnl.mean():+,.0f}; total ${df.pnl.sum():+,.0f}")
    pos = days[0][1][0]
    n0 = t1(pos[1], pos[2], pos[3], pos[4], D.START, False)[4]
    print(f"   (position on $3,184: ~${n0:,.0f}; +$1,000 needs a ~{1000 / n0 + 0.0012:.2%} fall; the stop is +2.25%)\n")
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    print("ACCOUNT from $3,184 (top pick only, week off after a +$1,000 win, $1,000 floor), end balance STRICT .. LENIENT:")
    print(f"   {'':34s} | {'full history':>17s} | {'2026 from Jan 1':>17s} | {'20 real picks':>17s} | 22 starts: under $1,000 / above $3,184")
    for mo in (True, False):
        cells = []
        for ps in (days, y26, real):
            a, b = account(ps, False, mo), account(ps, True, mo)
            cells.append(f"{(a[-1][4] if a else D.START):7,.0f} .. {(b[-1][4] if b else D.START):7,.0f}")
        u, ab = [0, 0], [0, 0]
        for m in pd.period_range("2025-01", "2026-10", freq="M"):
            sub = [d for d in days if d[0] >= m.to_timestamp()]
            for i, le in enumerate((False, True)):
                led = account(sub, le, mo)
                e = led[-1][4] if led else D.START
                u[i] += e < 1000
                ab[i] += e > D.START
        nm = "with month-off after 2 stops" if mo else "no month-off"
        print(f"   {nm:34s} | " + " | ".join(cells) + f" | {u[0]:2d} .. {u[1]:2d} / {ab[0]:2d} .. {ab[1]:2d} of 22")
    for le in (False, True):
        led = account(days, le, False)
        print(f"\n   full history, no month-off, {'LENIENT' if le else 'STRICT'} -- every trade until it stops:")
        for d, s, how, pnl, bal, n in led[:30]:
            print(f"      {d} {s:12s} {how:6s} {pnl:+8,.0f}  balance {bal:8,.0f}")
        if len(led) > 30:
            print(f"      ... {len(led)} trades in all; ends {led[-1][4]:,.0f}")
    print("\n   20 real picks, no month-off, STRICT:")
    for d, s, how, pnl, bal, n in account(real, False, False):
        print(f"      {d} {s:12s} {how:6s} {pnl:+8,.0f}  balance {bal:8,.0f}")


if __name__ == "__main__":
    main()
