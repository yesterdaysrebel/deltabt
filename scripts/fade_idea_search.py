"""Idea search for the gainer fade: entry timing, entry condition, holding period, pump shape.

    PYTHONPATH=. python scripts/fade_idea_search.py dev        # explore: Binance 2021-2023 ONLY
    PYTHONPATH=. python scripts/fade_idea_search.py holdout    # the frozen candidates on 2024-2026, once

Needs scripts/fade_idea_paths.py's cache. A trade: short at minute `e` of day D (price = the close of minute e-1;
e = 0 is D's daily open, i.e. rule A), stop = entry x (1 + 2 x ATR14%) checked first on each minute's high and
filled at max(stop, minute close), else exit at the close of minute `T`-1 counted from D 00:00. Net R = (price move
- 0.05% taker each side) / stop distance -- the same arithmetic as scripts/fade_binance_test.py (checked on load).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("out/binance_paths")
FEE, DAY = 0.0005, 1440
DEV, HOLD = ("2021", "2022", "2023"), ("2024", "2025", "2026")


def load():
    p = pd.read_parquet(OUT / "picks.parquet")
    a = np.load(OUT / "paths.npz")["paths"].astype(np.float64)
    hi, lo, cl = a[:, 0], a[:, 1], a[:, 2]
    cl = pd.DataFrame(cl).ffill(axis=1).to_numpy()                   # a quiet minute carries the last close
    hi = np.where(np.isfinite(hi), hi, -np.inf)
    return p, hi, lo, cl


def trade(i, p, hi, cl, e=0, T=DAY):
    """Net R of pick i entered at minute e and held to minute T (None if it cannot be entered)."""
    dist = p.dist.iat[i]
    entry = p.open.iat[i] if e == 0 else cl[i, e - 1]
    if not np.isfinite(entry) or entry <= 0 or not np.isfinite(cl[i, T - 1]):
        return None
    stop = entry * (1 + dist)
    h = hi[i, e:T]
    s = np.nonzero(h >= stop)[0]
    xp = max(stop, cl[i, e + s[0]]) if len(s) else cl[i, T - 1]
    return (-(xp / entry - 1) - FEE - FEE * xp / entry) / dist


def run(idx, p, hi, cl, e=0, T=DAY, cond=None):
    rows = []
    for i in idx:
        if cond is not None and not cond(i):
            continue
        r = trade(i, p, hi, cl, e, T)
        if r is not None:
            rows.append((p.year.iat[i], r))
    return pd.DataFrame(rows, columns=["year", "r"])


def line(name, d, years, n_all):
    if len(d) < 30:
        return f"  {name:44s} n {len(d):4d}  (too few)"
    z = d.r.mean() / (d.r.std(ddof=1) / np.sqrt(len(d)))
    per = " ".join(f"{d[d.year == y].r.mean():+.3f}" if (d.year == y).sum() >= 10 else "   n/a" for y in years)
    return (f"  {name:44s} n {len(d):4d} ({100 * len(d) / n_all:3.0f}%)  mean {d.r.mean():+.3f}  z {z:+5.2f}  "
            f"win {100 * (d.r > 0).mean():3.0f}%  | {per}")


def variants(p, hi, cl):
    """name -> kwargs for run(). The whole search space, fixed here before anything is looked at."""
    rel = lambda i, e: cl[i, e - 1] / p.open.iat[i] - 1              # noqa: E731  move since D's open, at minute e
    v = {"A baseline (00:00, 24 h)": {}}
    for h in (1, 2, 4, 8, 12):
        v[f"enter +{h} h, to day end"] = dict(e=60 * h)
    for h in (1, 2, 4, 8):
        for c in (0.10, 0.25):
            v[f"enter +{h} h if already DOWN >= {c:.2f} stop"] = dict(
                e=60 * h, cond=lambda i, e=60 * h, c=c: rel(i, e) <= -c * p.dist.iat[i])
        for c in (0.10, 0.25, 0.50):
            v[f"enter +{h} h if UP >= {c:.2f} stop"] = dict(
                e=60 * h, cond=lambda i, e=60 * h, c=c: rel(i, e) >= c * p.dist.iat[i])
    v["hold 48 h"] = dict(T=2 * DAY)
    v["hold 72 h"] = dict(T=3 * DAY)
    v["skip a day: enter D+1 00:00, 24 h"] = dict(e=DAY, T=2 * DAY)
    v["D+1 00:00 if D closed UP"] = dict(e=DAY, T=2 * DAY, cond=lambda i: rel(i, DAY) > 0)
    v["D+1 00:00 if D closed DOWN"] = dict(e=DAY, T=2 * DAY, cond=lambda i: rel(i, DAY) <= 0)
    q = p[p.year.isin(DEV)].prev_change.quantile([1 / 3, 2 / 3]).to_numpy()   # cut-offs from dev only
    v["pump size: smallest third"] = dict(cond=lambda i: p.prev_change.iat[i] <= q[0])
    v["pump size: middle third"] = dict(cond=lambda i: q[0] < p.prev_change.iat[i] <= q[1])
    v["pump size: largest third"] = dict(cond=lambda i: p.prev_change.iat[i] > q[1])
    v["rank 1 only"] = dict(cond=lambda i: p["rank"].iat[i] == 1)
    v["rank 2 only"] = dict(cond=lambda i: p["rank"].iat[i] == 2)
    return v


#: Filled in after `dev`, BEFORE `holdout` is ever run (docs/fade_idea_search_prereg.md).
CANDIDATES: list[str] = []


def main() -> None:
    mode = sys.argv[1]
    p, hi, lo, cl = load()
    if "prev_change" not in p:
        pk = pd.concat([pd.read_parquet(f"{d}/picks.parquet")[["symbol", "time", "prev_change"]]
                        for d in ("out/binance", "out/binance_control")])
        p = p.merge(pk.drop_duplicates(["symbol", "time"]), on=["symbol", "time"], how="left")
    chk = np.array([trade(i, p, hi, cl) for i in range(len(p))], dtype=float)
    ok = np.isfinite(chk)
    print(f"simulator check vs fade_binance_test: {int(ok.sum())} trades, max |diff| "
          f"{np.nanmax(np.abs(chk[ok] - p.A.to_numpy()[ok])):.5f} R")
    years = DEV if mode == "dev" else HOLD
    idx = [i for i in range(len(p)) if p.year.iat[i] in years]
    print(f"{mode.upper()}: Binance {years[0]}-{years[-1]}, {len(idx)} rule A picks; net R per trade, by year: {' '.join(years)}")
    v = variants(p, hi, cl)
    names = list(v) if mode == "dev" else ["A baseline (00:00, 24 h)"] + CANDIDATES
    for name in names:
        print(line(name, run(idx, p, hi, cl, **v[name]), years, len(idx)))


if __name__ == "__main__":
    main()
