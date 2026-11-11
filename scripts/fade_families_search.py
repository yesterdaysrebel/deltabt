"""Second idea search for the gainer fade: thirteen families (docs/fade_families_prereg.md).

    PYTHONPATH=. python scripts/fade_families_search.py dev        # explore: Binance 2021-2023 ONLY
    PYTHONPATH=. python scripts/fade_families_search.py holdout    # the frozen candidates on 2024-2026, once

Needs scripts/fade_idea_paths.py and scripts/fade_families_data.py. Net R = (price move - 0.05% taker each side) /
stop distance, the arithmetic of scripts/fade_binance_test.py (checked on load).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("out/binance_paths")
FEE, DAY = 0.0005, 1440
DEV, HOLD = ("2021", "2022", "2023"), ("2024", "2025", "2026")
RESHAPES_A = {1, 3, 4, 5, 10, 12, 13}                                # families that must also beat rule A


class Book:
    """A pick set with its 1m paths."""

    def __init__(self, picks: pd.DataFrame, paths: np.ndarray):
        self.p = picks.reset_index(drop=True)
        a = paths.astype(np.float64)
        self.hi = np.where(np.isfinite(a[:, 0]), a[:, 0], -np.inf)
        self.lo = np.where(np.isfinite(a[:, 1]), a[:, 1], np.inf)
        self.cl = pd.DataFrame(a[:, 2]).ffill(axis=1).to_numpy()     # a quiet minute carries the last close
        self.n = len(self.p)

    def trade(self, i: int, side: int = -1, e: int = 0, T: int = DAY):
        """(net R, exit minute) of pick i: side -1 short / +1 long, entered at minute e, held to minute T."""
        dist = self.p.dist.iat[i]
        entry = self.p.open.iat[i] if e == 0 else self.cl[i, e - 1]
        if not np.isfinite(entry) or entry <= 0 or not np.isfinite(self.cl[i, T - 1]):
            return None
        if side < 0:
            stop = entry * (1 + dist)
            s = np.nonzero(self.hi[i, e:T] >= stop)[0]
            x, xp = (e + s[0], max(stop, self.cl[i, e + s[0]])) if len(s) else (T - 1, self.cl[i, T - 1])
            return (-(xp / entry - 1) - FEE - FEE * xp / entry) / dist, x
        stop = entry * (1 - dist)
        s = np.nonzero(self.lo[i, e:T] <= stop)[0]
        x, xp = (e + s[0], min(stop, self.cl[i, e + s[0]])) if len(s) else (T - 1, self.cl[i, T - 1])
        return ((xp / entry - 1) - FEE - FEE * xp / entry) / dist, x


def load():
    A = Book(pd.read_parquet(OUT / "picks.parquet"), np.load(OUT / "paths.npz")["paths"])
    f = pd.read_parquet(OUT / "features.parquet")
    A.p = A.p.merge(f, on=["symbol", "time"], how="left")
    sets = {k: Book(pd.read_parquet(OUT / f"set_{k}.parquet"), np.load(OUT / f"set_{k}.npz")["paths"])
            for k in ("losers", "weekly", "volsurge")}
    b = np.load(OUT / "btc.npz")
    btc = {int(d): pd.Series(c).ffill().to_numpy(dtype=float) for d, c in zip(b["days"], b["close"])}
    return A, sets, btc


def btc_ret(btc, day: int, e: int, x: int):
    c = btc.get(int(day))
    if c is None or not np.isfinite(c[max(e - 1, 0)]) or not np.isfinite(c[x]):
        return None
    return c[x] / c[max(e - 1, 0)] - 1


def run(book: Book, years, side=-1, e=0, T=DAY, cond=None, hedge=0.0, btc=None) -> pd.DataFrame:
    rows = []
    for i in range(book.n):
        y = book.p.year.iat[i]
        if y not in years or (cond is not None and not cond(i)):
            continue
        t = book.trade(i, side, e, T)
        if t is None:
            continue
        r, x = t
        if hedge:
            br = btc_ret(btc, book.p.time.iat[i], e, x)
            if br is None:
                continue
            r += hedge * (-side * br - 2 * FEE) / book.p.dist.iat[i]  # the hedge is the opposite side in BTC
        rows.append((y, int(book.p.time.iat[i]), r))
    return pd.DataFrame(rows, columns=["year", "time", "r"])


def stats(d: pd.DataFrame, years) -> dict:
    n = len(d)
    if n < 2:
        return dict(n=n, mean=np.nan, z=np.nan, per={y: (0, np.nan) for y in years}, win=np.nan)
    z = d.r.mean() / (d.r.std(ddof=1) / np.sqrt(n)) if d.r.std(ddof=1) > 0 else np.nan
    per = {y: (int((d.year == y).sum()), d[d.year == y].r.mean() if (d.year == y).any() else np.nan) for y in years}
    return dict(n=n, mean=d.r.mean(), z=z, per=per, win=(d.r > 0).mean())


def variants(A: Book, sets, btc, years):
    """(family, name) -> a function giving the trades DataFrame. The whole search space."""
    p = A.p
    dev = p.year.isin(DEV)
    v = {}

    def third(col):
        q = p.loc[dev & np.isfinite(p[col]), col].quantile([1 / 3, 2 / 3]).to_numpy()
        lo_ = lambda i: np.isfinite(p[col].iat[i]) and p[col].iat[i] <= q[0]   # noqa: E731
        hi_ = lambda i: np.isfinite(p[col].iat[i]) and p[col].iat[i] > q[1]    # noqa: E731
        return lo_, hi_

    a = lambda **k: (lambda: run(A, years, **k))                     # noqa: E731
    v[(0, "rule A (00:00 short, 24 h)")] = a()
    for col, label in (("fund_last", "last funding"), ("fund_day", "D-1 funding sum"), ("taker_share", "taker-buy share"),
                       ("oi_chg", "open-interest change"), ("ls_top", "top-trader long/short"),
                       ("ls_acct", "all-account long/short"), ("taker_ls", "taker long/short volume")):
        lo_, hi_ = third(col)
        v[(1, f"{label}: lowest third")] = a(cond=lo_)
        v[(1, f"{label}: highest third")] = a(cond=hi_)
    v[(1, "last funding < 0")] = a(cond=lambda i: p.fund_last.iat[i] < 0)
    v[(1, "last funding >= 0")] = a(cond=lambda i: p.fund_last.iat[i] >= 0)
    v[(3, "has a spot market")] = a(cond=lambda i: p.has_spot.iat[i] == 1)
    v[(3, "no spot market")] = a(cond=lambda i: p.has_spot.iat[i] == 0)
    lo_, hi_ = third("spot_ratio")
    v[(3, "spot/futures volume: lowest third")] = a(cond=lo_)
    v[(3, "spot/futures volume: highest third")] = a(cond=hi_)
    v[(4, "listed < 30 days")] = a(cond=lambda i: p.age_days.iat[i] < 30)
    v[(4, "listed 30-180 days")] = a(cond=lambda i: 30 <= p.age_days.iat[i] <= 180)
    v[(4, "listed > 180 days")] = a(cond=lambda i: p.age_days.iat[i] > 180)
    v[(5, "rule A + BTC long 1.0x")] = a(hedge=1.0, btc=btc)
    v[(5, "rule A + BTC long 0.5x")] = a(hedge=0.5, btc=btc)
    L = sets["losers"]
    v[(6, "long the top 2 losers")] = lambda: run(L, years, side=+1)
    v[(6, "long losers + BTC short 1.0x")] = lambda: run(L, years, side=+1, hedge=1.0, btc=btc)

    def pair():
        g = run(A, years).groupby("time").r.mean()
        l_ = run(L, years, side=+1).groupby("time").r.mean()
        d = pd.concat([g, l_], axis=1, keys=["g", "l"]).dropna()
        return pd.DataFrame({"year": pd.to_datetime(d.index, unit="s").year.astype(str), "time": d.index,
                             "r": ((d.g + d.l) / 2).to_numpy()})
    v[(7, "pair: short gainers + long losers (per day)")] = pair
    v[(8, "LONG the gainers, 24 h")] = a(side=+1)
    v[(8, "LONG the gainers, 72 h")] = a(side=+1, T=3 * DAY)
    W = sets["weekly"]
    v[(9, "short top 2 weekly gainers")] = lambda: run(W, years)
    v[(9, "weekly gainers, D-1 was a down day")] = lambda: run(W, years, cond=lambda i: W.p.prev_change.iat[i] < 0)
    v[(10, "BTC fell on D-1")] = a(cond=lambda i: p.btc_prev.iat[i] < 0)
    v[(10, "BTC rose on D-1")] = a(cond=lambda i: p.btc_prev.iat[i] >= 0)
    lo_, hi_ = third("btc_prev")
    v[(10, "BTC D-1 change: lowest third")] = a(cond=lo_)
    v[(10, "BTC D-1 change: highest third")] = a(cond=hi_)
    V = sets["volsurge"]
    v[(11, "short the 2 largest volume surges")] = lambda: run(V, years)
    v[(11, "LONG the 2 largest volume surges")] = lambda: run(V, years, side=+1)

    def scale(e, c):
        def f():
            rows = []
            for i in range(A.n):
                y = p.year.iat[i]
                if y not in years:
                    continue
                t0 = A.trade(i)
                if t0 is None:
                    continue
                r = 0.5 * t0[0]
                if A.cl[i, e - 1] / p.open.iat[i] - 1 >= c * p.dist.iat[i]:
                    t1 = A.trade(i, e=e)
                    if t1 is not None:
                        r += 0.5 * t1[0]
                rows.append((y, int(p.time.iat[i]), r))
            return pd.DataFrame(rows, columns=["year", "time", "r"])
        return f
    v[(12, "half at 00:00, half at +2 h if up >= 0.25 stop")] = scale(120, 0.25)
    v[(12, "half at 00:00, half at +1 h if up >= 0.10 stop")] = scale(60, 0.10)
    for h in (4, 8, 12, 16, 20):
        v[(13, f"rule A closed at {h:02d}:00 UTC")] = a(T=60 * h)
    return v


#: Filled in after `dev`, BEFORE `holdout` is ever run (docs/fade_families_prereg.md).
CANDIDATES: list[tuple[int, str]] = []


def main() -> None:
    mode = sys.argv[1]
    A, sets, btc = load()
    chk = np.array([(A.trade(i) or (np.nan,))[0] for i in range(A.n)])
    print(f"simulator check vs fade_binance_test: max |diff| {np.nanmax(np.abs(chk - A.p.A.to_numpy())):.5f} R; "
          f"sets: " + ", ".join(f"{k} {b.n}" for k, b in sets.items()) + f"; btc days {len(btc)}")
    years = DEV if mode == "dev" else HOLD
    v = variants(A, sets, btc, years)
    keys = list(v) if mode == "dev" else [(0, "rule A (00:00 short, 24 h)")] + CANDIDATES
    print(f"{mode.upper()}: Binance {years[0]}-{years[-1]}; net R per trade (family 7: per day); by year {' '.join(years)}")
    base = None
    best: dict[int, tuple[float, str]] = {}
    for fam, name in keys:
        s = stats(v[(fam, name)](), years)
        if fam == 0:
            base = s["mean"]
        per = " ".join(f"{m:+.3f}" if n >= 30 else "   n/a" for n, m in s["per"].values())
        qual = ""
        if mode == "dev" and fam and s["n"] >= 150:
            have = [m for n, m in s["per"].values() if n >= 30]     # a year with < 30 trades is not counted
            ok = len(have) >= 2 and all(m > 0 for m in have) and (fam not in RESHAPES_A or s["mean"] > base)
            if ok and s["mean"] > 0:
                qual = " *"
                if fam not in best or s["z"] > best[fam][0]:
                    best[fam] = (s["z"], name)
        if mode == "holdout" and fam:
            pos = sum(m > 0 for n, m in s["per"].values() if n > 0)
            ok = s["mean"] > 0 and s["z"] >= 2.70 and pos >= 2 and (fam not in RESHAPES_A or s["mean"] > base)
            qual = f"  -> {'PASS' if ok else 'FAIL'}"
        print(f"  {fam:2d} {name:48s} n {s['n']:4d}  mean {s['mean']:+.3f}  z {s['z']:+5.2f}  "
              f"win {100 * s['win']:3.0f}%  | {per}{qual}")
    if mode == "dev":
        print("\nqualifiers (*) and the one candidate per family (highest z):")
        for fam in sorted(best):
            print(f"  ({fam}, {best[fam][1]!r}),   # dev z {best[fam][0]:+.2f}")
        print("families with no qualifier: " + ", ".join(str(f) for f in (1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13) if f not in best))


if __name__ == "__main__":
    main()
