"""Owner 2026-11-15: "test what you found" -- the QUICK BOOK exit as a full account, carefully.
RULES (everything else as Spike Sell, docs/careful_sim_spec.md): resting limit sell 5% above the 00:02 price; 49% of
the balance as margin at up to 20x, +25% extra margin; exchange stop +2.25% (touch; STRICT = worse of the stop and
that minute's close +0.1%, LENIENT = the stop +0.1%; at/over liquidation = margin + extra lost); NO target order, NO
lock, NO trail: each minute after the fill, if buying back at that minute's close (+0.1%, taker) nets at least
$50 x balance / $3,184 after all costs, the bot buys back ("BOOKED"); otherwise out at the close 24 h after the fill.
Stop is checked before the booking in a minute.
ACCOUNT: from $3,184; the pick that fills first is the trade, the other order is cancelled; nothing new while a trade
is open at 00:02; month off after 2 full stops (variant: no month-off); no trades under the $1,000 floor.
Two implementations (Q1 functions + bisection sizing, Q2 minute-by-minute state machine + closed-form sizing) must
agree on every trade; per-pick results must equal scripts/careful_quick_exit.q.

    PYTHONPATH=. python scripts/careful_quick_book.py
"""
import sys
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_quick_exit as QE  # noqa: E402
import careful_sim_a as A  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402

BOOK = 50.0


# ---------------------------------------------------------------- implementation Q1
def q1_trade(sym, H, L, Cl, bal, le):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * bal, 0.25 * bal
    n, ld = B.sizing(sym, margin, e, extra)
    cap = margin + extra
    raw = lambda x: n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e)  # noqa: E731
    need = BOOK * bal / D.START
    stop, Q = e * 1.0225, e * (1 + ld)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            return ("LIQ", -cap, k, j) if x >= Q else ("STOP", max(raw(x), -cap), k, j)
        if raw(Cl[j] * 1.001) >= need:
            return "BOOKED", raw(Cl[j] * 1.001), k, j
    return "24h", max(raw(Cl[last] * 1.001), -cap), k, last


def q1(days, le, month_off=True, floor=1000.0, fixed=False):
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
            r = q1_trade(sym, H, L, Cl, D.START if fixed else bal, le)
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


# ---------------------------------------------------------------- implementation Q2
class Pos:
    def __init__(self, sym, e, k, bal, le):
        self.sym, self.e, self.k, self.le = sym, e, k, le
        self.margin, self.extra = bal * 0.49, bal * 0.25
        self.n, liq = Cc.notional_for(sym, self.margin, e)
        self.liq_px = e * (1 + liq + self.extra / self.n)
        self.stop_px = e * 1.0225
        self.need = BOOK * bal / D.START
        self.until = min(2880, k + 1440)

    def value(self, px):
        return self.n * ((self.e - px) / self.e - 0.0002 - 0.0005 - 0.0005 * px / self.e)

    def minute(self, m, hi, cl):
        worst = -(self.margin + self.extra)
        if hi >= self.stop_px:
            px = (self.stop_px if self.le else max(self.stop_px, cl)) * 1.001
            return ("LIQ", worst) if px >= self.liq_px else ("STOP", max(self.value(px), worst))
        v = self.value(cl * 1.001)
        if v >= self.need:
            return "BOOKED", v
        if m == self.until:
            return "24h", max(v, worst)
        return None


def q2(days, le, month_off=True, floor=1000.0, fixed=False):
    acct = dict(bal=D.START, free=-1, ym="", stops=0)
    led = []
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        ym = f"{day.year}-{day.month:02d}"
        if ym != acct["ym"]:
            acct["ym"], acct["stops"] = ym, 0
        if not fixed and acct["bal"] < floor:
            continue
        if month_off and acct["stops"] >= 2:
            continue
        if acct["free"] > t0 + 2:
            continue
        size_on = D.START if fixed else acct["bal"]
        pos, src = None, None
        for m in range(3, 2881):
            if pos is None:
                if m >= 1440:
                    break
                hit = [(rank, sym, H, L, Cl) for rank, sym, H, L, Cl in picks if H[m] >= Cl[2] * 1.05 * 1.0005]
                if hit:
                    src = min(hit, key=lambda z: z[0])
                    pos = Pos(src[1], src[4][2] * 1.05, m, size_on, le)
                continue
            res = pos.minute(m, src[2][m], src[4][m])
            if res:
                acct["bal"] = acct["bal"] + res[1] if fixed else max(acct["bal"] + res[1], 0.0)
                acct["free"] = t0 + m
                acct["stops"] += res[0] in ("STOP", "LIQ")
                led.append((day.date(), pos.sym, pos.k, m, res[0], res[1], acct["bal"]))
                break
    return led


def both(days, le, **kw):
    a, b = q1(days, le, **kw), q2(days, le, **kw)
    assert len(a) == len(b) and all(x[:5] == y[:5] and abs(x[5] - y[5]) < 0.01 and abs(x[6] - y[6]) < 0.01 for x, y in zip(a, b)), (le, kw)
    return a


def main():
    days = D.load()
    for le in (False, True):                              # per pick == the earlier single-implementation function
        for day, picks in days:
            for rank, sym, H, L, Cl in picks:
                a, b = q1_trade(sym, H, L, Cl, D.START, le), QE.q(sym, H, L, Cl, le, book=BOOK)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0].replace("TIME", "24h") and abs(a[1] - b[1]) < 0.01))
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    for ps in (days, y26, real):
        for le in (False, True):
            for kw in (dict(), dict(month_off=False), dict(fixed=True, month_off=False)):
                both(ps, le, **kw)
    print("CHECKS PASSED: Q1 and Q2 agree on every trade (all periods, both fills, with / without month-off, fixed size); "
          "per-pick results equal the earlier function.\n")
    run4 = lambda ps, le, var: A.run(ps, var, le)  # noqa: E731
    print(f"ACCOUNTS from ${D.START:,.0f}, end balance STRICT .. LENIENT (floor $1,000; month-off after 2 stops unless noted):")
    print(f"  {'':42s} | {'full history':>19s} | {'2026 from Jan 1':>19s} | {'20 real picks':>19s}")
    for nm, fn in (("Spike Sell as built ($500 target, lock, trail)", lambda ps, le: run4(ps, le, "run4")[1]),
                   ("QUICK BOOK (first close netting >= $50)", lambda ps, le: both(ps, le)[-1][6]),
                   ("QUICK BOOK, no month-off", lambda ps, le: both(ps, le, month_off=False)[-1][6])):
        print(f"  {nm:42s} | " + " | ".join(f"{fn(ps, False):8,.0f} .. {fn(ps, True):8,.0f}" for ps in (days, y26, real)))
    print("\n22 MONTHLY STARTS (each run to Nov 14 2026), end balance, STRICT .. LENIENT:")
    for nm, fn in (("Spike Sell as built", lambda ps, le: run4(ps, le, "run4")[1]),
                   ("QUICK BOOK", lambda ps, le: (both(ps, le) or [(0,) * 6 + (D.START,)])[-1][6]),
                   ("QUICK BOOK, no month-off", lambda ps, le: (both(ps, le, month_off=False) or [(0,) * 6 + (D.START,)])[-1][6])):
        e = {le: np.array([fn([d for d in days if d[0] >= mo.to_timestamp()], le) for mo in pd.period_range("2025-01", "2026-10", freq="M")]) for le in (False, True)}
        print(f"  {nm:26s} median {np.median(e[False]):6,.0f} .. {np.median(e[True]):6,.0f} | under $1,000: {(e[False] < 1000).sum():2d} .. {(e[True] < 1000).sum():2d} of 22 | "
              f"above $3,184: {(e[False] > D.START).sum():2d} .. {(e[True] > D.START).sum():2d} of 22 | best {e[False].max():7,.0f} .. {e[True].max():7,.0f}")
    for le in (False, True):
        led = both(days, le, fixed=True, month_off=False)
        df = pd.DataFrame(led, columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
        c = Counter(df.how)
        print(f"\nEVERY TRADE at a fixed $3,184 size (no month-off, no floor), {'LENIENT' if le else 'STRICT'}: {len(df)} trades, total ${df.pnl.sum():+,.0f}, "
              f"avg ${df.pnl.mean():+.0f}; {dict(sorted(c.items()))}; avg booked ${df[df.how == 'BOOKED'].pnl.mean():+.0f}, avg stop ${df[df.how.isin(['STOP', 'LIQ'])].pnl.mean():+.0f}; "
              f"minutes to book: median {(df[df.how == 'BOOKED'].j - df[df.how == 'BOOKED'].k).median():.0f}")
        if not le:
            df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
            g = df.groupby("m").pnl.agg(["size", "sum"])
            print(f"   months up {(g['sum'] > 0).sum()} of {len(g)}; best {g['sum'].max():+,.0f}, worst {g['sum'].min():+,.0f}  (Spike Sell as built: total -$40,700, 5 of 23 months up)")
        df.to_csv(f"out/careful/trades_quickbook_every_{'lenient' if le else 'strict'}.csv", index=False)
    for le in (False, True):
        led = both(days, le)
        print(f"\nQUICK BOOK account, full history, {'LENIENT' if le else 'STRICT'}, month by month:")
        df = pd.DataFrame(led, columns=["day", "sym", "k", "j", "how", "pnl", "bal"])
        df["m"] = pd.to_datetime(df.day).dt.strftime("%Y-%m")
        prev = D.START
        for m, g in df.groupby("m"):
            c = Counter(g.how)
            print(f"   {m} start {prev:7,.0f} trades {len(g):2d} booked {c.get('BOOKED', 0):2d} stops {c.get('STOP', 0) + c.get('LIQ', 0)} 24h {c.get('24h', 0)}  P&L {g.pnl.sum():+8,.0f} end {g.bal.iloc[-1]:7,.0f}")
            prev = g.bal.iloc[-1]
        df.drop(columns="m").to_csv(f"out/careful/trades_quickbook_bot_{'lenient' if le else 'strict'}.csv", index=False)
    for le in (False, True):
        print(f"\n20 REAL PICKS, QUICK BOOK, {'LENIENT' if le else 'STRICT'}:")
        for d, s, k, j, how, pnl, bal in both(real, le):
            print(f"   {d} {s:12s} {how:6s} held {j - k:4d} min  {pnl:+8,.0f}  balance {bal:8,.0f}")


if __name__ == "__main__":
    main()
