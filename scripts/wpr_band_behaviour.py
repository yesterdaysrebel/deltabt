"""How the Williams %R(140) band behaves (owner 2026-11-16: "just study how the wpr band behaves"). No trading rule.

%R = where the close sits in the last 140 bars' high-low range: 0 = at the high, -100 = at the low. Bands: above -20
(top), below -80 (bottom). Everything is folded so that an UP move and a DOWN move are read the same way: "leaves a
band" = closes outside it having closed inside it the bar before; "the other band" = the far one.

  1  where %R spends its time
  2  after it leaves a band: back into the same band, or across to the other one; how fast; how far it gets
  3  the half-way point: once it has reached -50, how often does it finish the trip
  4  from any level: chance of touching the far band before the near one (against a coin toss walk: level / distance)
  5  5m and 1m together: the same chance for the 5m, split by where the 1m %R is and by whether the 5m is rising
  6  what the PRICE did on trips that crossed and trips that fell back

DATA A: the owner's own coin-days (out/manual/candles, Delta 1m).  DATA B: Delta's top gainers 2025-01..2026-11
(careful dataset, 2 days of 1m per pick).

    PYTHONPATH=. python scripts/wpr_band_behaviour.py      # -> out/wpr_band_behaviour.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from deltabt.indicators import wpr  # noqa: E402
from wpr_5m_carry import segments_a, segments_b  # noqa: E402

N = 140


def first_band(w):
    """For every bar: which band %R closes in next (1 = top, 0 = bottom, nan = neither before the data ends),
    and how many bars until then."""
    n = len(w)
    nxt, dist = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(n - 2, -1, -1):
        x = w[i + 1]
        if np.isnan(x):
            continue
        if x >= -20:
            nxt[i], dist[i] = 1.0, 1
        elif x <= -80:
            nxt[i], dist[i] = 0.0, 1
        else:
            nxt[i], dist[i] = nxt[i + 1], dist[i + 1] + 1
    return nxt, dist


def trips(w, c):
    """Every time %R leaves a band -> (direction, crossed?, bars, furthest level reached in the trip's own terms,
    price move in the trip's direction). A trip ends when %R closes in either band."""
    out = []
    nxt, dist = first_band(w)
    for s, leave in ((1, (w[1:] > -80) & (w[:-1] <= -80)), (-1, (w[1:] < -20) & (w[:-1] >= -20))):
        for i in np.flatnonzero(leave) + 1:
            if np.isnan(nxt[i]):
                continue
            k = i + int(dist[i])
            u = w[i:k] if s > 0 else -100 - w[i:k]                    # own terms: -80 = the band it left, -20 = the other
            crossed = (nxt[i] == 1.0) if s > 0 else (nxt[i] == 0.0)
            out.append((s, crossed, int(dist[i]), float(u.max()), s * (c[k] / c[i] - 1)))
    return out


def collect(segs):
    acc = {tf: dict(zone=np.zeros(3), trips=[], lvl=[], runs=[]) for tf in ("1m", "5m")}
    joint = []
    for sym, T, h, l, c in segs:
        n5 = len(c) // 5
        H5, L5, C5 = h[:n5 * 5].reshape(n5, 5).max(1), l[:n5 * 5].reshape(n5, 5).min(1), c[4:n5 * 5:5]
        w1, w5 = wpr(h, l, c, N), wpr(H5, L5, C5, N)
        for tf, w, cc in (("1m", w1, c), ("5m", w5, C5)):
            v = w[~np.isnan(w)]
            acc[tf]["zone"] += [(v <= -80).sum(), ((v > -80) & (v < -20)).sum(), (v >= -20).sum()]
            acc[tf]["trips"] += trips(w, cc)
            nxt, _ = first_band(w)
            m = ~np.isnan(w) & ~np.isnan(nxt) & (w > -80) & (w < -20)
            acc[tf]["lvl"].append(np.c_[w[m], nxt[m]])
            inb = np.where(np.isnan(w), 0, np.where(w >= -20, 1, np.where(w <= -80, -1, 0)))
            edges = np.flatnonzero(np.diff(inb) != 0) + 1
            for a, b in zip(edges[:-1], edges[1:]):
                if inb[a] != 0:
                    acc[tf]["runs"].append(b - a)
        nxt5, _ = first_band(w5)
        j = np.arange(3, n5)
        x1 = w1[5 * j + 4]
        m = ~np.isnan(w5[j]) & ~np.isnan(w5[j - 3]) & ~np.isnan(nxt5[j]) & ~np.isnan(x1) & (w5[j] > -80) & (w5[j] < -20)
        joint.append(np.c_[w5[j][m], w5[j][m] - w5[j - 3][m], x1[m], nxt5[j][m]])
    for tf in acc:
        acc[tf]["trips"] = pd.DataFrame(acc[tf]["trips"], columns=["dir", "crossed", "bars", "far", "move"])
        acc[tf]["lvl"] = np.vstack(acc[tf]["lvl"])
    return acc, np.vstack(joint)


def report(name, segs, out):
    w = out.append
    acc, J = collect(segs)
    w(f"\n================ {name}")
    for tf in ("1m", "5m"):
        a = acc[tf]
        z = a["zone"] / a["zone"].sum()
        t = a["trips"]
        r = np.array(a["runs"])
        w(f"\n--- %R(140) on {tf} bars")
        w(f"1  time spent: bottom band {z[0]:.0%}, between {z[1]:.0%}, top band {z[2]:.0%}.  A stay inside a band lasts a median "
          f"{np.median(r):.0f} bars (a quarter last {np.quantile(r, .75):.0f}+, a tenth {np.quantile(r, .9):.0f}+).")
        w(f"2  leaves a band {len(t):,} times.  Crosses to the other band: {t.crossed.mean():.0%} (up moves {t.crossed[t.dir > 0].mean():.0%}, "
          f"down moves {t.crossed[t.dir < 0].mean():.0%}).  Falls back into the band it left: {1 - t.crossed.mean():.0%}.")
        fb, cr = t[~t.crossed], t[t.crossed]
        w(f"   falls back: {(fb.bars <= 1).mean():.0%} of them on the very next bar, {(fb.bars <= 3).mean():.0%} within 3, {(fb.bars <= 10).mean():.0%} within 10 "
          f"(median {fb.bars.median():.0f} bars).  Crossing takes a median {cr.bars.median():.0f} bars (middle half {cr.bars.quantile(.25):.0f}-{cr.bars.quantile(.75):.0f}).")
        w("   how far the trips get before they end (own terms: -80 = the band it left, -20 = the other band):")
        w("      " + "   ".join(f"past {x}: {(t.far >= x).mean():.0%}" for x in (-70, -60, -50, -40, -30)) + f"   to the other band: {t.crossed.mean():.0%}")
        w("3  once a trip has got past a level, the chance it finishes:  " +
          "   ".join(f"past {x}: {t.crossed[t.far >= x].mean():.0%}" for x in (-70, -60, -50, -40, -30)))
        L = a["lvl"]
        w("4  from any bar between the bands: chance %R closes in the TOP band before the bottom one, by its level now")
        w("      level      " + " ".join(f"{f'{lo}..{lo + 10}':>9}" for lo in range(-80, -20, 10)))
        w("      measured   " + " ".join(f"{L[(L[:, 0] > lo) & (L[:, 0] <= lo + 10), 1].mean():9.0%}" for lo in range(-80, -20, 10)))
        w("      coin toss  " + " ".join(f"{(lo + 5 + 80) / 60:9.0%}" for lo in range(-80, -20, 10)))
        w(f"6  PRICE over a trip (in the trip's direction): crossed -> median {100 * cr.move.median():+.2f}% (a tenth under {100 * cr.move.quantile(.1):+.2f}%); "
          f"fell back -> median {100 * fb.move.median():+.2f}% (a tenth under {100 * fb.move.quantile(.1):+.2f}%);  all trips mean {100 * t.move.mean():+.3f}%")
    w("\n--- 5  the 5m and the 1m together: chance the 5m %R closes in its TOP band before its bottom band")
    w("   (read a down move by mirror: 5m level -30 falling with the 1m at the bottom = 5m level -70 rising with the 1m at the top)")
    up = np.c_[J[:, 0], J[:, 1], J[:, 2], J[:, 3]]
    dn = np.c_[-100 - J[:, 0], -J[:, 1], -100 - J[:, 2], 1 - J[:, 3]]
    F = np.vstack([up, dn])                                               # folded: everything read as an up move
    w(f"   {'5m level now':14} {'5m over last 3 bars':20} | {'1m at the top (exhausted)':>26} {'1m in the middle':>18} {'1m at the bottom':>17} | {'all':>5} {'coin toss':>9}")
    for lo, hi in ((-80, -70), (-70, -60), (-60, -50), (-50, -35), (-35, -20)):
        for nm, dm in (("rising", F[:, 1] > 0), ("falling", F[:, 1] <= 0)):
            m = (F[:, 0] > lo) & (F[:, 0] <= hi) & dm
            cells = []
            for sm in (F[:, 2] >= -20, (F[:, 2] > -80) & (F[:, 2] < -20), F[:, 2] <= -80):
                k = m & sm
                cells.append(f"{F[k, 3].mean():.0%} ({k.sum():,})" if k.sum() >= 50 else f"- ({k.sum()})")
            w(f"   {f'{lo}..{hi}':14} {nm:20} | {cells[0]:>26} {cells[1]:>18} {cells[2]:>17} | {F[m, 3].mean():5.0%} {((lo + hi) / 2 + 80) / 60:9.0%}")
    return acc, J


def main():
    out = ["HOW THE WILLIAMS %R(140) BAND BEHAVES -- 1m and 5m bars, Delta trade candles."]
    res = {"A": report("A  the owner's own coin-days (66 coins)", segments_a(), out),
           "B": report("B  Delta's top gainers, 2025-01 .. 2026-11", segments_b(), out)}
    txt = "\n".join(out)
    print(txt)
    Path("out/wpr_band_behaviour.txt").write_text(txt + "\n")
    pd.to_pickle(res, "out/manual/wpr_band_behaviour.pkl")


if __name__ == "__main__":
    main()
