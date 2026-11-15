"""Run 4 (final rules) + re-sell A as ONE account, with the 2-stops monthly hard stop ON or OFF.
Re-sell A: after a run-4 full stop, a market sell when a 1m low is 5% under the high since the stop (before 24:00
UTC), stop 2.25% above the sell, no target, out 24 h after the stop. Sized like run 4 on the balance after the loss:
49% margin at the most leverage up to 20x Delta allows (C.size), +25% extra margin after the fill; a stop filled at or
past liquidation loses margin + extra. One position at a time: nothing new until the re-sell is out.
Cross-check: every re-sell's $ equals its R from scripts/run4_resell_search.resell (independently verified) x the
planned loss, unless liquidated.

    PYTHONPATH=. python scripts/run4_resell_account.py
"""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_margin75 as M  # noqa: E402
import run4_resell_search as S  # noqa: E402
import run4_trailing as T  # noqa: E402

W, ST = ("off", 0.05), ("fixed", 0.0225)
hm = lambda k: f"{int(k) // 60 % 24:02d}:{int(k) % 60:02d}" + ("+1d" if k >= 1440 else "")  # noqa: E731


def resell_usd(ev, bal, lenient):
    day, sym, hi, lo, cl, ks = ev
    ep = S.entry_point(hi, lo, cl, ks, W)
    if ep is None:
        return None
    k, px = ep
    e = px * 0.999
    margin, extra = 0.49 * bal, 0.25 * bal
    pos, liqd = C.size(sym, margin, e)
    liq = e * (1 + liqd + extra / pos)
    sp = e * 1.0225
    end = min(len(cl) - 1, ks + 1440)
    for j in range(k + 1, end + 1):
        if hi[j] >= sp:
            x = sp * 1.001 if lenient else max(sp, cl[j]) * 1.001
            how = "STOP"
            break
    else:
        j, x, how = end, cl[end] * 1.001, "TIME"
    if how == "STOP" and x >= liq:
        return "LIQ", -(margin + extra), k, j
    usd = pos * (-(x / e - 1) - 0.0005 - 0.0005 * x / e)
    R = S.resell(ev, W, ST, None, lenient)
    assert R[1] == how and abs(R[0] * pos * (sp * 1.001 / e - 1 + 0.001) - usd) < 0.01, (day, sym, R, usd)
    return how, max(usd, -(margin + extra)), k, j


def account(paths, lenient, cap_on, resell=True, trace=None, log=None):
    bal, low, busy, month, stops = C.START, C.START, None, None, 0
    days = {}
    for p in paths:
        days.setdefault(pd.Timestamp(p[0]), []).append(p)
    for day in sorted(days):
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        row = None if log is None else log.setdefault(m, dict(start=bal, n=0, re=0, pnl=0.0))
        t0 = int(day.timestamp()) // 60
        if bal < 50 or (cap_on and stops >= 2) or (busy is not None and busy > t0 + 2):
            continue
        k = bal / C.START
        best = None
        for p in sorted(days[day], key=lambda p: M.RANK[(day, p[1])]):
            r = T.short(p[1], p[2], p[3], p[4], p[5], 0.49 * bal, 500 * k, 200 * k, 0.25 * bal, lenient, 0.008, None)
            if r and (best is None or r[2] < best[1][2]):
                best = (p, r)
        if best is None:
            continue
        p, r = best
        bal = max(bal + r[1], 0.0)
        low = min(low, bal)
        busy = t0 + r[3]
        stops += r[0] in ("STOP", "LIQ")
        if trace is not None:
            trace.append((day.date(), p[1], "sell", r[0], hm(r[2]), hm(r[3]), r[1], bal))
        if row is not None:
            row["n"] += 1
            row["pnl"] += r[1]
        if resell and r[0] in ("STOP", "LIQ") and r[3] < 1439 and bal >= 50 and not (cap_on and stops >= 2):
            q = resell_usd((day, p[1], p[2], p[3], p[4], r[3]), bal, lenient)
            if q:
                bal = max(bal + q[1], 0.0)
                low = min(low, bal)
                busy = t0 + q[3]
                stops += q[0] in ("STOP", "LIQ")
                if trace is not None:
                    trace.append((day.date(), p[1], "RE-SELL", q[0], hm(q[2]), hm(q[3]), q[1], bal))
                if row is not None:
                    row["n"] += 1
                    row["re"] += 1
                    row["pnl"] += q[1]
        if row is not None:
            row["end"] = bal
    return bal, low


def main():
    C.checks()
    M.load_ranks()
    hist, *_ = C.paths_history()
    real = sorted(C.paths_real(), key=lambda x: x[0])
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    allp = hist + real
    print("CROSS-CHECK: every re-sell's $ = its independently verified R x planned loss (asserted in the run).\n")
    for cap_on in (False, True):
        for le in (False, True):
            tr = []
            account(real, le, cap_on, trace=tr)
            print(f"20 REAL PICKS, run 4 + re-sell A, loss cap {'ON' if cap_on else 'OFF'}, {'LENIENT' if le else 'STRICT'}:")
            for d, s, kind, how, a, b, pnl, bal in tr:
                print(f"   {d} {s:12s} {kind:7s} {how:6s} in {a:5s} out {b:8s} {pnl:+8,.0f}   balance {bal:8,.0f}")
            print()
    print(f"From ${C.START:,.0f}, end balance (lowest on the way), STRICT .. LENIENT:")
    print(f"  {'':40s} | {'20 real picks':>21s} | {'2026 to Nov 14':>27s} | {'history + real':>27s}")
    for resell in (False, True):
        for cap_on in (True, False):
            out = []
            for ps in (real, y26, allp):
                a = account(ps, False, cap_on, resell)
                b = account(ps, True, cap_on, resell)
                out.append(f"{a[0]:6,.0f} .. {b[0]:6,.0f} (low {a[1]:5,.0f})")
            nm = f"{'run 4 + re-sell A' if resell else 'run 4 as built'}, loss cap {'ON' if cap_on else 'OFF'}"
            print(f"  {nm:40s} | " + " | ".join(out))
    for cap_on in (False,):
        for le in (False, True):
            log = {}
            account(y26, le, cap_on, log=log)
            print(f"\n  2026 month by month, run 4 + re-sell A, loss cap OFF, {'LENIENT' if le else 'STRICT'}:")
            for mo, x in sorted(log.items()):
                print(f"    {mo} start {x['start']:7,.0f} trades {x['n']:2d} (re-sells {x['re']}) P&L {x['pnl']:+8,.0f} end {x.get('end', x['start']):7,.0f}")


if __name__ == "__main__":
    main()
