"""Owner 2026-11-18 "try it": the two middle paths after "book $20-30 with more margin" lost money
(out/careful/spike_small_usd.txt). The live spike rule ("C from the fill": resting sell 5% above the 00:02 close, stop
10% above the entry, 10% trail from the fill, out after 24 h) with, written before the run:
  BE $X      break-even lock: once the trade has been $X in profit (a 1m low passes the level; STRICT by 0.1%), the stop
             moves to the entry from the next minute (it still trails below that). Nothing booked.
  SPLIT $X   half the position has a resting take-profit worth $X (on that half); the other half runs the rule as now.
  SPLIT+BE $X  the same, and once the half is booked the other half's stop moves to the entry.
At 10% / 20% / 30% / 50% margin x 5x (position 0.5x / 1x / 1.5x / 2.5x today's $2,895.60; fixed $ size, no compounding).
Liquidation ~27% above -> a loss is capped at 30.2% of the position. History: the bot's day rules
(spike_high_margin_check.live_select). Real: the paper runner's picks 10-29..11-17 plus today's (11-18 MMTUSD at its
actual fill, BTWUSD), selected as the bot does. Two implementations (loop / arrays) must agree, and must equal
spike_verify.trade1 on the plain rule and on a full take-profit. Before fees; after = maker in, maker at the target,
taker otherwise.

    PYTHONPATH=. python scripts/spike_split_be.py      # -> out/careful/spike_split_be.txt
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import real_picks_rule_c as R  # noqa: E402
import spike_high_margin_check as K  # noqa: E402
from spike_verify import CELLS, MAKER, TAKER, entry1, trade1  # noqa: E402

C = CELLS["C from the fill"]
N20 = 2878.0
MARGINS = (0.10, 0.20, 0.30, 0.50)
STOP, TRAIL, MINS = 0.10, 0.10, 1440
TODAY = ("2026-11-18", ("MMTUSD",))          # BTWUSD: the live bot skipped it (it did not trade it)
ACTUAL = {**R.ACTUAL, ("2026-11-18", "MMTUSD"): (236, 0.1511)}


def sim1(H, L, Cl, k, e, inside, frac, tp, lock, lock_on_tp, strict):
    """minute loop. frac of the position books at a tp fall; lock = fall that moves the stop to the entry.
    returns (k, exit minute, return on the whole position, fee fraction, how)"""
    sp = e * (1 + STOP)
    last = min(len(Cl) - 1, k + MINS)
    rem, res, fee, how, booked = 1.0, 0.0, MAKER, [], False

    def stopped(j):
        return -((max(sp, Cl[j]) * 1.001 if strict else sp) / e - 1)
    if inside and H[k] >= sp:
        return k, k, stopped(k), MAKER + TAKER, "STOP"
    low, locked = np.inf, False
    for j in range(k + 1, last + 1):
        if not np.isfinite(H[j]):
            continue
        hs = H[j] >= sp
        ht = frac > 0 and not booked and L[j] <= e * (1 - tp) * (0.999 if strict else 1.0)
        if hs and (strict or not ht):
            res += rem * stopped(j)
            fee += rem * TAKER
            return k, j, res, fee, "+".join(how + ["BE" if locked and sp >= e * 0.999999 else "STOP"])
        if ht:
            booked = True
            res += frac * tp
            fee += frac * MAKER
            rem -= frac
            how.append("TARGET")
            if rem <= 1e-12:
                return k, j, res, fee, "TARGET"
            if hs:                                   # LENIENT: both in the minute -> target, then the rest stops
                res += rem * stopped(j)
                fee += rem * TAKER
                return k, j, res, fee, "TARGET+STOP"
        low = min(low, L[j])
        sp = min(sp, low * (1 + TRAIL))
        if (lock is not None and low <= e * (1 - lock) * (0.999 if strict else 1.0)) or (lock_on_tp and booked):
            locked = True
            sp = min(sp, e)
    fin = np.flatnonzero(np.isfinite(Cl[:last + 1]))
    x = Cl[fin[-1]]
    res += rem * -((x * 1.0005 if strict else x) / e - 1)
    fee += rem * TAKER
    return k, last, res, fee, "+".join(how + ["CLOCK"])


def sim2(H, L, Cl, k, e, inside, frac, tp, lock, lock_on_tp, strict):
    """arrays: the stop path first, then the first touches"""
    sp0 = e * (1 + STOP)
    if inside and H[k] >= sp0:
        return k, k, -((max(sp0, Cl[k]) * 1.001 if strict else sp0) / e - 1), MAKER + TAKER
    last = min(len(Cl) - 1, k + MINS)
    idx = np.arange(k + 1, last + 1)
    ok = np.isfinite(H[idx])
    hh, ll, cc = np.where(ok, H[idx], -np.inf), np.where(ok, L[idx], np.inf), Cl[idx]
    prevlow = np.r_[np.inf, np.minimum.accumulate(ll)[:-1]]
    sp = np.minimum(sp0, prevlow * (1 + TRAIL))
    s = 0.999 if strict else 1.0
    ft = np.flatnonzero(ll <= e * (1 - tp) * s) if frac > 0 else np.array([], int)
    trig = []
    if lock is not None:
        f = np.flatnonzero(prevlow <= e * (1 - lock) * s)
        trig += [f[0]] if f.size else []
    if lock_on_tp and ft.size:
        trig.append(ft[0] + 1)
    if trig:
        sp = np.where(np.arange(len(idx)) >= min(trig), np.minimum(sp, e), sp)
    hs = np.flatnonzero(hh >= sp)
    ts = hs[0] if hs.size else None
    ta = ft[0] if ft.size else None
    stopret = (lambda t: -((max(sp[t], cc[t]) * 1.001 if strict else sp[t]) / e - 1))
    fin = np.flatnonzero(np.isfinite(Cl[:last + 1]))
    clock = -((Cl[fin[-1]] * 1.0005 if strict else Cl[fin[-1]]) / e - 1)
    if ta is not None and (ts is None or ta < ts or (ta == ts and not strict)):
        rest = 1 - frac
        if rest <= 1e-12:
            return k, idx[ta], tp, MAKER + MAKER
        # with the lock on the target the stop path changes after ta: recompute the rest's first stop from ta on
        later = hs[hs >= ta]
        if later.size:
            return k, idx[later[0]], frac * tp + rest * stopret(later[0]), MAKER + frac * MAKER + rest * TAKER
        return k, last, frac * tp + rest * clock, MAKER + frac * MAKER + rest * TAKER
    if ts is not None:
        return k, idx[ts], stopret(ts), MAKER + TAKER
    return k, last, clock, MAKER + TAKER


def one(H, L, Cl, ent, frac, tp, lock, lock_on_tp, strict):
    en = ent or entry1(H, Cl, "live", strict)
    if en is None:
        return None
    k, e, _, inside = en
    a = sim1(H, L, Cl, k, e, inside, frac, tp, lock, lock_on_tp, strict)
    b = sim2(H, L, Cl, k, e, inside, frac, tp, lock, lock_on_tp, strict)
    assert a[1] == b[1] and abs(a[2] - b[2]) < 1e-9 and abs(a[3] - b[3]) < 1e-12, (a, b)
    if frac == 0 and lock is None:
        t = trade1(H, L, Cl, C, strict, ent)
        assert t[1] == a[1] and abs(t[2] - a[2]) < 1e-9, (t, a)
    if frac == 1 and lock is None:
        t = trade1(H, L, Cl, dict(C, tp=tp), strict, ent)
        assert t[1] == a[1] and abs(t[2] - a[2]) < 1e-9, (t, a)
    k, j, ret, fee, how = a
    if ret < -K.LIQ_LOSS and np.nanmax(H[k:j + 1]) >= e * (1 + K.LIQ_UP):
        ret, how = -K.LIQ_LOSS, "LIQUIDATED"
    return k, j, ret, fee, how


def select(rows):
    """the bot's rules: no orders if a position is open at 00:00; the day's first fill wins (ties by rank)"""
    keep, open_until = [], pd.Timestamp.min
    for day, g in pd.DataFrame(rows).sort_values(["day", "tin", "rank"]).groupby("day", sort=True):
        if open_until >= day:
            continue
        keep.append(g.iloc[0])
        open_until = g.iloc[0].tout
    return pd.DataFrame(keep)


def run(src, n, v, strict):
    frac, usd, lock, lock_on_tp = v
    tp = None if frac == 0 else usd / (frac * n)
    lk = None if lock is None else lock / n
    rows = []
    for day, rank, sym, H, L, Cl in src:
        ent = None
        if (day.strftime("%Y-%m-%d"), sym) in ACTUAL:
            k, e = ACTUAL[(day.strftime("%Y-%m-%d"), sym)]
            ent = (k, e, e, True)
        a = one(H, L, Cl, ent, frac, tp, lk, lock_on_tp, strict)
        if a:
            rows.append(dict(day=day, rank=rank, sym=sym, tin=day + pd.Timedelta(minutes=a[0]), tout=day + pd.Timedelta(minutes=a[1]),
                             ret=a[2], fee=a[3], how=a[4]))
    return select(rows)


def line(d, n):
    u, f = d.ret.values * n, d.fee.values * n
    w = u > 0
    cum = np.cumsum(u - f)
    dd = (np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max()
    return (f"{len(u):4d} trades  win {100 * w.mean():3.0f}%  avg win ${u[w].mean():+5.0f}  avg loss ${u[~w].mean():+5.0f}  per trade ${u.mean():+6.1f} "
            f"(after fees ${(u - f).mean():+6.1f})  total ${u.sum():+8,.0f}  worst run ${-dd:,.0f}")


VARIANTS = {"as now": (0, None, None, False)}
for x in (20, 30, 50, 100):
    VARIANTS[f"BE ${x}"] = (0, None, float(x), False)
for x in (20, 30, 50):
    VARIANTS[f"SPLIT ${x}"] = (0.5, float(x), None, False)
for x in (20, 30, 50):
    VARIANTS[f"SPLIT+BE ${x}"] = (0.5, float(x), None, True)
VARIANTS["ALL at $25 (ref)"] = (1.0, 25.0, None, False)


def main():
    hist = [(day, rank, sym, H, L, Cl) for day, ps in D.load() for rank, sym, H, L, Cl in ps]
    real = [(pd.Timestamp(d), r, s, H, L, Cl) for d, r, s, H, L, Cl in R.picks()]
    t0 = int(pd.Timestamp(TODAY[0], tz="UTC").timestamp())
    for rank, sym in enumerate(TODAY[1], 1):
        H, L, Cl = R.fetch(sym, t0)
        real.append((pd.Timestamp(TODAY[0]), rank, sym, H, L, Cl))
        time.sleep(0.3)
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    for m in MARGINS:
        n = 5 * m * N20
        w(f"===== MARGIN {m:.0%}: position ${n:,.0f} (the 10% stop ~ -${0.1 * n:,.0f})")
        for fill in ("STRICT", "LENIENT"):
            st = fill == "STRICT"
            for nm, v in VARIANTS.items():
                h, r = run(hist, n, v, st), run(real, n, v, st)
                mmt = r[r.sym == "MMTUSD"]
                w(f"  {fill:7} {nm:16} history {line(h, n)}")
                w(f"  {'':7} {'':16} real    {line(r, n)}  | today MMT ${(mmt.ret.sum() * n):+.0f} ({mmt.how.iloc[0] if len(mmt) else '-'})")
            w("")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_split_be.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
