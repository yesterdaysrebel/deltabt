"""Owner 2026-11-15: "So when SL hits we sell again?" -- run 4 (final rules) plus ONE re-entry short on the same coin
after a full stop (STOP or LIQ), placed only before 24:00 UTC of the pick day:
  R1  resting post-only sell 5% above the stop's fill (another spike), maker entry like the first;
  R2  resting sell 2.25% above the stop's fill;
  R3  market sell right away at that minute's close -0.1% (taker 0.05%).
The re-entry uses the same rules (49% / 25% extra of the balance AFTER the loss, stop +2.25%, target/lock scaled,
1% lock, 0.8% trail, 24 h) and its stop counts toward the 2-stops monthly hard stop.
Check: the general short here reproduces run4_trailing.short on every first entry (strict and lenient), and the
strict first entries also match the independent verify_independent.trade.

    PYTHONPATH=. python scripts/run4_resell.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_margin75 as M  # noqa: E402
import run4_trailing as T  # noqa: E402
import verify_independent as V  # noqa: E402


def short_from(sym, hi, lo, cl, ks, limit, maker, margin, target, lock, extra, lenient):
    """A short whose sell is `limit` (resting, filled when a high trades 0.05% through, from minute ks, before 24:00)
    or, if maker=False, a market sell AT `limit` in minute ks-1 (already filled). Returns (how, pnl, fill, exit)."""
    n = len(cl)
    if maker:
        kf = next((k for k in range(ks, min(n, 1440)) if hi[k] >= limit * 1.0005), None)
        if kf is None:
            return None
        fin, sin = C.MAKER, C.SPREAD / 2
    else:
        kf, fin, sin = ks - 1, C.FEE, 0.0
    e = limit
    pos, liqd = C.size(sym, margin, e)
    liqd += extra / pos
    at_risk = margin + extra
    cost = fin + C.FEE + sin
    pnl = lambda xp: max(pos * (-(xp / e - 1) - fin - C.FEE * xp / e - sin), -at_risk)   # noqa: E731
    tp, sp, liq = e * (1 - (target / pos + cost)), e * 1.0225, e * (1 + liqd)
    lock_px = e * (1 - (lock / pos + cost))
    locked, low = False, e
    end = min(n - 1, kf + 1440)
    for k in range(kf + 1, end + 1):
        if hi[k] >= sp:
            xp = sp * 1.001 if lenient else max(sp, cl[k]) * 1.001
            how = "LIQ" if (xp >= liq and not locked) else ("LOCKED" if locked else "STOP")
            return how, (-at_risk if how == "LIQ" else pnl(xp)), kf, k, xp
        if lo[k] <= tp * 0.999:
            return "TARGET", pos * (-(tp / e - 1) - fin - C.FEE * tp / e - sin), kf, k, tp
        low = min(low, lo[k])
        if not locked and lo[k] <= e * 0.99:
            locked, sp = True, min(sp, lock_px)
            if cl[k] >= sp:
                return "LOCKED", pnl(cl[k] * 1.001), kf, k, cl[k] * 1.001
        if locked:
            sp = min(sp, low * 1.008)
            if cl[k] >= sp:
                return "LOCKED", pnl(cl[k] * 1.001), kf, k, cl[k] * 1.001
    return "24h", pnl(cl[end] * 1.001), kf, end, cl[end] * 1.001


def legs(p, bal, mode, lenient, stops_left):
    """The day's trades on pick p: the first short, then (mode) one re-entry after a full stop. -> [(how, pnl, fill, exit)]"""
    day, sym, hi, lo, cl, k0 = p
    out = []

    def go(ks, limit, maker, b):
        k = b / C.START
        return short_from(sym, hi, lo, cl, ks, limit, maker, 0.49 * b, 500 * k, 200 * k, 0.25 * b, lenient)

    r = go(k0, cl[k0 - 1] * 1.05, True, bal)
    if r is None:
        return out
    out.append(r)
    if mode is None or r[0] not in ("STOP", "LIQ") or stops_left <= 1 or r[3] >= 1439:
        return out
    b2 = max(bal + r[1], 0.0)
    if b2 < 50:
        return out
    if mode == "R3":
        r2 = go(r[3] + 1, cl[r[3]] * 0.999, False, b2)
    else:
        r2 = go(r[3] + 1, r[4] * (1.05 if mode == "R1" else 1.0225), True, b2)
    if r2:
        out.append(r2)
    return out


def account(paths, mode, lenient, log=None, trace=None):
    bal, busy, month, stops = C.START, None, None, 0
    days = {}
    for p in paths:
        days.setdefault(pd.Timestamp(p[0]), []).append(p)
    for day in sorted(days):
        m = day.strftime("%Y-%m")
        if m != month:
            month, stops = m, 0
        row = None if log is None else log.setdefault(m, dict(start=bal, trades=0, re=0, pnl=0.0, T=0, L=0, S=0))
        t0 = int(day.timestamp()) // 60
        if bal < 50 or stops >= 2 or (busy is not None and busy > t0 + 2):
            continue
        cands = []
        for p in sorted(days[day], key=lambda p: M.RANK[(day, p[1])]):
            ls = legs(p, bal, mode, lenient, 2 - stops)
            if ls and (not cands or ls[0][2] < cands[0][1][0][2]):
                cands = [(p, ls)]
        if not cands:
            continue
        p, ls = cands[0]
        for i, r in enumerate(ls):
            bal = max(bal + r[1], 0.0)
            stops += r[0] in ("STOP", "LIQ")
            busy = t0 + r[3]
            if row is not None:
                row["trades"] += 1
                row["re"] += i > 0
                row["pnl"] += r[1]
                row[{"TARGET": "T", "LOCKED": "L", "STOP": "S", "LIQ": "S"}.get(r[0], "L") if r[0] != "24h" else "L"] += r[0] != "24h"
            if trace is not None:
                trace.append((day.date(), p[1], "re-sell" if i else "sell", r, bal))
        if row is not None:
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
        for _, s, h, l, c, k in allp:
            a = T.short(s, h, l, c, k, mg, 500.0, 200.0, ex, le, 0.008, None)
            b = short_from(s, h, l, c, k, c[k - 1] * 1.05, True, mg, 500.0, 200.0, ex, le)
            assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:4] == b[2:4] and abs(a[1] - b[1]) < 0.01)), (s, a, b)
            if not le and b is not None:
                v = V.trade(s, h, l, c, k, mg, ex, 500.0, 200.0, 0.008)
                assert v[0] == b[0] and v[3] == b[3] and abs(v[1] - b[1]) < 0.05, (s, v, b)
    print("CHECK PASSED: the general short reproduces run4_trailing.short on all first entries (strict+lenient) and the "
          "independent verify_independent.trade (strict).\n")
    print("Per trade on a fixed $3,184 (no account effects), STRICT .. LENIENT:")
    for nm, ps in (("20 real picks", real), ("full history + real", allp)):
        print(f"  {nm}:")
        for mode in (None, "R1", "R2", "R3"):
            o = []
            for le in (False, True):
                first = re = 0.0
                n_re = re_t = re_s = 0
                for p in ps:
                    ls = legs(p, C.START, mode, le, 2)
                    # fixed size: re-size the re-entry on $3,184 too
                    if len(ls) == 2:
                        r = ls[0]
                        day, sym, hi, lo, cl, k0 = p
                        if mode == "R3":
                            r2 = short_from(sym, hi, lo, cl, r[3] + 1, cl[r[3]] * 0.999, False, mg, 500.0, 200.0, ex, le)
                        else:
                            r2 = short_from(sym, hi, lo, cl, r[3] + 1, r[4] * (1.05 if mode == "R1" else 1.0225), True, mg, 500.0, 200.0, ex, le)
                        ls = [r, r2]
                    if ls:
                        first += ls[0][1]
                    if len(ls) == 2:
                        re += ls[1][1]
                        n_re += 1
                        re_t += ls[1][0] == "TARGET"
                        re_s += ls[1][0] in ("STOP", "LIQ")
                o.append((first + re, n_re, re_t, re_s, re))
            (s0, n0, t0, f0, r0), (s1, n1, t1, f1, r1) = o
            nm2 = {None: "run 4 as built", "R1": "R1 re-sell +5% above", "R2": "R1 re-sell +2.25% above".replace("R1", "R2"),
                   "R3": "R3 re-sell at market"}[mode]
            extra = "" if mode is None else f" | re-entries {n0}..{n1}: {t0}..{t1} targets, {f0}..{f1} stops, re-entry P&L ${r0:+,.0f} .. ${r1:+,.0f}"
            print(f"    {nm2:24s} total ${s0:+9,.0f} .. ${s1:+9,.0f}{extra}")
    y26 = [x for x in hist if x[0] >= pd.Timestamp("2026-01-01")] + real
    print("\nAs an account from $3,184 (fill-first, 2-stops hard stop, scaling), STRICT .. LENIENT:")
    for mode in (None, "R1", "R2", "R3"):
        r = [account(real, mode, le) for le in (False, True)]
        y = [account(y26, mode, le) for le in (False, True)]
        h = [account(allp, mode, le) for le in (False, True)]
        print(f"   {str(mode or 'as built'):8s}: 20 real picks ${r[0]:,.0f} .. ${r[1]:,.0f} | 2026 to Nov 14 ${y[0]:,.0f} .. ${y[1]:,.0f} | history + real ${h[0]:,.0f} .. ${h[1]:,.0f}")
    for mode in ("R1", "R3"):
        tr = []
        account(real, mode, False, trace=tr)
        print(f"\n  20 real picks, {mode}, STRICT, every trade:")
        for d, s, kind, r, b in tr:
            print(f"    {d} {s:12s} {kind:7s} {r[0]:6s} {r[1]:+8,.0f}  balance {b:8,.0f}")


if __name__ == "__main__":
    main()
