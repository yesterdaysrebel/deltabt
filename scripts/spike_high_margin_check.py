"""Owner 2026-11-17: "are you sure about the results here?" -- independent check of scripts/spike_high_margin.py.

Differences from the checked script, on purpose:
  * trades from trade2 (the array implementation), not trade1;
  * the bot's REAL day rules, which spike_high_margin's account did not apply: one trade a day (the first fill wins,
    the other order is cancelled even if the first trade closes before the other would fill) and no orders on a day
    that starts with a position open;
  * liquidation applied per trade by checking the minute highs against the liquidation price, instead of a cap
    on the result;
  * 5,000 random orderings of the same trades: how much of "worst fall" / "under the floor" is the order they came in.

    PYTHONPATH=. python scripts/spike_high_margin_check.py      # -> out/careful/spike_high_margin_check.txt
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
from spike_verify import CELLS, trade2  # noqa: E402

BAL, N20 = 2895.60, 2878.0
MARGINS = (0.20, 0.30, 0.40, 0.50, 0.60)
C = CELLS["C from the fill"]
LIQ_UP = 0.27                          # liquidation ~27% above the entry at 5x with the extra margin (live/spike_live.py)
LIQ_LOSS = (1 + 25 / 49) / 5           # a liquidation loses the margin posted: 30.2% of the position


def trades(days, c, strict):
    rows = []
    for day, ps in days:
        for rank, sym, H, L, Cl in ps:
            a = trade2(H, L, Cl, c, strict)
            if a is None:
                continue
            k, j, ret, how, _ = a
            e = Cl[2] * 1.05
            hh = H[k:j + 1]
            if np.nanmax(hh) >= e * (1 + LIQ_UP) and ret < -LIQ_LOSS:    # price passed liquidation before the stop filled
                ret, how = -LIQ_LOSS, "LIQUIDATED"
            rows.append(dict(day=day, rank=rank, sym=sym, tin=day + pd.Timedelta(minutes=k), tout=day + pd.Timedelta(minutes=j), ret=ret, how=how))
    return pd.DataFrame(rows)


def live_select(d):
    """the bot's rules: no orders if a position is open at 00:00; the day's first fill wins (ties by rank)"""
    keep, open_until = [], pd.Timestamp.min
    for day, g in d.sort_values(["day", "tin", "rank"]).groupby("day", sort=True):
        if open_until >= day:              # still open at 00:00 -> no orders today
            continue
        first = g.iloc[0]
        keep.append(first)
        open_until = first.tout
    return pd.DataFrame(keep)


def path(rets, m, sizes_cap=None):
    bal, peak, mdd, lo, floor_at = BAL, BAL, 0.0, BAL, None
    for i, r in enumerate(rets):
        n = 5 * m * bal * N20 / BAL
        if sizes_cap is not None:
            n = min(n, sizes_cap[i])
        bal += n * r
        peak, lo = max(peak, bal), min(lo, bal)
        mdd = max(mdd, 1 - bal / peak)
        if floor_at is None and bal < 1000:
            floor_at = i
    return bal, lo, mdd, floor_at


def main():
    days = D.load()
    rng = np.random.default_rng(20261117)
    out = [__doc__.split("\n\n")[0], ""]
    w = out.append
    for fill in ("STRICT", "LENIENT"):
        d = trades(days, C, fill == "STRICT")
        s = live_select(d)
        cap = np.array([D.PRODUCTS.get(x, {}).get("max_lev_notional", np.inf) for x in s.sym])
        day_r = s.ret.to_numpy()
        t = day_r.mean() / (day_r.std(ddof=1) / np.sqrt(len(day_r)))
        w(f"===== {fill}: {len(d)} fills, {len(s)} taken under the bot's day rules; liquidated {int((s.how == 'LIQUIDATED').sum())}; "
          f"per taken trade {100 * day_r.mean():+.2f}% of the position (t {t:+.2f}), win {100 * (day_r > 0).mean():.0f}%")
        for m in MARGINS:
            n = N20 * m / 0.20
            bal, lo, mdd, fl = path(day_r, m, cap)
            sh = [path(rng.permutation(day_r), m) for _ in range(5000)]
            falls = np.array([x[2] for x in sh])
            under = np.mean([x[3] is not None for x in sh])
            w(f"  margin {m:.0%}: per trade ${n * day_r.mean():+6.1f} | actual order: lowest ${lo:,.0f}, worst fall {100 * mdd:.0f}%"
              + (f", UNDER $1,000 at trade {fl + 1} ({s.day.iloc[fl]:%Y-%m-%d})" if fl is not None else ", never under $1,000")
              + f" | 5,000 random orders: worst fall median {100 * np.median(falls):.0f}%, 1-in-10 bad luck {100 * np.quantile(falls, 0.9):.0f}%, "
              f"under $1,000 in {100 * under:.0f}%")
    txt = "\n".join(out)
    print(txt)
    Path("out/careful/spike_high_margin_check.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()


def target_check():
    """the $40 / $45 take-profit cells under the same day rules (added after the first run of this file)"""
    days = D.load()
    rng = np.random.default_rng(7)
    out = ["\nTAKE-PROFIT $40 / $45 under the bot's day rules (STRICT, LENIENT per trade; STRICT floor odds over 5,000 orders):"]
    for m in MARGINS:
        n = N20 * m / 0.20
        cells = []
        for usd in (40.0, 45.0):
            c = dict(C, tp=usd / n)
            r = {f: live_select(trades(days, c, f == "STRICT")).ret.to_numpy() for f in ("STRICT", "LENIENT")}
            under = np.mean([path(rng.permutation(r["STRICT"]), m)[3] is not None for _ in range(2000)])
            cells.append(f"${usd:.0f}: {len(r['STRICT'])} taken, win {100 * (r['STRICT'] > 0).mean():.0f}%, "
                         f"${n * r['STRICT'].mean():+.1f} / ${n * r['LENIENT'].mean():+.1f} per trade, under $1,000 in {100 * under:.0f}%")
        out.append(f"  margin {m:.0%}:  " + "  |  ".join(cells))
    return out


if __name__ == "__main__":
    t = target_check()
    print("\n".join(t))
    with open("out/careful/spike_high_margin_check.txt", "a") as f:
        f.write("\n".join(t) + "\n")
