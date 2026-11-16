"""Owner 2026-11-15: "Can we try 1% fall simulator". Top pick only, resting limit sell 5% above the 00:02 price, MARGIN of
the balance at up to 20x (Delta's live per-coin terms) + extra margin after the fill; TAKE PROFIT when the price falls
1% below the entry (a resting buy at entry x 0.99, filled when a 1m low trades 0.1% through it); STOP s above the
entry (STRICT: worse of the stop and the close +0.1%; LENIENT: the stop +0.1%; at/over liquidation: margin + extra
lost); out after 24 h. Per trade at a fixed $3,184, as an account (month off after 2 full stops, $1,000 floor), and the
one-time +$2,000 goal from all 615 start days. Two implementations (loop / vectorised + closed-form sizing).

    PYTHONPATH=. python scripts/careful_one_pct.py
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402

LIVE = json.load(open("out/delta_margin/product_limits_live_all.json"))
for p in LIVE.values():
    if p["im"] < 5.0:
        p.update(im=5.0, mm=min(p["mm"], 2.5))
D.PRODUCTS.clear()
D.PRODUCTS.update(LIVE)
FALL = 0.01


def p1(sym, H, L, Cl, bal, le, mg, ex, s):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    m, x = mg * bal, ex * bal
    n, ld = B.sizing(sym, m, e, x)
    cap, Q = m + x, e * (1 + ld)
    pl = lambda p: max(n * (-(p / e - 1) - 0.0007 - 0.0005 * p / e), -cap)  # noqa: E731
    tp, sp = e * (1 - FALL), e * (1 + s)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= sp:
            p = sp * 1.001 if le else max(sp, Cl[j]) * 1.001
            return ("LIQ", -cap, k, j) if p >= Q else ("STOP", pl(p), k, j)
        if L[j] <= tp * 0.999:
            return "TARGET", pl(tp), k, j
    return "24h", pl(Cl[last] * 1.001), k, last


def p2(sym, H, L, Cl, bal, le, mg, ex, s):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    m, x = bal * mg, bal * ex
    n, liq = Cc.notional_for(sym, m, e)
    Q = e * (1 + liq + x / n)
    last = min(2880, k + 1440)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    sp, tp = e * (1 + s), e * (1 - FALL)
    si, ti = np.flatnonzero(h >= sp), np.flatnonzero(l <= tp * 0.999)
    a = int(si[0]) if si.size else 10 ** 9
    b = int(ti[0]) if ti.size else 10 ** 9
    net = lambda p: max(n * ((e - p) / e - 0.0007 - 0.0005 * p / e), -(m + x))  # noqa: E731
    if a <= b and a < 10 ** 9:
        p = sp * 1.001 if le else max(sp, c[a]) * 1.001
        return ("LIQ", -(m + x), k, k + 1 + a) if p >= Q else ("STOP", net(p), k, k + 1 + a)
    if b < 10 ** 9:
        return "TARGET", net(tp), k, k + 1 + b
    return "24h", net(Cl[last] * 1.001), k, last


def account(days, le, cfg, i0=0, goal=None, floor=1000.0, month_off=True):
    bal, busy, month, stops, n = D.START, -1, None, 0, 0
    for day, picks in days[i0:]:
        t0 = int(day.timestamp()) // 60
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        if bal < floor:
            return ("FLOOR", (day - days[i0][0]).days, bal, n) if goal else (None, None, bal, n)
        if (month_off and stops >= 2) or busy > t0 + 2:
            continue
        r = p1(picks[0][1], picks[0][2], picks[0][3], picks[0][4], bal, le, *cfg)
        if r is None:
            continue
        bal = max(bal + r[1], 0.0)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        n += 1
        if goal and bal >= D.START + goal:
            return "GOAL", (day - days[i0][0]).days, bal, n
    if goal and bal < floor:
        return "FLOOR", None, bal, n
    return ("OPEN" if goal else None), None, bal, n


CFGS = [(0.10, 0.051, 0.0225), (0.50, 0.25, 0.0225), (0.75, 0.20, 0.0225), (0.10, 0.051, 0.01), (0.50, 0.25, 0.01), (0.75, 0.20, 0.01)]


def main():
    days = D.load()
    real = [d for d in days if d[0] >= pd.Timestamp("2026-10-29")]
    y26 = [d for d in days if d[0] >= pd.Timestamp("2026-01-01")]
    for cfg in CFGS:
        for le in (False, True):
            for day, picks in days:
                q = picks[0]
                a, b = p1(q[1], q[2], q[3], q[4], D.START, le, *cfg), p2(q[1], q[2], q[3], q[4], D.START, le, *cfg)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (cfg, day, a, b)
    print("CHECK PASSED: two implementations agree on every trade of every version, both fills.\n")
    print("TARGET = a 1% fall. Top pick only. STRICT .. LENIENT.\n")
    for mg, ex, s in CFGS:
        cfg = (mg, ex, s)
        print(f"=== {mg:.0%} margin, stop {s:.2%} ===")
        for le in (False, True):
            rs = [p1(p[0][1], p[0][2], p[0][3], p[0][4], D.START, le, *cfg) for d, p in days]
            df = pd.DataFrame([(r[0], r[1]) for r in rs if r], columns=["how", "pnl"])
            c = df.how.value_counts()
            w, lo = df[df.how == "TARGET"], df[df.how.isin(["STOP", "LIQ"])]
            need = -lo.pnl.mean() / (w.pnl.mean() - lo.pnl.mean())
            print(f"   per trade {'LENIENT' if le else 'STRICT '}: {len(df)} trades; 1% fall first {c.get('TARGET', 0) / len(df):.0%} (win avg ${w.pnl.mean():+,.0f}), "
                  f"stop first {(c.get('STOP', 0) + c.get('LIQ', 0)) / len(df):.0%} (avg ${lo.pnl.mean():+,.0f}, liquidated {c.get('LIQ', 0)}), 24 h {c.get('24h', 0)}; "
                  f"break-even needs {need:.0%} wins; avg per trade ${df.pnl.mean():+,.0f}")
        cells = []
        for ps in (days, y26, real):
            a, b = account(ps, False, cfg), account(ps, True, cfg)
            cells.append(f"{a[2]:7,.0f} .. {b[2]:7,.0f}")
        print(f"   account from $3,184: full history {cells[0]} | 2026 from Jan 1 {cells[1]} | 20 real picks {cells[2]}")
        for le in (False, True):
            res = [account(days, le, cfg, i0=i, goal=2000.0) for i in range(len(days))]
            g = [r[1] for r in res if r[0] == "GOAL"]
            f = [r for r in res if r[0] == "FLOOR"]
            o = sum(r[0] == "OPEN" for r in res)
            print(f"   one-time +$2,000 goal {'LENIENT' if le else 'STRICT '}: first in {len(g)} of 615 starts ({len(g) / 615:.0%}), floor first {len(f)} ({len(f) / 615:.0%}), "
                  f"neither by Nov 14 {o}" + (f"; median {np.median(g):.0f} days to the goal" if g else ""))
        print()


if __name__ == "__main__":
    main()
