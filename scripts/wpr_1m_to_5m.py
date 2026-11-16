"""Owner 2026-11-16: "when 1m has moved above -20 or -80, how does 5 min behave?"  Descriptive, no trading rule, no fees.

EVENT: the 1m %R(140) closes above -20 having been at or below it (an UP event), or closes below -80 having been at
or above it (a DOWN event). Everything is folded into the event's own terms: the "band ahead" is the top band for an
up event and the bottom band for a down event; the 5m level is given as -100 (far behind) .. 0 (fully ahead).
At each event, read the last CLOSED 5m bar, then follow the 5m:
  where the 5m %R is; whether the 5m Supertrend(10,2) is on the event's side;
  whether the 5m %R closes in the band ahead within 30 min / 1 h / 3 h, and (from between the bands) whether it gets
  to the band ahead before the band behind;
  what price does over the next 15 min and 1 h (in the event's direction).
BASELINE: random 1m bars with a random direction, read the same way -- the 5m from the same starting zone WITHOUT the
1m event. The difference between the two columns is what the 1m event adds.

DATA U: 30 most-traded Delta perps, 2026-08-01..11-15, liquid days.  DATA B: Delta's top gainers 2025-01..2026-11
(careful dataset), liquid stretches only.

    PYTHONPATH=. python scripts/wpr_1m_to_5m.py        # -> out/wpr_1m_to_5m.txt
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import pullback_5m_trend as P  # noqa: E402
from deltabt.indicators import supertrend, wpr  # noqa: E402
from st_band_behaviour import liquid  # noqa: E402
from wpr_5m_carry import segments_b  # noqa: E402
from wpr_band_behaviour import first_band  # noqa: E402

ZONES = (("still in the band behind", -100.01, -80), ("-80..-65", -80, -65), ("-65..-50", -65, -50), ("-50..-35", -50, -35),
         ("-35..-20", -35, -20), ("already in the band ahead", -20, 0.01))


def segs_u():
    seen = P.seen_coin_days()
    for c in json.loads((P.OUT / "universe.json").read_text())["coins"]:
        sym = c["symbol"]
        d = pd.read_parquet(P.OUT / f"{sym}.parquet").sort_values("time").reset_index(drop=True)
        flat = (d.high == d.low).groupby(d.time // 86400).mean()
        bad = set(flat.index[flat > P.FLAT_MAX]) | {dd for dd in flat.index if (sym, int(dd)) in seen}
        for g in P.segments(d):
            yield g.time.to_numpy(), g.high.to_numpy(float), g.low.to_numpy(float), g.close.to_numpy(float), bad


def segs_b():
    for sym, T, h, l, c in liquid(segments_b(), []):
        yield T, h, l, c, set()


def collect(segs, rng):
    rows = []
    for T, h, l, c, bad in segs:
        n = len(c)
        n5 = n // 5
        if n5 < 200:
            continue
        H5, L5, C5 = h[:n5 * 5].reshape(n5, 5).max(1), l[:n5 * 5].reshape(n5, 5).min(1), c[4:n5 * 5:5]
        w1, w5 = wpr(h, l, c, 140), wpr(H5, L5, C5, 140)
        u5 = np.nan_to_num(-supertrend(H5, L5, C5, 2.0, 10)[1])
        nxt, dist = first_band(w5)
        wp = np.r_[np.nan, w1[:-1]]
        with np.errstate(invalid="ignore"):
            ev = np.where((w1 > -20) & (wp <= -20), 1, np.where((w1 < -80) & (wp >= -80), -1, 0))
        lim = n5 * 5 - 185
        idx = np.flatnonzero(ev)
        idx = idx[(idx >= 720) & (idx < lim)]
        if lim <= 721:
            continue
        ridx = rng.choice(np.arange(720, lim), size=min(len(idx), lim - 720), replace=False)
        for kind, I, S in ((1, idx, ev[idx]), (0, ridx, rng.choice([1, -1], size=len(ridx)))):
            for i, s in zip(I, S):
                if T[i] // 86400 in bad:
                    continue
                j = (i + 1) // 5 - 1
                x = w5[j]
                if np.isnan(x):
                    continue
                u = x if s > 0 else -100 - x                           # 5m level in the event's own terms
                f = w5[j + 1:j + 37]
                f = f if s > 0 else -100 - f
                hit = np.flatnonzero(f >= -20)
                t_hit = int(hit[0]) + 1 if hit.size else 99
                ahead_first = np.nan
                if -80 < u < -20 and not np.isnan(nxt[j]):
                    ahead_first = float((nxt[j] == 1.0) if s > 0 else (nxt[j] == 0.0))
                rows.append((kind, s, T[i] // 86400, u, float(u5[j] == s), t_hit, ahead_first, s * (c[i + 15] / c[i] - 1), s * (c[i + 60] / c[i] - 1)))
    return pd.DataFrame(rows, columns=["event", "s", "day", "u", "st5", "t_hit", "ahead_first", "r15", "r60"])


def report(name, D, out):
    w = out.append
    E, R = D[D.event == 1], D[D.event == 0]
    w(f"\n================ {name}: {len(E):,} events ({int((E.s > 0).sum()):,} up, {int((E.s < 0).sum()):,} down), {len(R):,} random bars")
    w("each pair = after the 1m event / from the same 5m zone at a random bar")
    w(f"{'where the 5m %R is (own terms)':28} | {'share of':>9} | {'5m Supertrend':>13} | {'5m %R closes in the band ahead within':^41} | {'ahead before':>13} | {'price, event direction':^29}")
    w(f"{'':28} | {'events':>9} | {'on side':>13} | {'30 min':>13} {'1 h':>13} {'3 h':>13} | {'behind':>13} | {'next 15 min':>14} {'next 1 h':>14}")
    for nm, lo, hi in ZONES + (("ALL", -100.01, 0.01),):
        e, r = E[(E.u > lo) & (E.u <= hi)], R[(R.u > lo) & (R.u <= hi)]
        if len(e) < 50 or len(r) < 50:
            continue
        pair = lambda f: f"{f(e):5.0f}% /{f(r):4.0f}%"  # noqa: E731
        ab = f"{100 * e.ahead_first.mean():5.0f}% /{100 * r.ahead_first.mean():4.0f}%" if e.ahead_first.notna().sum() > 50 else f"{'-':>13}"
        w(f"{nm:28} | {100 * len(e) / len(E):4.0f}% /{100 * len(r) / len(R):3.0f}% | {pair(lambda d: 100 * d.st5.mean())} | "
          f"{pair(lambda d: 100 * (d.t_hit <= 6).mean())} {pair(lambda d: 100 * (d.t_hit <= 12).mean())} {pair(lambda d: 100 * (d.t_hit <= 36).mean())} | {ab} | "
          f"{100 * e.r15.mean():+6.3f}/{100 * r.r15.mean():+6.3f}% {100 * e.r60.mean():+6.3f}/{100 * r.r60.mean():+6.3f}%")
    for nm, m in (("up events (1m closes above -20)", E.s > 0), ("down events (1m closes below -80)", E.s < 0)):
        e = E[m]
        w(f"   {nm}: 5m already in the band ahead {100 * (e.u >= -20).mean():.0f}%, between the bands {100 * ((e.u > -80) & (e.u < -20)).mean():.0f}%, still in the band behind {100 * (e.u <= -80).mean():.0f}%; "
          f"5m reaches the band ahead within 1 h {100 * (e.t_hit[e.u < -20] <= 12).mean():.0f}% (of those not there yet)")


def main():
    rng = np.random.default_rng(3)
    out = ["WHEN THE 1m %R(140) HAS JUST CLOSED ABOVE -20 (OR BELOW -80): HOW THE 5m BEHAVES. Delta 1m trade candles, no fees."]
    res = {}
    for name, segs in (("U  30 most-traded perps, Aug-Nov 2026, liquid days", segs_u()), ("B  top gainers 2025-26, liquid stretches", segs_b())):
        res[name[0]] = collect(segs, rng)
        report(name, res[name[0]], out)
    txt = "\n".join(out)
    print(txt)
    Path("out/wpr_1m_to_5m.txt").write_text(txt + "\n")
    pd.to_pickle(res, "out/manual/wpr_1m_to_5m.pkl")


if __name__ == "__main__":
    main()
