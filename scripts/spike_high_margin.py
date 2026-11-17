"""Owner 2026-11-17: "try with high margin" (after the $40-45 target check, scripts/spike_usd_target.py).

Current rule (rule C, 10% trail FROM THE FILL, 10% stop, 24 h) with more margin at the same 5x leverage, so the position
grows: margin M of the balance -> notional 5 x M x balance (20% now = ~1x; $2,878 at $2,895.60). The extra margin
(25/49 of M) is kept, so the whole posting is 1.51 x M (60% -> 91% of the balance; more is impossible) and liquidation
stays ~27% above the entry at 5x -- past the 10% stop. (More size from more LEVERAGE instead would bring liquidation
inside the 10% stop, so it is not tested.)
Grid, written before the run: M = 20% (now), 30%, 40%, 50%, 60%; target none (as now), $40, $45 (a resting
take-profit at $40 / $45 on the position at today's balance, i.e. a smaller % move as M grows).
  PER TRADE  every fill 2025-01..2026-11 and the 13 real picks (as the bot trades them), $ at today's $2,895.60
  ACCOUNT    $2,895.60 compounding, one position at a time (first fill of the day wins), size capped at the product's
             full-leverage notional; lowest point, worst fall, and the date it first goes under the bot's $1,000 floor
             (the bot stops there).
STRICT / LENIENT fills and the two implementations of scripts/spike_verify.py (must agree on every trade). Before fees.
Liquidation: a fill past it (STRICT books API3 2025-08-19, +93% inside its fill minute, at -88%) is capped at the
margin posted, -30.2% of the position, and counted.

    PYTHONPATH=. python scripts/spike_high_margin.py      # -> out/careful/spike_high_margin.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
from spike_verify import CELLS, trade1, trade2  # noqa: E402

BAL, N20 = 2895.60, 2878.0
MARGINS, TARGETS = (0.20, 0.30, 0.40, 0.50, 0.60), (None, 40.0, 45.0)
C = CELLS["C from the fill"]
LIQ = -(1 + 25 / 49) / 5      # isolated: the most a trade can lose is the margin posted, 30.2% of the position at 5x


def cell(m, usd):
    return C if usd is None else dict(C, tp=usd / (N20 * m / 0.20))


def history(days, c, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            a, b = trade1(H, L, Cl, c, strict), trade2(H, L, Cl, c, strict)
            assert (a is None) == (b is None) and (a is None or (a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3])), (day, sym, a, b)
            if a:
                rows.append((day, sym, rank, a[0], a[1], max(a[2], LIQ), "LIQUIDATED" if a[2] < LIQ else a[3], a[4]))
    return pd.DataFrame(rows, columns=["day", "sym", "rank", "k", "j", "ret", "how", "dist"])


def real(c, strict):
    cand = []
    for day, rank, sym, H, L, Cl in R_PICKS:
        ent = None
        if (day, sym) in R.ACTUAL:
            k, e = R.ACTUAL[(day, sym)]
            ent = (k, e, e, True)
        a = trade1(H, L, Cl, c, strict, ent)
        cand.append((day, a[0] if a else 9999, rank, a))
    cand.sort(key=lambda x: (x[0], x[1], x[2]))
    out, traded, busy = [], set(), None
    for day, _, rank, a in cand:
        if day in traded or not a or (busy is not None and busy > pd.Timestamp(day)):    # no orders while a position is open at 00:00
            continue
        t_in = pd.Timestamp(day) + pd.Timedelta(minutes=a[0])
        if busy is not None and t_in <= busy:
            continue
        busy = pd.Timestamp(day) + pd.Timedelta(minutes=a[1])
        traded.add(day)
        out.append(max(a[2], LIQ))
    return np.array(out)


def per(r, n):
    usd = r * n
    w = usd > 0
    return f"win {100 * w.mean():3.0f}%  avg win ${usd[w].mean():+5.0f}  avg loss ${usd[~w].mean():+5.0f}  per trade ${usd.mean():+6.1f}"


def account(d, m):
    t0 = d.day.map(lambda x: int(pd.Timestamp(x).timestamp())).to_numpy()
    d = d.assign(tin=t0 + 60 * d.k.to_numpy(), tout=t0 + 60 * d.j.to_numpy()).sort_values(["tin", "rank"])
    bal, free, peak, mdd, lo, n, floor, worst = BAL, -1, BAL, 0.0, BAL, 0, None, 0.0
    for x in d.itertuples():
        if x.tin <= free:
            continue
        size = min(5 * m * bal * N20 / BAL, D.PRODUCTS.get(x.sym, {}).get("max_lev_notional", np.inf))
        pnl = size * x.ret
        worst = min(worst, pnl / bal)
        bal += pnl
        n += 1
        free = x.tout
        peak, lo = max(peak, bal), min(lo, bal)
        mdd = max(mdd, 1 - bal / peak)
        if floor is None and bal < 1000:
            floor = x.day
    return (f"{n} taken, end ${bal:>12,.0f}, lowest ${lo:,.0f}, worst fall {100 * mdd:.0f}%, worst single trade {100 * worst:.0f}% of the balance"
            + (f", UNDER THE $1,000 FLOOR {floor:%Y-%m-%d} (bot stops)" if floor is not None else ", never under $1,000"))


def main():
    global R_PICKS
    days = D.load()
    R_PICKS = R.picks()
    out = [__doc__.split("\n\n")[0], f"$ per trade at today's ${BAL:,.2f}. Before fees."]
    w = out.append
    for m in MARGINS:
        n = N20 * m / 0.20
        w(f"\n===== margin {m:.0%} at 5x: position ${n:,.0f} ({n / BAL:.1f}x the balance); a full 10% stop ~ ${0.10 * n:,.0f} "
          f"({100 * 0.10 * n / BAL:.0f}% of the balance); $40 = {100 * 40 / n:.2f}% move, $45 = {100 * 45 / n:.2f}%")
        for usd in TARGETS:
            c = cell(m, usd)
            S, Ln = history(days, c, True), history(days, c, False)
            nm = "no target (as now)" if usd is None else f"take-profit ${usd:.0f}"
            hy = " ".join(f"{n * S.ret[(S.day >= a) & (S.day < b)].mean():+.0f}" for a, b in
                          (("2025-01-01", "2025-07-01"), ("2025-07-01", "2026-01-01"), ("2026-01-01", "2026-07-01"), ("2026-07-01", "2027-01-01")))
            w(f"  {nm}")
            w(f"     history STRICT  {len(S)} trades  {per(S.ret, n)}  | half-years $ {hy} | liquidated {int((S.how == 'LIQUIDATED').sum())} (STRICT), {int((Ln.how == 'LIQUIDATED').sum())} (LENIENT)")
            w(f"     history LENIENT {'':10}  {per(Ln.ret, n)}")
            rs = real(c, True)
            w(f"     real picks {len(rs)} trades STRICT {per(rs, n)} | LENIENT ${n * real(c, False).mean():+.1f}")
            w(f"     account STRICT  {account(S, m)}")
            w(f"     account LENIENT {account(Ln, m)}")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_high_margin.txt").write_text(txt + "\n")


R_PICKS = []

if __name__ == "__main__":
    main()
