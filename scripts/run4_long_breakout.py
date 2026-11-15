"""Owner 2026-11-15: "Why not buy the coin when it moves up?" -- the mirror of run 4 on the SAME picks: a BUY stop
`UP` above the 00:02 price (valid to 24:00); 49% margin at 20x (Delta per-coin limits), 25% extra margin after the
fill; stop -2.25%; take-profit netting +$500; after a 1m high +1% the stop moves to net +$200, then trails 0.8% under
the highest high; out after 24 h. Costs: entry is a stop-market = taker 0.05% + 0.05% slippage, STRICT fills it at the
worse of the trigger and that minute's close (+0.1%), LENIENT at the trigger (+0.1%); stops the same way; take-profit
fills when a high trades 0.1% through it. Stop and target in one minute = stop. Liquidation entry x (1 - (IM - MM) -
extra/position), losing margin + extra. Two independent implementations (A, B) must agree on every trade.

    PYTHONPATH=. python scripts/run4_long_breakout.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_margin75 as M  # noqa: E402
import run4_trailing as T  # noqa: E402
import verify_independent as V  # noqa: E402

FEE, SLIP = 0.0005, 0.0005


def long_a(sym, hi, lo, cl, k0, margin, target, lock, extra, lenient, up=0.05):
    n = len(cl)
    trig = cl[k0 - 1] * (1 + up)
    kf = next((k for k in range(k0, min(n, 1440)) if hi[k] >= trig), None)
    if kf is None:
        return None
    e = (trig if lenient else max(trig, cl[kf])) * 1.001
    pos, liqd = C.size(sym, margin, e)
    liqd += extra / pos
    at_risk = margin + extra
    pnl = lambda xp: max(pos * ((xp / e - 1) - FEE - FEE * xp / e), -at_risk)   # noqa: E731  (slippage is in the prices)
    tp = e * (1 + target / pos + 2 * FEE)
    sp, liq, lock_px = e * (1 - 0.0225), e * (1 - liqd), e * (1 + lock / pos + 2 * FEE)
    locked, high = False, e
    end = min(n - 1, kf + 1440)
    for k in range(kf + 1, end + 1):
        if lo[k] <= sp:
            xp = (sp if lenient else min(sp, cl[k])) * 0.999
            if not locked and xp <= liq:
                return "LIQ", -at_risk, kf, k
            return ("LOCKED" if locked else "STOP"), pnl(xp), kf, k
        if hi[k] >= tp * 1.001:
            return "TARGET", pos * ((tp / e - 1) - FEE - FEE * tp / e), kf, k
        high = max(high, hi[k])
        if not locked and hi[k] >= e * 1.01:
            locked, sp = True, max(sp, lock_px)
            if cl[k] <= sp:
                return "LOCKED", pnl(cl[k] * 0.999), kf, k
        if locked:
            sp = max(sp, high * (1 - 0.008))
            if cl[k] <= sp:
                return "LOCKED", pnl(cl[k] * 0.999), kf, k
    return "24h", pnl(cl[end] * 0.999), kf, end


def long_b(symbol, highs, lows, closes, k_decide, margin_usd, target_usd, lock_usd, extra_usd, lenient, up=0.05):
    """Written separately: V.position_and_liquidation sizing (bisection on Delta's terms), explicit state names."""
    trigger = closes[k_decide - 1] * (1 + up)
    hits = np.nonzero(highs[k_decide:1440] >= trigger)[0]
    if len(hits) == 0:
        return None
    f = k_decide + int(hits[0])
    entry = trigger * 1.001 if lenient else max(trigger, closes[f]) * 1.001
    notional, liq_dist = V.position_and_liquidation(symbol, margin_usd, entry)
    liq_dist += extra_usd / notional
    loss_cap = margin_usd + extra_usd

    def net(px):
        return max(notional * (px - entry) / entry - notional * FEE - notional * px / entry * FEE, -loss_cap)

    take = entry * (1 + target_usd / notional + 2 * FEE)
    protect = entry * 0.9775
    liquidation = entry * (1 - liq_dist)
    state = "OPEN"
    peak = entry
    last = min(len(closes) - 1, f + 1440)
    for k in range(f + 1, last + 1):
        if lows[k] <= protect:
            px = (protect if lenient else min(protect, closes[k])) * 0.999
            if state == "OPEN" and px <= liquidation:
                return "LIQ", -loss_cap, f, k
            return ("STOP" if state == "OPEN" else "LOCKED"), net(px), f, k
        if highs[k] >= take * 1.001:
            return "TARGET", notional * (take - entry) / entry - notional * FEE - notional * take / entry * FEE, f, k
        peak = max(peak, highs[k])
        if state == "OPEN" and highs[k] >= entry * 1.01:
            state = "LOCKED"
            protect = max(protect, entry * (1 + lock_usd / notional + 2 * FEE))
        if state == "LOCKED":
            protect = max(protect, peak * 0.992)
            if closes[k] <= protect:
                return "LOCKED", net(closes[k] * 0.999), f, k
    return "24h", net(closes[last] * 0.999), f, last


def account(paths, lenient, up, cap=None, log=None):
    """Same account logic as scripts/run4_account.account_a (fill-first, other cancelled, 2-stops hard stop)."""
    bal, busy, month, stops, m0 = C.START, None, None, 0, C.START
    days = {}
    for p in paths:
        days.setdefault(pd.Timestamp(p[0]), []).append(p)
    for day in sorted(days):
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops, m0 = m, 0, bal
        row = None if log is None else log.setdefault(m, dict(start=bal, trades=0, pnl=0.0, T=0, L=0, S=0))
        t0 = int(day.timestamp()) // 60
        if bal < 50 or stops >= 2 or (cap and bal - m0 >= cap) or (busy is not None and busy > t0 + 2):
            continue
        k = bal / C.START
        best = None
        for p in sorted(days[day], key=lambda p: M.RANK[(day, p[1])]):
            r = long_a(p[1], p[2], p[3], p[4], p[5], 0.49 * bal, 500 * k, 200 * k, 0.25 * bal, lenient, up)
            if r and (best is None or r[2] < best[2]):
                best = r
        if best is None:
            continue
        bal = max(bal + best[1], 0.0)
        busy = t0 + best[3]
        stops += best[0] in ("STOP", "LIQ")
        if row is not None:
            row["trades"] += 1
            row["pnl"] += best[1]
            row[{"TARGET": "T", "LOCKED": "L"}.get(best[0], "S")] += best[0] != "24h"
            row["end"] = bal
    return bal


def main():
    C.checks()
    M.load_ranks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    allp = hist + real
    mg, ex = 0.49 * C.START, 0.25 * C.START
    for le in (False, True):
        for p in allp:
            a = long_a(p[1], *p[2:6], mg, 500.0, 200.0, ex, le)
            b = long_b(p[1], *p[2:6], mg, 500.0, 200.0, ex, le)
            assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.05)), (p[0], p[1], a, b)
    print("CHECK PASSED: two independent long implementations agree on every trade (strict and lenient).\n")
    print("Per trade on a fixed $3,184, STRICT .. LENIENT. SHORT = run 4 as built (sell the spike), LONG = buy the breakout.")
    sets = (("20 real picks", real), ("full history + real", allp))
    for nm, ps in sets:
        print(f"\n  {nm}:")
        for label, fn in (("SHORT +5% (run 4)", None), ("LONG  +5% breakout", 0.05), ("LONG  +7.5% breakout", 0.075)):
            o = []
            for le in (False, True):
                if fn is None:
                    rs = [T.short(p[1], *p[2:6], mg, 500.0, 200.0, ex, le, 0.008, None) for p in ps]
                else:
                    rs = [long_a(p[1], *p[2:6], mg, 500.0, 200.0, ex, le, fn) for p in ps]
                rs = [r for r in rs if r]
                o.append((sum(r[1] for r in rs), len(rs), sum(r[0] == "TARGET" for r in rs), sum(r[0] in ("STOP", "LIQ") for r in rs),
                          sum(r[0] == "LIQ" for r in rs)))
            (s0, n0, t0, f0, l0), (s1, *_rest) = o
            print(f"    {label:22s} fills {n0:4d} | targets {t0:4d} | full stops {f0:4d} (liq {l0}) | total ${s0:+10,.0f} .. ${s1:+10,.0f}")
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    print("\nAs an account from $3,184 (fill-first, 2-stops hard stop, target/lock scale with the balance), STRICT .. LENIENT:")
    for up in (0.05, 0.075):
        r = [account(real, le, up) for le in (False, True)]
        y = [account(y26, le, up) for le in (False, True)]
        h = [account(allp, le, up) for le in (False, True)]
        print(f"   LONG +{up:.1%}: 20 real picks ${r[0]:,.0f} .. ${r[1]:,.0f} | 2026 to Nov 14 ${y[0]:,.0f} .. ${y[1]:,.0f} | full history ${h[0]:,.0f} .. ${h[1]:,.0f}")
    for le in (False, True):
        log = {}
        account(y26, le, 0.05, log=log)
        print(f"\n  LONG +5%, 2026 month by month, {'LENIENT' if le else 'STRICT'}:")
        for mo, x in sorted(log.items()):
            print(f"    {mo} start {x['start']:7,.0f} trades {x['trades']:2d} ({x['T']}T {x['L']}L {x['S']}S) P&L {x['pnl']:+8,.0f} end {x.get('end', x['start']):7,.0f}")


if __name__ == "__main__":
    main()
