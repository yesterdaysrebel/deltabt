"""THE account simulation for run 4, matching what live/spike_live.py does (fixed 2026-11-15 after the owner's "are
you sure about numbers?": earlier account loops took the day's picks in LIST order and let the second pick trade after
the first closed -- the bot takes whichever order FILLS FIRST and cancels the other at once).

Per UTC day:
  * no new orders if a position is still open at entry time (00:02), the month already has 2 full stops (the hard
    stop), or the balance is under $50;
  * each eligible pick (all of them, or the best-ranked only) gets a resting order; the pick whose order FILLS FIRST
    (earliest fill minute; tie -> better rank) is the day's only trade -- the other order is cancelled at that fill;
  * the trade runs to its exit (target, lock/trail, stop, liquidation or 24 h) and may run into the next day.
Two independent implementations (A and B) must agree to the cent before any number is printed.

    PYTHONPATH=. python scripts/run4_account.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_margin75 as M  # noqa: E402  (ranks)
import run4_trailing as T  # noqa: E402
import verify_independent as V  # noqa: E402

TARGET, LOCK, EXTRA, TRAIL, LIMIT = 500.0, 200.0, 0.25, 0.008, 2
CAP = None        # monthly profit stop: no new entries for the rest of the month once the month is up this much ($)


def trade_a(p, bal, margin, lenient):
    day, sym, hi, lo, cl, k0 = p
    k = bal / C.START
    return T.short(sym, hi, lo, cl, k0, margin * bal, TARGET * k, LOCK * k, EXTRA * bal, lenient, TRAIL, None)


def account_a(paths, margin, lenient, first_only, start=C.START, log=None):
    bal, low = start, start
    busy_until = None                                    # absolute minute the open trade ends
    month, stops = None, 0
    days: dict = {}
    for p in paths:
        days.setdefault(pd.Timestamp(p[0]), []).append(p)
    for day in sorted(days):
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops, m_start = m, 0, bal
        t0 = int(day.timestamp()) // 60
        capped = CAP is not None and bal - m_start >= CAP
        if bal < 50 or stops >= LIMIT or capped or (busy_until is not None and busy_until > t0 + 2):
            if log is not None:
                log.setdefault(m, dict(start=bal, trades=0, pnl=0.0, TARGET=0, LOCKED=0, STOP=0, LIQ=0, h24=0))
            continue
        picks = sorted(days[day], key=lambda p: M.RANK[(day, p[1])])
        if first_only:
            picks = picks[:1]
        best = None
        for p in picks:
            r = trade_a(p, bal, margin, lenient)
            if r is not None and (best is None or r[2] < best[1][2]):
                best = (p, r)
        row = None if log is None else log.setdefault(m, dict(start=bal, trades=0, pnl=0.0, TARGET=0, LOCKED=0, STOP=0, LIQ=0, h24=0))
        if best is None:
            continue
        r = best[1]
        bal = max(bal + r[1], 0.0)
        low = min(low, bal)
        busy_until = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        if row is not None:
            row["trades"] += 1
            row["pnl"] += r[1]
            row["h24" if r[0] == "24h" else r[0]] += 1
        if log is not None:
            log[m]["end"] = bal
    if log is not None:
        prev = start
        for m in sorted(log):
            log[m].setdefault("end", log[m]["start"] + log[m]["pnl"])
            prev = log[m]["end"]
        _ = prev
    return bal, low


def account_b(paths, margin, lenient, first_only, start=C.START):
    """Independent implementation: events in time order, the independent trade function (verify_independent.trade)."""
    by_day: dict = {}
    for d, s, hi, lo, cl, k0 in paths:
        by_day.setdefault(pd.Timestamp(d), []).append((M.RANK[(pd.Timestamp(d), s)], s, hi, lo, cl, k0))
    balance, lowest, open_until, cur_month, full_stops = start, start, -1, "", 0
    for d in sorted(by_day):
        if d.strftime("%Y-%m") != cur_month:
            cur_month, full_stops, month_open = d.strftime("%Y-%m"), 0, balance
        day_min = int(d.timestamp()) // 60
        if CAP is not None and balance >= month_open + CAP:
            continue
        if balance < 50 or full_stops >= LIMIT or open_until > day_min + 2:
            continue
        cands = sorted(by_day[d])[:1] if first_only else sorted(by_day[d])
        fills = []
        for rank, s, hi, lo, cl, k0 in cands:
            scale = balance / C.START
            if lenient:
                res = T.short(s, hi, lo, cl, k0, margin * balance, TARGET * scale, LOCK * scale, EXTRA * balance, True, TRAIL, None)
            else:
                res = V.trade(s, hi, lo, cl, k0, margin * balance, EXTRA * balance, TARGET * scale, LOCK * scale, TRAIL)
            if res is not None:
                fills.append((res[2], rank, res))
        if not fills:
            continue
        fills.sort()
        how, pnl, fmin, xmin = fills[0][2]
        balance = max(balance + pnl, 0.0)
        lowest = min(lowest, balance)
        open_until = day_min + xmin
        if how in ("STOP", "LIQ"):
            full_stops += 1
    return balance, lowest


def main() -> None:
    C.checks()
    M.load_ranks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    configs = [("49%, both picks (as built)", 0.49, False), ("75%, best-ranked pick only", 0.75, True),
               ("49%, best-ranked pick only", 0.49, True)]
    for name, mg, fo in configs:                         # A and B must agree (strict: A uses run4_trailing, B the independent trade)
        for paths in (real, y26, hist):
            a, b = account_a(paths, mg, False, fo), account_b(paths, mg, False, fo)
            assert abs(a[0] - b[0]) < 0.01 and abs(a[1] - b[1]) < 0.01, (name, a, b)
    print("ACCOUNT CHECK PASSED: two independent account implementations agree to the cent (strict fills, all configs, "
          "real picks / 2026 / full history).\n")
    print(f"From ${C.START:,.0f}. STRICT .. LENIENT stop fills.\n")
    print(f"  {'version':30s} | {'20 real picks':>17s} | {'2026 to Nov 14':>17s} | {'history end':>15s} | {'starts <$1k':>11s} | {'median 12 mo':>15s}")
    for name, mg, fo in configs:
        c = {}
        for le in (False, True):
            rb = account_a(real, mg, le, fo)[0]
            yb = account_a(y26, mg, le, fo)[0]
            hb = account_a(hist, mg, le, fo)[0]
            gone, b12 = 0, []
            for mo in pd.period_range("2025-02", "2026-10", freq="M"):
                s = mo.to_timestamp()
                sub = [x for x in hist if x[0] >= s]
                gone += account_a(sub, mg, le, fo)[1] < 1000
                if s + pd.Timedelta(days=365) <= hist[-1][0]:
                    b12.append(account_a([x for x in sub if x[0] < s + pd.Timedelta(days=365)], mg, le, fo)[0])
            c[le] = (rb, yb, hb, gone, np.median(b12))
        s_, l_ = c[False], c[True]
        print(f"  {name:30s} | {s_[0]:7,.0f} .. {l_[0]:7,.0f} | {s_[1]:7,.0f} .. {l_[1]:7,.0f} | {s_[2]:6,.0f} .. {l_[2]:6,.0f} | "
              f"{s_[3]:4d} .. {l_[3]:2d} | {s_[4]:6,.0f} .. {l_[4]:6,.0f}")
    for name, mg, fo in configs[:2]:
        for le, nm in ((False, "STRICT"), (True, "LENIENT")):
            log: dict = {}
            account_a(y26, mg, le, fo, log=log)
            print(f"\n  2026 month by month, {name}, {nm}:")
            for mo, r in sorted(log.items()):
                print(f"    {mo}  start {r['start']:7,.0f}  trades {r['trades']:2d} ({r['TARGET']}T {r['LOCKED']}L {r['STOP'] + r['LIQ']}S)  "
                      f"P&L {r['pnl']:+8,.0f}  end {r['end']:7,.0f}")


if __name__ == "__main__":
    main()
