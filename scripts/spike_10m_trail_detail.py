"""Owner 2026-11-18: "run the 10% rule on real trades and show numbers, I want to see how does trailing SL work on them".

The live spike rule as it will run after the deploy: 10% margin x 5x ($1,439 position at $2,895.60, the bot's sizing),
resting sell 5% above the 00:02 close, stop 10% above the entry, then the stop trails 10% above the lowest FINISHED 1m low
since the fill, out after 24 h. The real picks 10-29..11-18 (the paper runner's, scripts/real_picks_rule_c.py; 11-17 CROSS
and 11-18 MMT at their actual live fills), the bot's day rules. For each trade: fill, starting stop, the best point
(lowest low) and the profit there, where the trail had the stop at the exit, the exit, the result, and how much of the
best was given back; then the stop's path hour by hour. STRICT fills (stop at the worse of the stop and the minute's
close +0.1%); the trade must equal spike_verify.trade1 / trade2. Before fees.

    PYTHONPATH=. python scripts/spike_10m_trail_detail.py      # -> out/careful/spike_10m_trail_detail.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import real_picks_rule_c as R  # noqa: E402
import spike_high_margin_check as K  # noqa: E402
import spike_split_be as B  # noqa: E402
from spike_verify import CELLS, trade1, trade2  # noqa: E402

C = CELLS["C from the fill"]
N = 5 * 0.10 * 2878.0
STRICT = True


def hm(day, m):
    return (pd.Timestamp(day) + pd.Timedelta(minutes=int(m))).strftime("%m-%d %H:%M")


def main():
    real = [(pd.Timestamp(d), r, s, H, L, Cl) for d, r, s, H, L, Cl in R.picks()]
    H, L, Cl = R.fetch("MMTUSD", int(pd.Timestamp("2026-11-18", tz="UTC").timestamp()))
    real.append((pd.Timestamp("2026-11-18"), 1, "MMTUSD", H, L, Cl))
    rows, arr = [], {}
    for day, rank, sym, H, L, Cl in real:
        key = (day.strftime("%Y-%m-%d"), sym)
        ent = None
        if key in B.ACTUAL:
            k, e = B.ACTUAL[key]
            ent = (k, e, e, True)
        a, b = trade1(H, L, Cl, C, STRICT, ent), trade2(H, L, Cl, C, STRICT, ent)
        if a is None:
            continue
        assert a[:2] == b[:2] and abs(a[2] - b[2]) < 1e-9 and a[3] == b[3], (key, a, b)
        rows.append(dict(day=day, rank=rank, sym=sym, tin=day + pd.Timedelta(minutes=a[0]), tout=day + pd.Timedelta(minutes=a[1]), a=a,
                         e=ent[1] if ent else Cl[2] * 1.05))
        arr[(day, sym)] = (H, L, Cl)
    sel = K.live_select(pd.DataFrame(rows))
    out = [__doc__.split("\n\n")[0], f"Position ${N:,.0f}; the starting stop (10% above) costs ~${0.1 * N:.0f}. Times UTC.", ""]
    w = out.append
    tot = []
    for x in sel.itertuples():
        H, L, Cl = arr[(x.day, x.sym)]
        k, j, ret, how, _ = x.a
        e = x.e
        sp0 = e * 1.10
        lows = L[k + 1:j + 1]
        ok = np.isfinite(lows)
        ll = np.where(ok, lows, np.inf)
        prevlow = np.r_[np.inf, np.minimum.accumulate(ll)[:-1]]     # finished minutes before each minute
        sp = np.minimum(sp0, prevlow * 1.10)
        ib = int(np.argmin(ll)) if ok.any() else None
        best = (e - ll[ib]) / e * N if ib is not None else 0.0
        stop_at_exit = sp[-1] if len(sp) else sp0
        exit_px = e * (1 - ret)
        usd = ret * N
        tot.append(usd)
        under = np.flatnonzero(sp < e)
        w(f"{x.day:%m-%d} {x.sym:12} sold {e:.6g} at {hm(x.day, k)}; stop starts {sp0:.6g} (-${0.1 * N:.0f} if hit)")
        if ib is not None:
            w(f"      best point {ll[ib]:.6g} at {hm(x.day, k + 1 + ib)}: +${best:.0f} in profit ({100 * (e - ll[ib]) / e:.1f}% down) "
              f"-> the trail had the stop at {min(sp0, ll[ib] * 1.1):.6g} ({'+' if ll[ib] * 1.1 < e else '-'}${abs(e - min(sp0, ll[ib] * 1.1)) / e * N:.0f} if hit)")
        w(f"      stop below the entry (profit locked) from: {hm(x.day, k + 1 + under[0]) if under.size else 'never -- the price never fell 9.1%'}")
        w(f"      EXIT {how:5} {hm(x.day, j)} at {exit_px:.6g} (stop then {stop_at_exit:.6g}) -> ${usd:+.0f}; gave back ${best - usd:.0f} of the best")
        path = []
        for h in range(0, (j - k) // 60 + 1):
            m = min(h * 60, len(sp) - 1)
            if m >= 0 and len(sp):
                path.append(f"{hm(x.day, k + 1 + m)[6:]} {sp[m]:.5g}")
        w("      stop by the hour: " + " | ".join(path[:25]))
        w("")
    t = np.array(tot)
    w(f"TOTAL {len(t)} trades: {int((t > 0).sum())} wins avg ${t[t > 0].mean():+.0f}, {int((t <= 0).sum())} losses avg ${t[t <= 0].mean():+.0f}; "
      f"total ${t.sum():+,.0f}, per trade ${t.mean():+.1f} before fees")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_10m_trail_detail.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
