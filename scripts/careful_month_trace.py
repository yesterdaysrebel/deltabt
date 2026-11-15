"""Spike Sell, one month, trade by trade on the careful dataset (Delta 1m, 48 h per pick), with every price needed to
check it on a Delta chart. The account starts at $3,184 on the month's first pick day (or continues from the
careful account if --continue). Every trade is asserted equal to implementations A, B and C.

    PYTHONPATH=. python scripts/careful_month_trace.py 2025-01 [--lenient]
"""
import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_a as A  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402

MONTH = sys.argv[1]
LEN = "--lenient" in sys.argv


def clock(day, k):
    t = day + pd.Timedelta(minutes=int(k))
    return f"{t:%d %b %H:%M} UTC ({t + pd.Timedelta(hours=5, minutes=30):%d %b %H:%M} IST)"


def main():
    days = [d for d in D.load() if d[0].strftime("%Y-%m") == MONTH]
    led = [M.run(days, "run4", LEN)[0] for M in (A, B, Cc)]
    assert all(len(x) == len(led[0]) for x in led) and all(
        a[:6] == b[:6] == c[:6] and abs(a[6] - b[6]) < 0.01 and abs(a[6] - c[6]) < 0.01 for a, b, c in zip(*led))
    ledger = {(x[0], x[1]): x for x in led[0]}
    print(f"SPIKE SELL, {MONTH}, {'LENIENT' if LEN else 'STRICT'} stop fills, from ${D.START:,.0f} -- A, B and C agree on every trade.")
    print("Rules: sell limit 5% above the 00:02 price; 49% of balance margin at up to 20x; +25% extra margin after the fill;"
          "\nstop +2.25%; target nets +$500 x balance/$3,184; after a 1% fall the stop locks +$200 x scale, then trails 0.8%"
          "\nabove the low; out 24 h after the fill; off for the month after 2 full stops; stop below $1,000 balance.\n")
    bal, stops, busy = D.START, 0, -1
    for day, picks in days:
        t0 = int(day.timestamp()) // 60
        names = ", ".join(f"#{r} {s}" for r, s, *_ in picks)
        if bal < 1000 or stops >= 2 or busy > t0 + 2:
            why = "balance under $1,000 (bot STOPPED)" if bal < 1000 else ("2 full stops this month" if stops >= 2 else "previous trade still open at 00:02")
            print(f"{day.date()}  picks {names}: SKIPPED -- {why}")
            continue
        sc = bal / D.START
        info = []
        for rank, sym, H, L, Cl in picks:
            lim = Cl[2] * 1.05
            k = next((j for j in range(3, 1440) if H[j] >= lim * 1.0005), None)
            info.append((rank, sym, H, L, Cl, lim, k))
        filled = [x for x in info if x[6] is not None]
        if not filled:
            print(f"{day.date()}  picks {names}: no spike -- " + "; ".join(f"{s} limit {lim:.6g} (day high {H[3:1440].max():.6g})" for _, s, H, _, _, lim, _ in info))
            continue
        rank, sym, H, L, Cl, lim, k = min(filled, key=lambda x: (x[6], x[0]))
        x = ledger[(day.date(), sym)]
        margin, extra = 0.49 * bal, 0.25 * bal
        n, ld = B.sizing(sym, margin, lim, extra)
        p = D.PRODUCTS.get(sym) or B.DEFAULT
        contracts = n / (p["cv"] * lim)
        T = lim * (1 - (500 * sc / n + 0.0012))
        K = lim * (1 - (200 * sc / n + 0.0012))
        others = [f"{s} limit {l:.6g} " + (f"would fill {clock(day, kk)[:12]} -> cancelled" if kk else "never reached -> cancelled at 24:00")
                  for r, s, _, _, _, l, kk in info if s != sym]
        print(f"{day.date()}  picks {names}")
        print(f"   00:02 price {Cl[2]:.6g} -> SELL LIMIT {lim:.6g}; FILLED {clock(day, k)} (1m high {H[k]:.6g})")
        if others:
            print(f"   other order: {'; '.join(others)}")
        print(f"   size: ${n:,.0f} position = {contracts:,.0f} contracts (contract value {p['cv']}), {n / margin:.1f}x on ${margin:,.0f} margin, +${extra:,.0f} extra")
        print(f"   stop {lim * 1.0225:.6g} (+2.25%) | target {T:.6g} (net +${500 * sc:,.0f}) | lock level {K:.6g} (net +${200 * sc:,.0f}) "
              f"after a fall to {lim * 0.99:.6g} | liquidation {lim * (1 + ld):.6g} (+{ld:.2%})")
        j = x[4]
        tail = f"1m high {H[j]:.6g} low {L[j]:.6g} close {Cl[j]:.6g}"
        print(f"   EXIT {x[5]} {clock(day, j)}  [{tail}]")
        bal = x[7]
        stops += x[5] in ("STOP", "LIQ")
        busy = t0 + j
        print(f"   P&L ${x[6]:+,.2f} -> balance ${bal:,.2f}   (full stops this month: {stops})")
    print(f"\n{MONTH} end balance ${bal:,.2f} ({bal - D.START:+,.2f})")


if __name__ == "__main__":
    main()
