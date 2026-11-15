"""Owner 2026-11-15: "Run all tests" -- the best trailing method (scripts/careful_trail_ways.py, chosen on 2025) as a
full account, carefully.
RULES (everything else as Spike Sell, docs/careful_sim_spec.md): resting limit sell 5% above the 00:02 price; 49% of
the balance as margin at up to 20x, +25% extra margin; exchange stop +2.25%. NO target. Once a 1m low reaches the
price netting +$150 x balance/$3,184, the stop moves to BREAK-EVEN (the price netting $0) and from then on trails 5%
above the lowest low since the fill (only ever down). Per minute: (a) a 1m high at/over the stop in force -> stop
fill (STRICT worse of the stop and the close +0.1%; LENIENT the stop +0.1%; at/over liquidation -> margin + extra
lost); (b) the stop is updated; (c) a close at/over the new stop -> bought back at the close +0.1%. Out 24 h after
the fill. An exit while the stop is still the original 2.25% is a full STOP (counts for the month-off rule); after
the move it is "TRAIL" (does not count, like a lock exit).
ACCOUNT: from $3,184; the first pick to fill is the trade, the other is cancelled; nothing new while a trade is open
at 00:02; month off after 2 full stops (variant: none); no trades under the $1,000 floor.
Two implementations (T1 loop + bisection sizing + account A; T2 stop arrays + closed-form sizing + account B) must
agree on every trade; per-pick results must equal scripts/careful_trail_ways.

    PYTHONPATH=. python scripts/careful_be_trail.py
"""
import sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_quick_book as QB  # noqa: E402
import careful_sim_a as A  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402
import careful_trail_ways as TW  # noqa: E402

ACT, TRAIL = 150.0, 0.05


def t1(sym, H, L, Cl, bal, le):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * bal, 0.25 * bal
    n, ld = B.sizing(sym, margin, e, extra)
    cap, Q = margin + extra, e * (1 + ld)
    raw = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    first = e * 1.0225
    stop, low, active = first, e, False
    act_px = e * (1 - (ACT * bal / D.START / n + 0.0012))
    be = e * (1 - 0.0012)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            if x >= Q:
                return "LIQ", -cap, k, j
            return ("STOP" if stop == first else "TRAIL"), raw(x), k, j
        low = min(low, L[j])
        if not active and L[j] <= act_px:
            active = True
        if active:
            stop = min(stop, be, low * (1 + TRAIL))
        if Cl[j] >= stop:
            return ("STOP" if stop == first else "TRAIL"), raw(Cl[j] * 1.001), k, j
    return "24h", raw(Cl[last] * 1.001), k, last


def t2(sym, H, L, Cl, bal, le):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    m_, x_ = bal * 0.49, bal * 0.25
    n, liq = Cc.notional_for(sym, m_, e)
    Q = e * (1 + liq + x_ / n)
    last = min(2880, k + 1440)
    idx = np.arange(k + 1, last + 1)
    h, l, c = H[idx], L[idx], Cl[idx]
    lowrun = np.minimum(e, np.minimum.accumulate(l))
    first = e * 1.0225
    hit = np.flatnonzero(l <= e * (1 - (ACT * bal / D.START / n + 0.0012)))
    cand = np.full(len(idx), np.inf)
    if hit.size:
        cand = np.where(np.arange(len(idx)) >= hit[0], np.minimum(e * (1 - 0.0012), lowrun * (1 + TRAIL)), np.inf)
    after = np.minimum(first, np.minimum.accumulate(cand))
    before = np.concatenate(([first], after[:-1]))
    net = lambda x: max(n * ((e - x) / e - 0.0007 - 0.0005 * x / e), -(m_ + x_))  # noqa: E731
    A_, C_ = h >= before, c >= after
    ev = np.flatnonzero(A_ | C_)
    if ev.size == 0:
        return "24h", net(Cl[last] * 1.001), k, last
    i = int(ev[0])
    if A_[i]:
        x = before[i] * 1.001 if le else max(before[i], c[i]) * 1.001
        if x >= Q:
            return "LIQ", -(m_ + x_), k, k + 1 + i
        return ("STOP" if before[i] == first else "TRAIL"), net(x), k, k + 1 + i
    return ("STOP" if after[i] == first else "TRAIL"), net(c[i] * 1.001), k, k + 1 + i


def acct_a(days, le, month_off=True, floor=1000.0, fixed=False):
    bal, busy, month, stops, out = D.START, -1, None, 0, []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        if (not fixed and bal < floor) or (month_off and stops >= 2) or busy > t0 + 2:
            continue
        best = None
        for rank, sym, H, L, Cl in picks:
            r = t1(sym, H, L, Cl, D.START if fixed else bal, le)
            if r and (best is None or r[2] < best[1][2]):
                best = (sym, r)
        if best is None:
            continue
        sym, r = best
        bal = bal + r[1] if fixed else max(bal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        out.append((day.date(), sym, r[2], r[3], r[0], r[1], bal))
    return out


def acct_b(days, le, month_off=True, floor=1000.0, fixed=False):
    st = dict(cash=D.START, free_at=-10 ** 9, ym=None, full=0)
    book = []
    for day, picks in days:
        base = int(day.timestamp()) // 60
        ym = (day.year, day.month)
        if ym != st["ym"]:
            st["ym"], st["full"] = ym, 0
        blocked = (st["cash"] < floor and not fixed) or (month_off and st["full"] >= 2) or st["free_at"] > base + 2
        if blocked:
            continue
        cands = []
        for rank, sym, H, L, Cl in picks:
            t = t2(sym, H, L, Cl, D.START if fixed else st["cash"], le)
            if t is not None:
                cands.append((t[2], rank, sym, t))
        if not cands:
            continue
        _, _, sym, t = min(cands, key=lambda z: (z[0], z[1]))
        st["cash"] = st["cash"] + t[1] if fixed else max(st["cash"] + t[1], 0.0)
        st["free_at"] = base + t[3]
        if t[0] in ("STOP", "LIQ"):
            st["full"] += 1
        book.append((day.date(), sym, t[2], t[3], t[0], t[1], st["cash"]))
    return book


def both(days, le, **kw):
    a, b = acct_a(days, le, **kw), acct_b(days, le, **kw)
    assert len(a) == len(b) and all(x[:5] == y[:5] and abs(x[5] - y[5]) < 0.01 and abs(x[6] - y[6]) < 0.01 for x, y in zip(a, b)), (le, kw)
    return a


def end(led):
    return led[-1][6] if led else D.START


def main():
    days = D.load()
    for le in (False, True):
        for day, picks in days:
            for rank, sym, H, L, Cl in picks:
                a, b = t1(sym, H, L, Cl, D.START, le), TW.w1(sym, H, L, Cl, le, "act", (150, 0.05))
                assert (a is None) == (b is None) and (a is None or (a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (day, sym, a, b)
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    for ps in (days, y26, real):
        for le in (False, True):
            for kw in (dict(), dict(month_off=False), dict(fixed=True, month_off=False)):
                both(ps, le, **kw)
    print("CHECKS PASSED: T1+account A and T2+account B agree on every trade (all periods, both fills, with / without month-off, "
          "fixed size); per-pick results equal the trailing comparison.\n")
    rows = (("Spike Sell as built ($500 target, lock, trail)", lambda ps, le: A.run(ps, "run4", le)[1]),
            ("Quick booking (first close netting >= $50)", lambda ps, le: end(QB.both(ps, le))),
            ("BREAK-EVEN + 5% TRAIL", lambda ps, le: end(both(ps, le))),
            ("BREAK-EVEN + 5% TRAIL, no month-off", lambda ps, le: end(both(ps, le, month_off=False))))
    print(f"ACCOUNTS from ${D.START:,.0f}, end balance STRICT .. LENIENT (floor $1,000; month-off after 2 full stops unless noted):")
    print(f"  {'':46s} | {'full history':>19s} | {'2026 from Jan 1':>19s} | {'20 real picks':>19s}")
    for nm, fn in rows:
        print(f"  {nm:46s} | " + " | ".join(f"{fn(ps, False):8,.0f} .. {fn(ps, True):8,.0f}" for ps in (days, y26, real)))
    print("\n22 MONTHLY STARTS (each run to Nov 14 2026), end balance, STRICT .. LENIENT:")
    for nm, fn in rows:
        e = {le: np.array([fn([d for d in days if d[0] >= mo.to_timestamp()], le) for mo in pd.period_range("2025-01", "2026-10", freq="M")]) for le in (False, True)}
        print(f"  {nm:46s} median {np.median(e[False]):6,.0f} .. {np.median(e[True]):6,.0f} | under $1,000: {(e[False] < 1000).sum():2d} .. {(e[True] < 1000).sum():2d} | "
              f"above $3,184: {(e[False] > D.START).sum():2d} .. {(e[True] > D.START).sum():2d} of 22 | best {e[False].max():7,.0f} .. {e[True].max():7,.0f}")
    for le in (False, True):
        df = pd.DataFrame(both(days, le, fixed=True, month_off=False), columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
        c = Counter(df.how)
        df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
        g = df.groupby("m").pnl.sum()
        w = df[df.pnl > 0]
        print(f"\nEVERY TRADE at a fixed $3,184 size (no month-off, no floor), {'LENIENT' if le else 'STRICT'}: {len(df)} trades, total ${df.pnl.sum():+,.0f}, "
              f"avg ${df.pnl.mean():+.0f}; {dict(sorted(c.items()))}")
        print(f"   winners {len(w)} ({len(w) / len(df):.0%}) avg ${w.pnl.mean():+,.0f}, biggest ${w.pnl.max():+,.0f}; top 10 winners = ${w.pnl.nlargest(10).sum():+,.0f}; "
              f"full stops avg ${df[df.how.isin(['STOP', 'LIQ'])].pnl.mean():+,.0f}; trail/break-even exits with a loss avg ${df[(df.how == 'TRAIL') & (df.pnl <= 0)].pnl.mean():+,.0f}")
        print(f"   months up {(g > 0).sum()} of {len(g)}; best {g.max():+,.0f}, worst {g.min():+,.0f}   (as built: -40,700 .. -18,224; quick booking: -19,321 .. -4,829)")
        if not le:
            for m, v in g.items():
                gg = df[df.m == m]
                print(f"     {m} trades {len(gg):2d}  winners {int((gg.pnl > 0).sum()):2d}  full stops {int(gg.how.isin(['STOP', 'LIQ']).sum()):2d}  P&L {v:+8,.0f}")
        df.drop(columns="m").to_csv(f"out/careful/trades_betrail_every_{'lenient' if le else 'strict'}.csv", index=False)
    for le in (False, True):
        led = both(days, le)
        print(f"\nBREAK-EVEN + 5% TRAIL account (month-off, floor), full history, {'LENIENT' if le else 'STRICT'}, month by month:")
        df = pd.DataFrame(led, columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
        df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
        prev = D.START
        for m, g in df.groupby("m"):
            c = Counter(g.how)
            print(f"   {m} start {prev:7,.0f} trades {len(g):2d} winners {int((g.pnl > 0).sum()):2d} full stops {c.get('STOP', 0) + c.get('LIQ', 0)}  P&L {g.pnl.sum():+8,.0f} end {g.bal.iloc[-1]:7,.0f}")
            prev = g.bal.iloc[-1]
        df.drop(columns="m").to_csv(f"out/careful/trades_betrail_bot_{'lenient' if le else 'strict'}.csv", index=False)
    for le in (False, True):
        print(f"\n20 REAL PICKS, BREAK-EVEN + 5% TRAIL, {'LENIENT' if le else 'STRICT'}:")
        for d, s, k, j, how, pnl, bal in both(real, le):
            print(f"   {d} {s:12s} {how:6s} held {j - k:4d} min  {pnl:+8,.0f}  balance {bal:8,.0f}")


if __name__ == "__main__":
    main()
