"""OPEN exploration of unpopular angles on the gainer fade (owner 2026-11-11: "look for unpopular ideas",
"run them in different ways instead of hard by the book").

    PYTHONPATH=. python scripts/fade_explore_open.py

NOT a test: no pre-registration, no hold-out, all six years (Binance 2021-2026) looked at together. Anything here
is a LEAD. The only fair check left is year-by-year consistency and then the paper bot going forward.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_search as S  # noqa: E402

YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")
FEE, DAY = S.FEE, S.DAY


def show(name: str, d: pd.DataFrame, unit: str = "R") -> None:
    if len(d) < 40:
        print(f"  {name:52s} n {len(d):4d}  (too few)")
        return
    z = d.r.mean() / (d.r.std(ddof=1) / np.sqrt(len(d)))
    per = [d[d.year == y].r.mean() if (d.year == y).sum() >= 20 else np.nan for y in YEARS]
    pos = sum(m > 0 for m in per if np.isfinite(m))
    have = sum(np.isfinite(m) for m in per)
    cells = " ".join(f"{m:+.3f}" if np.isfinite(m) else "   n/a" for m in per)
    flag = "  <<" if have >= 5 and pos == have and z >= 3 else ("  <" if have >= 5 and pos >= have - 1 and z >= 2.5 else "")
    print(f"  {name:52s} n {len(d):4d}  mean {d.r.mean():+.3f}{unit}  z {z:+5.2f}  {pos}/{have} yrs  | {cells}{flag}")


def main() -> None:
    A, sets, btc = S.load()
    p = A.p
    n = A.n
    print("OPEN EXPLORATION (not a test). Binance 2021-2026, rule A's picks. '<<' = positive every year and z >= 3;")
    print("'<' = positive in all but one year and z >= 2.5. Years: " + " ".join(YEARS))
    base = S.run(A, YEARS)
    show("rule A (00:00 short, 24 h)", base)

    # ---- 1. hour-by-hour drift of the picks (short's gain, in % of price, before fees)
    print("\n1. HOUR BY HOUR: the short's gain per hour, % of price, before fees (a round trip costs ~0.10-0.16%)")
    op = p.open.to_numpy()
    for h in range(24):
        a = op if h == 0 else A.cl[:, 60 * h - 1]
        b = A.cl[:, 60 * (h + 1) - 1]
        d = pd.DataFrame({"year": p.year, "r": -(b / a - 1) * 100}).dropna()
        show(f"{h:02d}:00-{h + 1:02d}:00 UTC", d, "%")

    # ---- 2. re-short after the stop
    print("\n2. RE-SHORT AFTER A STOP (rule A trade stopped at minute s; short again later, 2xATR stop above the new entry)")
    stopped = []
    for i in range(n):
        t = A.trade(i)
        if t is not None and t[1] < DAY - 1 and t[0] < -0.9:
            stopped.append((i, t[1]))
    print(f"  ({len(stopped)} stopped trades)")
    for wait, T, label in ((1, DAY, "+1 min, to day end"), (60, DAY, "+1 h, to day end"), (240, DAY, "+4 h, to day end"),
                           (60, 2 * DAY, "+1 h, to 48 h"), (60, 3 * DAY, "+1 h, to 72 h")):
        rows = []
        for i, s in stopped:
            e = s + wait
            if e >= T - 30:
                continue
            t = A.trade(i, e=e, T=T)
            if t is not None:
                rows.append((p.year.iat[i], t[0]))
        show(f"re-short {label}", pd.DataFrame(rows, columns=["year", "r"]))
    rows = []
    for i, s in stopped:                                             # only if it has come back under the old stop
        for e in range(s + 30, DAY - 60, 30):
            if A.cl[i, e - 1] < p.open.iat[i] * (1 + p.dist.iat[i]):
                t = A.trade(i, e=e, T=2 * DAY)
                if t is not None:
                    rows.append((p.year.iat[i], t[0]))
                break
    show("re-short once back under the old stop, to 48 h", pd.DataFrame(rows, columns=["year", "r"]))

    # ---- 3. repeat picks
    print("\n3. REPEAT PICKS (the coin was also a top-2 pick the day before)")
    seen = set(zip(p.symbol, p.time))
    streak = np.zeros(n, dtype=int)
    for i in range(n):
        k = 0
        while (p.symbol.iat[i], int(p.time.iat[i]) - (k + 1) * 86400) in seen:
            k += 1
        streak[i] = k
    for name, m in (("first day in the top 2", streak == 0), ("second day running", streak == 1), ("third day or more", streak >= 2)):
        show(name, S.run(A, YEARS, cond=lambda i, m=m: m[i]))
        show("  ... LONG instead", S.run(A, YEARS, side=+1, cond=lambda i, m=m: m[i]))

    # ---- 4. day of week
    print("\n4. DAY OF WEEK (the day the short is held)")
    dow = pd.to_datetime(p.time, unit="s").dt.dayofweek.to_numpy()
    for k, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        show(name, S.run(A, YEARS, cond=lambda i, k=k: dow[i] == k))
    show("Sat+Sun", S.run(A, YEARS, cond=lambda i: dow[i] >= 5))
    show("Mon-Fri", S.run(A, YEARS, cond=lambda i: dow[i] < 5))

    # ---- 5. regime flip and stacked conditions (cut-offs from ALL years: exploratory)
    print("\n5. REGIME (cut-offs from all years -- exploratory)")
    q = lambda c, a: p[c].quantile(a)                                # noqa: E731
    bad_btc = (p.btc_prev > q("btc_prev", 2 / 3)).to_numpy()
    bad_tak = (p.taker_share > q("taker_share", 2 / 3)).to_numpy()
    good_ls = (p.ls_acct > q("ls_acct", 2 / 3)).to_numpy()
    bad_ls = (p.ls_acct <= q("ls_acct", 1 / 3)).to_numpy()
    show("short: BTC NOT in its top third yesterday", S.run(A, YEARS, cond=lambda i: not bad_btc[i]))
    show("short: taker-buy share NOT in its top third", S.run(A, YEARS, cond=lambda i: not bad_tak[i]))
    show("short: neither of the two", S.run(A, YEARS, cond=lambda i: not bad_btc[i] and not bad_tak[i]))
    show("short: neither, and most accounts long", S.run(A, YEARS, cond=lambda i: not bad_btc[i] and not bad_tak[i] and good_ls[i]))
    show("LONG: BTC in its top third yesterday", S.run(A, YEARS, side=+1, cond=lambda i: bad_btc[i]))
    show("LONG: taker-buy share in its top third", S.run(A, YEARS, side=+1, cond=lambda i: bad_tak[i]))
    show("LONG: both", S.run(A, YEARS, side=+1, cond=lambda i: bad_btc[i] and bad_tak[i]))
    show("LONG: fewest accounts long", S.run(A, YEARS, side=+1, cond=lambda i: bad_ls[i]))
    show("flip: short unless BTC top third, then long", pd.concat([
        S.run(A, YEARS, cond=lambda i: not bad_btc[i]), S.run(A, YEARS, side=+1, cond=lambda i: bad_btc[i])]))

    # ---- 6. where in its own day the coin peaks
    print("\n6. FIRST HOUR SHAPE (at 01:00, vs the 00:00 open, in stop distances) -> short from 01:00 to day end")
    rel = (A.cl[:, 59] / op - 1) / p.dist.to_numpy()
    hi1 = (np.max(A.hi[:, :60], axis=1) / op - 1) / p.dist.to_numpy()
    for name, m in (("spiked >= 0.25 then gave half back", (hi1 >= 0.25) & (rel <= hi1 / 2)),
                    ("spiked >= 0.25 and still at the high", (hi1 >= 0.25) & (rel > 0.8 * hi1)),
                    ("flat first hour (|move| < 0.05)", np.abs(rel) < 0.05),
                    ("down >= 0.25 in the first hour", rel <= -0.25)):
        show(name, S.run(A, YEARS, e=60, cond=lambda i, m=m: m[i]))


if __name__ == "__main__":
    main()
