"""Owner 2026-11-15: "Will it let winners go?" -- what the QUICK BOOK exit (first 1m close netting >= $50, stop 2.25%)
gives up: for every trade it books, what the same position would have done if held on with the 2.25% stop
(strict fill) for the rest of the 24 h. Sanity: reaching +$500 before the stop, over ALL fills, must match the
small-target test (57% on 2026, scripts/careful_small_target.py).

    PYTHONPATH=. python scripts/careful_left_on_table.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402

rows = []
for d, ps in D.load():
    for r, sym, H, L, Cl in ps:
        e = Cl[2] * 1.05
        k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
        if k is None:
            continue
        n, ld = B.sizing(sym, 0.49 * D.START, e, 0.25 * D.START)
        raw = lambda x: n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e)  # noqa: E731
        stop, last = e * 1.0225, min(2880, k + 1440)
        booked = None
        best, end_how, end_pnl, hit500 = -1e9, "24h", None, False
        tp500 = e * (1 - (500 / n + 0.0012))
        for j in range(k + 1, last + 1):
            if H[j] >= stop:
                end_how, end_pnl = "STOP", raw(max(stop, Cl[j]) * 1.001)
                break
            if L[j] <= tp500 * 0.999:
                hit500 = True
            best = max(best, raw(L[j]))
            if booked is None and raw(Cl[j] * 1.001) >= 50:
                booked = (j, raw(Cl[j] * 1.001))
        if end_pnl is None:
            end_pnl = raw(Cl[last] * 1.001)
        rows.append(dict(day=d, booked=booked is not None, book_pnl=booked[1] if booked else np.nan, best=best,
                         hit500=hit500, end_how=end_how, end_pnl=end_pnl))
t = pd.DataFrame(rows)
print(f"sanity: all fills reaching +$500 before the stop: 2026 {t[t.day >= '2026'].hit500.mean():.0%} (small-target test: 57%)\n")
b = t[t.booked]
print(f"QUICK BOOK booked {len(b)} of {len(t)} trades at an average ${b.book_pnl.mean():+.0f}.")
print("If those same trades had been HELD with the 2.25% stop instead:")
for x in (500, 1000, 2000, 3000):
    print(f"   best point reached at least +${x:,}: {(b.best >= x).mean():.0%}")
print(f"   came back and hit the stop later: {(b.end_how == 'STOP').mean():.0%} (avg ${b[b.end_how == 'STOP'].end_pnl.mean():+,.0f}); "
      f"still open at 24 h: {(b.end_how == '24h').mean():.0%} (avg ${b[b.end_how == '24h'].end_pnl.mean():+,.0f})")
print(f"   average if simply held to the stop or 24 h: ${b.end_pnl.mean():+,.0f}   vs booked ${b.book_pnl.mean():+,.0f}")
print(f"   average best point (only knowable afterwards): ${b.best.mean():+,.0f}")
for yr, g in (("2025", b[b.day < "2026"]), ("2026", b[b.day >= "2026"])):
    print(f"   {yr}: booked avg ${g.book_pnl.mean():+,.0f}; held avg ${g.end_pnl.mean():+,.0f}; later stopped {(g.end_how == 'STOP').mean():.0%}; reached +$500 {(g.best >= 500).mean():.0%}")
