"""Owner 2026-11-17: "my idea was make small profit per trade and stop at one point then start next month".

Grid written before the run. The live rule (rule C, 10% stop, 10% trail from the fill, 24 h) under the bot's day rules
(one trade a day, first fill wins, no orders on a day that starts with a position open), plus:
  per trade   no target (as now) | take-profit $40 | take-profit $45      (resting limit; % = $ / position)
  per month   no stop | stop for the rest of the month once the month's closed profit reaches $100 / $200 / $300 / $500
              (the trade that crosses it closes by its own rule; a trade entered in a month counts to that month; the
              bot starts again on the 1st -- if a position is still open on the 1st at 00:00, as always, no orders
              that day)
  margin      20% (now; position $2,878) and 10% ($1,439), 5x
$ at today's $2,895.60 every month (not compounded), so months compare directly. 2025-01..2026-11 (23 months).
STRICT / LENIENT fills (scripts/spike_verify.py; trade2, which agrees with trade1 on every trade). Two independent
implementations of the month logic (a day-by-day loop, and selecting month by month from the fills) must agree.
Before fees.

    PYTHONPATH=. python scripts/spike_month_target.py      # -> out/careful/spike_month_target.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_verify import CELLS, trade2  # noqa: E402

C = CELLS["C from the fill"]
N20 = 2878.0
TARGETS, MONTHLY, MARGINS = (None, 40.0, 45.0), (None, 100.0, 200.0, 300.0, 500.0), (0.20, 0.10)


def fills(days, c, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            a = trade2(H, L, Cl, c, strict)
            if a:
                rows.append((day, rank, sym, day + pd.Timedelta(minutes=a[0]), day + pd.Timedelta(minutes=a[1]), a[2], a[3]))
    return pd.DataFrame(rows, columns=["day", "rank", "sym", "tin", "tout", "ret", "how"])


def impl_a(f, n, cap):
    """day-by-day loop"""
    by_day = {d: g.sort_values(["tin", "rank"]) for d, g in f.groupby("day")}
    all_days = pd.date_range(f.day.min(), f.day.max(), freq="D")
    taken, open_until, month, pnl_m = [], pd.Timestamp.min, None, 0.0
    for d in all_days:
        if d.strftime("%Y-%m") != month:
            month, pnl_m = d.strftime("%Y-%m"), 0.0
        if open_until >= d or d not in by_day:
            continue
        if cap is not None and pnl_m >= cap:
            continue
        x = by_day[d].iloc[0]
        usd = x.ret * n
        taken.append((month, x.day, x.sym, usd, x.how))
        pnl_m += usd
        open_until = x.tout
    return pd.DataFrame(taken, columns=["month", "day", "sym", "usd", "how"])


def impl_b(f, n, cap):
    """month by month: first fill of each day, then walk the month's candidates, carrying the open position"""
    first = f.sort_values(["day", "tin", "rank"]).drop_duplicates("day")
    first = first.assign(month=first.day.dt.strftime("%Y-%m"))
    out, busy = [], pd.Timestamp.min
    for m, g in first.groupby("month", sort=True):
        tot = 0.0
        for x in g.itertuples():
            if x.day <= busy:
                continue
            if cap is not None and tot >= cap:
                break
            u = x.ret * n
            out.append((m, x.day, x.sym, u, x.how))
            tot += u
            busy = x.tout
    return pd.DataFrame(out, columns=["month", "day", "sym", "usd", "how"])


def summary(t, months):
    pm = t.groupby("month").usd.sum().reindex(months, fill_value=0.0)
    w = t.usd > 0
    return pm, (f"{len(t):3d} trades, win {100 * w.mean():3.0f}%, avg win ${t.usd[w].mean():+4.0f}, avg loss ${t.usd[~w].mean():+5.0f} | "
                f"avg month ${pm.mean():+5.0f}, months up {int((pm > 0).sum())}/{len(pm)}, worst month ${pm.min():+5.0f}, "
                f"best ${pm.max():+5.0f}, total ${pm.sum():+6,.0f}")


def main():
    days = D.load()
    months = sorted({d.strftime("%Y-%m") for d, _ in days})
    out = [__doc__.split("\n\n")[0], f"{len(months)} months {months[0]}..{months[-1]}. Before fees; $ at $2,895.60 each month.", ""]
    w = out.append
    for marg in MARGINS:
        n = N20 * marg / 0.20
        for usd_t in TARGETS:
            c = C if usd_t is None else dict(C, tp=usd_t / n)
            nm = "no take-profit (as now)" if usd_t is None else f"take-profit ${usd_t:.0f} ({100 * usd_t / n:.2f}%)"
            w(f"===== margin {marg:.0%} (position ${n:,.0f}), {nm}")
            for fill in ("STRICT", "LENIENT"):
                f = fills(days, c, fill == "STRICT")
                for cap in MONTHLY:
                    a, b = impl_a(f, n, cap), impl_b(f, n, cap)
                    assert len(a) == len(b) and (a.day.values == b.day.values).all() and np.allclose(a.usd, b.usd), (marg, usd_t, fill, cap)
                    pm, s = summary(a, months)
                    hit = "" if cap is None else f" | reached ${cap:.0f} in {int((pm >= cap).sum())}/{len(pm)} months"
                    lab = "no monthly stop" if cap is None else f"stop at +${cap:.0f}/month"
                    w(f"  {fill:7} {lab:20} {s}{hit}")
            w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_month_target.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
