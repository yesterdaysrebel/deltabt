"""Trade-by-trade trace of one month under the final run-4 rules, as the bot runs it (fill-first, the other order
cancelled, nothing new while a trade is open, off for the month after 2 full stops). Each trade's P&L is asserted
equal to run4_trailing.short (the tested function).

    PYTHONPATH=. python scripts/run4_month_trace.py 2026-01 [--lenient]
"""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import clean_compare as C  # noqa: E402
import run4_margin75 as M  # noqa: E402
import run4_trailing as T  # noqa: E402

MONTH = sys.argv[1] if len(sys.argv) > 1 else "2026-01"
LEN = "--lenient" in sys.argv
hm = lambda k: f"{int(k) // 60 % 24:02d}:{int(k) % 60:02d}" + ("+1d" if k >= 1440 else "")  # noqa: E731


def detail(sym, hi, lo, cl, k0, bal):
    k = bal / C.START
    margin, extra, target, lock = 0.49 * bal, 0.25 * bal, 500 * k, 200 * k
    lim = cl[k0 - 1] * 1.05
    kf = next((j for j in range(k0, min(len(cl), 1440)) if hi[j] >= lim * 1.0005), None)
    if kf is None:
        return None
    e = lim
    pos, liqd = C.size(sym, margin, e)
    liqd += extra / pos
    cost = C.MAKER + C.FEE + C.SPREAD / 2
    tp, sp, liq = e * (1 - (target / pos + cost)), e * 1.0225, e * (1 + liqd)
    lock_px = e * (1 - (lock / pos + cost))
    d = dict(ref=cl[k0 - 1], e=e, kf=kf, pos=pos, margin=margin, extra=extra, lev=pos / margin, tp=tp, sp0=sp, liq=liq,
             lock_px=lock_px, target=target, lock=lock, low=e, locked_at=None)
    stage, low = 0, e
    end = min(len(cl) - 1, kf + 1440)
    for j in range(kf + 1, end + 1):
        if hi[j] >= sp:
            xp = sp * 1.001 if LEN else max(sp, cl[j]) * 1.001
            how = "LIQ" if (xp >= liq and stage == 0) else ("STOP" if stage == 0 else "LOCKED")
            d.update(how=how, k=j, xp=xp, stop_at_exit=sp, high=hi[j], close=cl[j])
            break
        if lo[j] <= tp * 0.999:
            d.update(how="TARGET", k=j, xp=tp)
            break
        low = min(low, lo[j])
        if stage < 2 and lo[j] <= e * 0.99:
            stage, sp = 2, min(sp, lock_px)
            d["locked_at"] = j
            if cl[j] >= sp:
                d.update(how="LOCKED", k=j, xp=cl[j] * 1.001)
                break
        if stage == 2:
            sp = min(sp, low * 1.008)
            if cl[j] >= sp:
                d.update(how="LOCKED", k=j, xp=cl[j] * 1.001)
                break
    else:
        d.update(how="24h", k=end, xp=cl[end] * 1.001)
    d["low"] = low
    r = T.short(sym, hi, lo, cl, k0, margin, target, lock, extra, LEN, 0.008, None)
    d["pnl"] = r[1]
    assert r[0] == d["how"] and r[3] == d["k"], (r, d["how"], d["k"])
    return d


def main():
    C.checks()
    M.load_ranks()
    hist, *_ = C.paths_history()
    paths = sorted(hist + C.paths_real(), key=lambda x: x[0])
    ms = pd.Timestamp(MONTH + "-01")
    me = ms + pd.offsets.MonthBegin(1)
    days = {}
    for p in paths:
        if ms <= pd.Timestamp(p[0]) < me:
            days.setdefault(pd.Timestamp(p[0]), []).append(p)
    # start balance = the account's balance on the 1st when run from $3,184 on 2026-01-01
    import run4_account as RA
    pre = [p for p in paths if pd.Timestamp("2026-01-01") <= pd.Timestamp(p[0]) < ms]
    bal = RA.account_a(pre, 0.49, LEN, False)[0] if pre else C.START
    print(f"{MONTH}, {'LENIENT' if LEN else 'STRICT'} stop fills, start balance ${bal:,.2f} (the 2026 account from $3,184)\n")
    stops, busy, m0 = 0, -1, bal
    for day in sorted(days):
        t0 = int(day.timestamp()) // 60
        names = ", ".join(p[1] for p in sorted(days[day], key=lambda p: M.RANK[(day, p[1])]))
        if stops >= 2:
            print(f"{day.date()}  picks {names}: SKIPPED (hard stop: 2 full stops this month)")
            continue
        if busy > t0 + 2:
            print(f"{day.date()}  picks {names}: SKIPPED (yesterday's trade still open)")
            continue
        res = []
        for p in sorted(days[day], key=lambda p: M.RANK[(day, p[1])]):
            d = detail(p[1], p[2], p[3], p[4], p[5], bal)
            res.append((p[1], d))
        orders = "; ".join(f"{s} sell limit {d['e']:.6g}" if d else f"{s} no spike" for s, d in res)
        filled = [(d["kf"], s, d) for s, d in res if d]
        if not filled:
            print(f"{day.date()}  picks {names}: neither spiked 5% -> both orders cancelled at 24:00")
            continue
        kf, sym, d = min(filled, key=lambda x: x[0])
        other = [s for s, _ in res if s != sym]
        print(f"{day.date()}  picks {names}")
        print(f"   SOLD {sym} at {d['e']:.6g} ({hm(kf)} UTC; 00:02 price {d['ref']:.6g} +5%)"
              + (f"; {other[0]} order cancelled" if other else ""))
        print(f"   size ${d['pos']:,.0f} position = {d['lev']:.1f}x on ${d['margin']:,.0f} margin, +${d['extra']:,.0f} extra margin after the fill")
        print(f"   target {d['tp']:.6g} (net +${d['target']:,.0f}) | stop {d['sp0']:.6g} (+2.25%) | liquidation {d['liq']:.6g} (+{d['liq']/d['e']-1:.2%})")
        if d["locked_at"] is not None:
            print(f"   fell 1% at {hm(d['locked_at'])} -> stop moved to {d['lock_px']:.6g} (net +${d['lock']:,.0f}), then trailed 0.8% above the low {d['low']:.6g}")
        why = {"TARGET": "take-profit hit", "STOP": "stop hit", "LIQ": "LIQUIDATED (jumped past the stop)",
               "LOCKED": "locked/trailed stop hit", "24h": "24 h time exit"}[d["how"]]
        extra = ""
        if d["how"] in ("STOP", "LIQ"):
            extra = f" (stop {d['stop_at_exit']:.6g}; that minute high {d['high']:.6g}, close {d['close']:.6g})"
        bal = max(bal + d["pnl"], 0.0)
        stops += d["how"] in ("STOP", "LIQ")
        busy = t0 + d["k"]
        print(f"   EXIT {hm(d['k'])}: {why} at {d['xp']:.6g}{extra}")
        print(f"   P&L ${d['pnl']:+,.2f}  -> balance ${bal:,.2f}   (full stops this month: {stops})")
    print(f"\nMonth P&L ${bal - m0:+,.2f}; end balance ${bal:,.2f}")


if __name__ == "__main__":
    main()
