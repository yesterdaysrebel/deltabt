"""How Supertrend(10, 2) behaves with the Williams %R(140) band (owner 2026-11-16: "study how the super trend behaves
with the band"). Descriptive, no trading rule, no costs. Same two data sets as scripts/wpr_band_behaviour.py.

Everything is folded so an up move and a down move read the same way. For a Supertrend LEG (flip to the next flip) or
a %R TRIP (leaving a band until %R closes in either band), %R is given in the move's OWN TERMS:
-100..-80 = the band behind the move, -20..0 = the band ahead of it.

  1  Supertrend alone: how long a leg lasts, what price does over it
  2  where %R is when Supertrend flips, and where it is while a leg runs
  3  a leg by the %R level at its flip: length, price, does %R get to the band ahead before the flip back
  4  a %R trip by Supertrend's side when %R leaves the band: crossing chance, price
  5  %R level x Supertrend side: chance of the band ahead before the band behind
  6  which comes first on a trip that crosses: the Supertrend flip or %R leaving the band
  7  5m and 1m: a 5m trip by both Supertrends; a 1m leg by the 5m Supertrend's side

    PYTHONPATH=. python scripts/st_band_behaviour.py       # -> out/st_band_behaviour.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from deltabt.indicators import supertrend, wpr  # noqa: E402
from wpr_5m_carry import segments_a, segments_b  # noqa: E402
from wpr_band_behaviour import first_band  # noqa: E402

N = 140
FLAT_MAX = 0.20      # Supertrend's ATR collapses on no-trade minutes (Delta fills them as flat bars) and then flips on
#                      every tick, so a stretch with more than this share of flat 1m bars is left out (counts printed).


def liquid(segs, stats):
    for sym, T, h, l, c in segs:
        flat = float((h == l).mean())
        stats.append((len(c), flat, flat <= FLAT_MAX))
        if flat <= FLAT_MAX:
            yield sym, T, h, l, c


def legs(w, up, c, other=None):
    """Supertrend legs: (dir, bars, price move in the leg's direction, %R own terms at the flip, %R reached the band
    ahead during the leg, the other timeframe's Supertrend agrees at the flip)."""
    f = np.flatnonzero((up[1:] != up[:-1]) & (up[1:] != 0) & (up[:-1] != 0)) + 1
    f = f[f >= N]
    out = []
    for a, b in zip(f[:-1], f[1:]):
        s = up[a]
        if np.isnan(w[a]):
            continue
        u = w[a:b] if s > 0 else -100 - w[a:b]
        out.append((s, b - a, s * (c[b] / c[a] - 1), u[0], bool(np.nanmax(u) >= -20), np.nan if other is None else float(other[a] == s)))
    return out


def trips(w, up, c, other=None):
    """%R trips: (dir, crossed, bars, price move, Supertrend with the trip at the leave, bars from the leave to the
    Supertrend flip onto the trip's side (<0 = flipped before), other timeframe's Supertrend with the trip)."""
    nxt, dist = first_band(w)
    flips = np.flatnonzero(up[1:] != up[:-1]) + 1
    out = []
    for s, leave in ((1, (w[1:] > -80) & (w[:-1] <= -80)), (-1, (w[1:] < -20) & (w[:-1] >= -20))):
        for i in np.flatnonzero(leave) + 1:
            if np.isnan(nxt[i]) or up[i] == 0:
                continue
            k = i + int(dist[i])
            crossed = (nxt[i] == 1.0) if s > 0 else (nxt[i] == 0.0)
            if up[i] == s:                                             # already with: when did it flip onto this side
                p = np.searchsorted(flips, i, side="right") - 1
                lag = flips[p] - i if p >= 0 else np.nan
            else:                                                      # against: the next flip is onto the trip's side
                p = np.searchsorted(flips, i, side="right")
                lag = flips[p] - i if (p < len(flips) and flips[p] <= k) else np.inf
            out.append((s, crossed, int(dist[i]), s * (c[k] / c[i] - 1), bool(up[i] == s), lag,
                        np.nan if other is None else float(other[i] == s)))
    return out


def collect(segs):
    acc = {tf: dict(legs=[], trips=[], lvl=[], zone=np.zeros(3)) for tf in ("1m", "5m")}
    for sym, T, h, l, c in segs:
        n5 = len(c) // 5
        H5, L5, C5 = h[:n5 * 5].reshape(n5, 5).max(1), l[:n5 * 5].reshape(n5, 5).min(1), c[4:n5 * 5:5]
        w1, w5 = wpr(h, l, c, N), wpr(H5, L5, C5, N)
        u1, u5 = -supertrend(h, l, c, 2.0, 10)[1], -supertrend(H5, L5, C5, 2.0, 10)[1]
        u1, u5 = np.nan_to_num(u1), np.nan_to_num(u5)
        j = (np.arange(len(c)) + 1) // 5 - 1                           # the last 5m bar closed by each 1m close
        u5_on_1 = np.where(j >= 0, u5[np.clip(j, 0, n5 - 1)], 0)
        u1_on_5 = u1[4:n5 * 5:5]
        for tf, w, up, cc, oth in (("1m", w1, u1, c, u5_on_1), ("5m", w5, u5, C5, u1_on_5)):
            a = acc[tf]
            a["legs"] += legs(w, up, cc, oth)
            a["trips"] += trips(w, up, cc, oth)
            nxt, _ = first_band(w)
            m = ~np.isnan(w) & (up != 0)
            own = np.where(up > 0, w, -100 - w)                        # %R in the Supertrend's own terms
            a["zone"] += [(own[m] <= -80).sum(), ((own[m] > -80) & (own[m] < -20)).sum(), (own[m] >= -20).sum()]
            m &= ~np.isnan(nxt) & (w > -80) & (w < -20)
            a["lvl"].append(np.c_[w[m], up[m], nxt[m]])
    for tf in acc:
        acc[tf]["legs"] = pd.DataFrame(acc[tf]["legs"], columns=["dir", "bars", "move", "u0", "reach", "oth"])
        acc[tf]["trips"] = pd.DataFrame(acc[tf]["trips"], columns=["dir", "crossed", "bars", "move", "st_with", "lag", "oth"])
        acc[tf]["lvl"] = np.vstack(acc[tf]["lvl"])
    return acc


def pm(x):
    return f"median {100 * x.median():+.2f}%, mean {100 * x.mean():+.3f}%, worst tenth {100 * x.quantile(.1):+.2f}%, best tenth {100 * x.quantile(.9):+.2f}%"


def report(name, segs, out):
    w = out.append
    st = []
    acc = collect(liquid(segs, st))
    st = np.array(st, float)
    w(f"\n================ {name}")
    w(f"stretches kept: {int(st[:, 2].sum()):,} of {len(st):,} ({st[st[:, 2] == 1, 0].sum():,.0f} of {st[:, 0].sum():,.0f} one-minute bars); "
      f"flat no-trade minutes: {np.average(st[:, 1], weights=st[:, 0]):.0%} of all bars, {np.average(st[st[:, 2] == 1, 1], weights=st[st[:, 2] == 1, 0]):.0%} of the kept ones")
    for tf in ("1m", "5m"):
        a = acc[tf]
        g, t = a["legs"], a["trips"]
        o = "5m" if tf == "1m" else "1m"
        w(f"\n--- {tf} bars: {len(g):,} Supertrend legs, {len(t):,} %R trips")
        w(f"1  a leg lasts a median {g.bars.median():.0f} bars (middle half {g.bars.quantile(.25):.0f}-{g.bars.quantile(.75):.0f}, a tenth {g.bars.quantile(.9):.0f}+); "
          f"{(g.bars <= 3).mean():.0%} are over within 3 bars, {(g.bars <= 10).mean():.0%} within 10.")
        w(f"   price from one flip to the next, in the leg's direction: {pm(g.move)}; ends in profit {100 * (g.move > 0).mean():.0f}%")
        w(f"2  %R at the flip: still in the band behind {(g.u0 <= -80).mean():.0%}, between {((g.u0 > -80) & (g.u0 < -20)).mean():.0%}, "
          f"already in the band ahead {(g.u0 >= -20).mean():.0%} (median level {g.u0.median():.0f}).")
        z = a["zone"] / a["zone"].sum()
        w(f"   while a leg runs, %R is in the band ahead {z[2]:.0%} of the time, between {z[1]:.0%}, in the band behind {z[0]:.0%}.")
        w("3  a leg by the %R level at its flip")
        w(f"   {'%R at the flip':28} {'legs':>6} {'median bars':>11} {'%R reaches the band ahead':>26} {'ends in profit':>14}   price over the leg")
        for nm, m in (("in the band behind (-100..-80)", g.u0 <= -80), ("-80..-50", (g.u0 > -80) & (g.u0 <= -50)), ("-50..-20", (g.u0 > -50) & (g.u0 < -20)),
                      ("in the band ahead (-20..0)", g.u0 >= -20)):
            x = g[m]
            w(f"   {nm:28} {len(x):6d} {x.bars.median():11.0f} {100 * x.reach.mean():25.0f}% {100 * (x.move > 0).mean():13.0f}%   {pm(x.move)}")
        w("4  a %R trip by Supertrend's side at the bar %R leaves the band")
        w(f"   {'Supertrend':24} {'trips':>6} {'crosses':>8} {'median bars if it crosses':>26}   price over the trip")
        for nm, m in (("with the trip", t.st_with), ("against the trip", ~t.st_with)):
            x = t[m]
            w(f"   {nm:24} {len(x):6d} {100 * x.crossed.mean():7.0f}% {x.bars[x.crossed].median():26.0f}   {pm(x.move)}")
        ag = t[~t.st_with]
        fl = np.isfinite(ag.lag)
        w(f"   of the trips that start against Supertrend: it flips onto the trip's side during {fl.mean():.0%} of them "
          f"(median {ag.lag[fl].median():.0f} bars after the leave) -> those cross {100 * ag.crossed[fl].mean():.0f}%; it never flips on the other {1 - fl.mean():.0%} -> those cross {100 * ag.crossed[~fl].mean():.0f}%")
        wt = t[t.st_with & t.lag.notna()]
        w("   of the trips that start with Supertrend, by how long ago it flipped:  " + "   ".join(
            f"{nm}: {100 * wt.crossed[m].mean():.0f}% cross ({int(m.sum()):,})" for nm, m in
            (("same bar to 3 bars", wt.lag >= -3), ("4-10 bars", (wt.lag < -3) & (wt.lag >= -10)), ("11-30", (wt.lag < -10) & (wt.lag >= -30)), ("over 30", wt.lag < -30))))
        L = a["lvl"]
        F = np.vstack([np.c_[L[:, 0], L[:, 1], L[:, 2]], np.c_[-100 - L[:, 0], -L[:, 1], 1 - L[:, 2]]])   # folded to up moves
        w("5  chance %R closes in the band AHEAD before the band behind, by its level now and Supertrend's side")
        w("      level (own terms)    " + " ".join(f"{f'{lo}..{lo + 10}':>9}" for lo in range(-80, -20, 10)))
        for nm, m in (("Supertrend with    ", F[:, 1] > 0), ("Supertrend against ", F[:, 1] < 0)):
            w(f"      {nm}  " + " ".join(f"{F[m & (F[:, 0] > lo) & (F[:, 0] <= lo + 10), 2].mean():9.0%}" for lo in range(-80, -20, 10)))
        w("      share of bars with   " + " ".join(f"{(F[(F[:, 0] > lo) & (F[:, 0] <= lo + 10), 1] > 0).mean():9.0%}" for lo in range(-80, -20, 10)))
        cr = t[t.crossed]
        w(f"6  on the trips that cross ({len(cr):,}): Supertrend was already with the trip when %R left the band on {cr.st_with.mean():.0%}; "
          f"it flipped within 3 bars either side of the leave on {(cr.lag.abs() <= 3).mean():.0%}; it flipped later, during the trip, on {(~cr.st_with & np.isfinite(cr.lag)).mean():.0%}; "
          f"it never came onto the trip's side on {(~cr.st_with & ~np.isfinite(cr.lag)).mean():.0%}.")
        w(f"7  with the {o} Supertrend")
        for a_, b_, m in ((f"{tf} with", "with", t.st_with & (t.oth == 1)), (f"{tf} with", "against", t.st_with & (t.oth == 0)),
                          (f"{tf} against", "with", ~t.st_with & (t.oth == 1)), (f"{tf} against", "against", ~t.st_with & (t.oth == 0))):
            w(f"   a {tf} %R trip, {a_:11} / {o} {b_:8} {int(m.sum()):6,} trips, {100 * t.crossed[m].mean():3.0f}% cross, price {pm(t.move[m])}")
        for nm, m in ((f"{o} Supertrend with the flip", g.oth == 1), (f"{o} Supertrend against the flip", g.oth == 0)):
            x = g[m]
            w(f"   a {tf} leg, {nm:32} {len(x):6d} legs, median {x.bars.median():.0f} bars, in profit {100 * (x.move > 0).mean():.0f}%, {pm(x.move)}")
    return acc


def main():
    out = ["HOW SUPERTREND(10, 2) BEHAVES WITH THE WILLIAMS %R(140) BAND -- 1m and 5m bars, Delta trade candles, no costs."]
    res = {"A": report("A  the owner's own coin-days (66 coins)", segments_a(), out),
           "B": report("B  Delta's top gainers, 2025-01 .. 2026-11", segments_b(), out)}
    txt = "\n".join(out)
    print(txt)
    Path("out/st_band_behaviour.txt").write_text(txt + "\n")
    pd.to_pickle(res, "out/manual/st_band_behaviour.pkl")


if __name__ == "__main__":
    main()
