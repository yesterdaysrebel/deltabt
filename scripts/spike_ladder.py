"""Owner 2026-11-18: "every real reached some point at profit why not apply ladder on the sell? like sell 50% at certain
level, sell 25% at another, rest at another? does that make any difference?"

The live spike rule ("C from the fill": resting sell 5% above the 00:02 close, stop 10% above, trail 10% above the lowest
finished 1m low, out after 24 h) with take-profit LADDERS, written before the run (fall from the entry: share sold):
  L1  3%: 50%  6%: 25%  10%: 25%           L2  2%: 50%  5%: 25%  rest trails
  L3  3%: 50%  6%: 25%  rest trails        L4  3%: 33%  6%: 33%  10%: 34%
  L5  5%: 50%  10%: 25%  15%: 25%          L6  5%: 50%  10%: 25%  rest trails
  L7  3%: 25%  6%: 25%  10%: 25%  rest trails
  and L2, L3, L6 "+BE": once the first step has sold, the rest's stop moves to the entry.
"rest trails" = the unsold part keeps the stop / trail / 24 h. 10% margin x 5x ($1,439 position; 20% = double every $).
History 2025-01..2026-11-14 with the bot's day rules, 2025 and 2026 shown separately; real picks 10-29..11-18 (CROSS and
MMT at their live fills). Two implementations (minute loop / arrays) must agree on every trade; with no ladder both must
equal spike_verify.trade1. STRICT fills (stop wins a minute it shares with a step; target passed by 0.1%; stop at the
worse of the stop and the close +0.1%) and LENIENT (at the levels; steps first). Before fees; after = maker in, maker on
each step, taker on the rest.

    PYTHONPATH=. python scripts/spike_ladder.py      # -> out/careful/spike_ladder.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
import spike_split_be as B  # noqa: E402
from spike_verify import CELLS, MAKER, TAKER, entry1, trade1  # noqa: E402

C = CELLS["C from the fill"]
N = 5 * 0.10 * 2878.0
STOP, TRAIL, MINS = 0.10, 0.10, 1440
LADDERS = {"as now": ([], False),
           "L1 3/6/10 : 50/25/25": ([(0.03, .50), (0.06, .25), (0.10, .25)], False),
           "L2 2/5 : 50/25 + trail": ([(0.02, .50), (0.05, .25)], False),
           "L3 3/6 : 50/25 + trail": ([(0.03, .50), (0.06, .25)], False),
           "L4 3/6/10 : 33/33/34": ([(0.03, .33), (0.06, .33), (0.10, .34)], False),
           "L5 5/10/15 : 50/25/25": ([(0.05, .50), (0.10, .25), (0.15, .25)], False),
           "L6 5/10 : 50/25 + trail": ([(0.05, .50), (0.10, .25)], False),
           "L7 3/6/10 : 25x3 + trail": ([(0.03, .25), (0.06, .25), (0.10, .25)], False),
           "L2 + BE": ([(0.02, .50), (0.05, .25)], True),
           "L3 + BE": ([(0.03, .50), (0.06, .25)], True),
           "L6 + BE": ([(0.05, .50), (0.10, .25)], True)}


def loop(H, L, Cl, k, e, inside, steps, be, strict):
    sp = e * (1 + STOP)
    last = min(len(Cl) - 1, k + MINS)
    s = 0.999 if strict else 1.0

    def stopped(j):
        return -((max(sp, Cl[j]) * 1.001 if strict else sp) / e - 1)
    if inside and H[k] >= sp:
        return k, stopped(k), MAKER + TAKER
    rem, res, fee, done, low = 1.0, 0.0, MAKER, [False] * len(steps), np.inf
    for j in range(k + 1, last + 1):
        if not np.isfinite(H[j]):
            continue
        hs = H[j] >= sp
        hit = [i for i, (lv, fr) in enumerate(steps) if not done[i] and L[j] <= e * (1 - lv) * s]
        if hs and (strict or not hit):
            return j, res + rem * stopped(j), fee + rem * TAKER
        for i in hit:
            done[i] = True
            res += steps[i][1] * steps[i][0]
            fee += steps[i][1] * MAKER
            rem -= steps[i][1]
        if rem <= 1e-9:
            return j, res, fee
        if hs:
            return j, res + rem * stopped(j), fee + rem * TAKER
        low = min(low, L[j])
        sp = min(sp, low * (1 + TRAIL))
        if be and done[0]:
            sp = min(sp, e)
    fin = np.flatnonzero(np.isfinite(Cl[:last + 1]))
    x = Cl[fin[-1]]
    return last, res + rem * -((x * 1.0005 if strict else x) / e - 1), fee + rem * TAKER


def arrays(H, L, Cl, k, e, inside, steps, be, strict):
    sp0 = e * (1 + STOP)
    if inside and H[k] >= sp0:
        return k, -((max(sp0, Cl[k]) * 1.001 if strict else sp0) / e - 1), MAKER + TAKER
    last = min(len(Cl) - 1, k + MINS)
    idx = np.arange(k + 1, last + 1)
    ok = np.isfinite(H[idx])
    hh, ll, cc = np.where(ok, H[idx], -np.inf), np.where(ok, L[idx], np.inf), Cl[idx]
    s = 0.999 if strict else 1.0
    first = [(np.flatnonzero(ll <= e * (1 - lv) * s)) for lv, fr in steps]
    ta = [f[0] if f.size else None for f in first]
    sp = np.minimum(sp0, np.r_[np.inf, np.minimum.accumulate(ll)[:-1]] * (1 + TRAIL))
    if be and steps and ta[0] is not None:
        sp = np.where(np.arange(len(idx)) > ta[0], np.minimum(sp, e), sp)
    hs = np.flatnonzero(hh >= sp)
    ts = hs[0] if hs.size else None
    res, fee, rem, endm = 0.0, MAKER, 1.0, None
    for (lv, fr), t in zip(steps, ta):
        if t is not None and (ts is None or t < ts or (t == ts and not strict)):
            res += fr * lv
            fee += fr * MAKER
            rem -= fr
            endm = t if endm is None else max(endm, t)
    if rem <= 1e-9:
        return idx[endm], res, fee
    if ts is not None:
        return idx[ts], res + rem * -((max(sp[ts], cc[ts]) * 1.001 if strict else sp[ts]) / e - 1), fee + rem * TAKER
    fin = np.flatnonzero(np.isfinite(Cl[:last + 1]))
    x = Cl[fin[-1]]
    return last, res + rem * -((x * 1.0005 if strict else x) / e - 1), fee + rem * TAKER


def run(src, steps, be, strict):
    rows = []
    for day, rank, sym, H, L, Cl in src:
        key = (day.strftime("%Y-%m-%d"), sym)
        ent = None
        if key in B.ACTUAL:
            k, e = B.ACTUAL[key]
            ent = (k, e, e, True)
        en = ent or entry1(H, Cl, "live", strict)
        if en is None:
            continue
        k, e, _, inside = en
        a, b = loop(H, L, Cl, k, e, inside, steps, be, strict), arrays(H, L, Cl, k, e, inside, steps, be, strict)
        assert a[0] == b[0] and abs(a[1] - b[1]) < 1e-9 and abs(a[2] - b[2]) < 1e-9, (key, steps, be, a, b)
        if not steps:
            t = trade1(H, L, Cl, C, strict, ent)
            assert t[1] == a[0] and abs(t[2] - a[1]) < 1e-9, (key, t, a)
        ret = max(a[1], -B.K.LIQ_LOSS)
        rows.append(dict(day=day, rank=rank, sym=sym, tin=day + pd.Timedelta(minutes=k), tout=day + pd.Timedelta(minutes=int(a[0])), ret=ret, fee=a[2]))
    return B.K.live_select(pd.DataFrame(rows))


def line(d):
    u, f = d.ret.values * N, d.fee.values * N
    w = u > 0
    cum = np.cumsum(u - f)
    dd = (np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max()
    y = " ".join(f"{yr} ${(d.ret[d.day.dt.year == yr] * N).mean():+5.1f}" for yr in (2025, 2026))
    return (f"{len(u):3d} tr  win {100 * w.mean():3.0f}%  avg win ${u[w].mean():+4.0f}  avg loss ${u[~w].mean():+4.0f}  per trade ${u.mean():+5.1f} "
            f"(after fees ${(u - f).mean():+5.1f})  total ${u.sum():+7,.0f}  worst run ${-dd:,.0f}  [{y}]")


def main():
    hist = [(day, rank, sym, H, L, Cl) for day, ps in D.load() for rank, sym, H, L, Cl in ps]
    real = [(pd.Timestamp(d), r, s, H, L, Cl) for d, r, s, H, L, Cl in R.picks()]
    H, L, Cl = R.fetch("MMTUSD", int(pd.Timestamp("2026-11-18", tz="UTC").timestamp()))
    real.append((pd.Timestamp("2026-11-18"), 1, "MMTUSD", H, L, Cl))
    out = [__doc__.split("\n\n")[0], f"$ on a ${N:,.0f} position (10% margin x 5x). Before fees unless said.", ""]
    w = out.append
    for fill in ("STRICT", "LENIENT"):
        w(f"===== {fill}")
        for nm, (steps, be) in LADDERS.items():
            h, r = run(hist, steps, be, fill == "STRICT"), run(real, steps, be, fill == "STRICT")
            w(f"  {nm:26} history {line(h)}")
            w(f"  {'':26} real    {line(r)}  | " + " ".join(f"{x.sym[:5]} {x.ret * N:+.0f}" for x in r.itertuples()))
        w("")
    w("loop and array implementations agreed on every trade; 'as now' equals spike_verify.trade1.")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_ladder.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
