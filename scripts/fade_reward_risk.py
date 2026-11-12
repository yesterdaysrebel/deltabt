"""Reward:risk >= 1 for rule A (owner 2026-11-12: "we are not folowing risk reward ratio in any of them, we risk more
than we make").

    PYTHONPATH=. python scripts/fade_reward_risk.py

Rule A picks on Binance 2021-26 with 3-day 1m paths (out/binance_paths/paths.npz). Stop = s (2x ATR14%, 1x ATR14%,
fixed 5% or 10%); target = 1, 2 or 3 x the stop (reward:risk 1:1, 2:1, 3:1); else out at 24, 48 or 72 h.
First prints rule A's OWN realised reward:risk (no target). Fills: REALISTIC -- entry at the 00:03 close, target must
trade 0.1% through, stop at the worse of the stop and the minute's close, 0.1% spread + 0.05% fee a side.
R is in units of each row's own stop. Break-even win rate = (1 + costs/stop) / (1 + RR) roughly; printed per row.
PASS BAR (written first): mean R > 0 in >= 5 of 6 years and t >= 2 over all trades. 36 rows: expect ~1 false t>2.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_search as S  # noqa: E402

FEE, EXTRA, THROUGH, E = S.FEE, 0.001, 0.001, 3
YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")


def load():
    p = pd.read_parquet(S.OUT / "picks.parquet").reset_index(drop=True)
    a = np.load(S.OUT / "paths.npz")["paths"].astype(np.float64)
    hi = np.where(np.isfinite(a[:, 0]), a[:, 0], -np.inf)
    lo = np.where(np.isfinite(a[:, 1]), a[:, 1], np.inf)
    cl = pd.DataFrame(a[:, 2]).ffill(axis=1).to_numpy()
    entry = cl[:, E - 1]
    ok = np.isfinite(entry) & (entry > 0) & np.isfinite(cl[:, 1439])
    hi, lo = hi[ok], lo[ok]
    hi[:, :E], lo[:, :E] = -np.inf, np.inf
    return p[ok].reset_index(drop=True), hi, lo, cl[ok], entry[ok]


def first(mask, n):
    has = mask.any(axis=1)
    return np.where(has, mask.argmax(axis=1), n)


def trade(hi, lo, cl, entry, stop, rr, hours):
    n = len(entry)
    T = min(hours * 60, cl.shape[1])
    end = np.minimum(T - 1, np.where(np.isfinite(cl[:, T - 1]), T - 1, T - 1))
    sp = entry * (1 + stop)
    ms = first(hi[:, :T] >= sp[:, None], T)
    cand = [(ms, np.maximum(sp, cl[np.arange(n), np.minimum(ms, T - 1)]), 0)]
    if rr:
        tp = entry * (1 - rr * stop)
        cand.append((first(lo[:, :T] <= (tp * (1 - THROUGH))[:, None], T), tp, 1))
    xc = cl[np.arange(n), end]
    cand.append((np.full(n, T - 1), xc, 2))
    M = np.stack([c[0] for c in cand]) * 10 + np.array([c[2] for c in cand])[:, None]
    j = M.argmin(axis=0)
    xp = np.stack([c[1] for c in cand])[j, np.arange(n)]
    k = np.array([c[2] for c in cand])[j]                               # 0 stop, 1 target, 2 time
    ret = -(xp / entry - 1) - FEE - FEE * xp / entry - EXTRA
    ok = np.isfinite(ret)
    return ret / stop, k, ok


def line(name, r, k, yrs, stop, rr):
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))
    pos = sum(r[yrs == y].mean() > 0 for y in YEARS)
    w, l = r[r > 0], r[r <= 0]
    cost = (2 * FEE + EXTRA) / np.median(stop)
    be = (1 + cost) / (1 + rr) if rr else np.nan
    ok = pos >= 5 and t >= 2
    print(f"  {name:34s} {r.mean():+.3f}R t {t:+5.2f}  win {100 * (r > 0).mean():3.0f}%"
          + (f" (need {100 * be:3.0f}%)" if rr else "            ")
          + f"  avg win {w.mean():+.2f}R avg loss {l.mean():+.2f}R  stop/target/time {100 * (k == 0).mean():.0f}/{100 * (k == 1).mean():.0f}/{100 * (k == 2).mean():.0f}%"
          + f"  +yrs {pos}/6 | " + " ".join(f"{r[yrs == y].mean():+.3f}" for y in YEARS) + f"  {'PASS' if ok else 'fail'}")


def main() -> None:
    p, hi, lo, cl, entry = load()
    yrs = p.year.to_numpy()
    atr = p.dist.to_numpy() / 2
    stops = {"2x ATR": 2 * atr, "1x ATR": atr, "fixed 5%": np.full(len(p), 0.05), "fixed 10%": np.full(len(p), 0.10)}
    print(f"RULE A picks Binance 2021-26, n {len(p)}, realistic fills. Median 2x-ATR stop {100 * np.median(2 * atr):.1f}%")
    for sn, st in stops.items():
        print(f"\n  stop {sn}")
        for hours in (24, 48, 72):
            r, k, ok = trade(hi, lo, cl, entry, st, 0, hours)
            line(f"no target, out at {hours} h", r[ok], k[ok], yrs[ok], st, 0)
            for rr in (1, 2, 3):
                r, k, ok = trade(hi, lo, cl, entry, st, rr, hours)
                line(f"target {rr}:1, out at {hours} h", r[ok], k[ok], yrs[ok], st, rr)


def wide() -> None:
    """Owner 2026-11-12: "it should be the other way risk 10 to make 40 or 50" -- reward:risk 4:1 and 5:1."""
    p, hi, lo, cl, entry = load()
    yrs = p.year.to_numpy()
    atr = p.dist.to_numpy() / 2
    stops = {"fixed 2%": np.full(len(p), 0.02), "fixed 3%": np.full(len(p), 0.03), "fixed 5%": np.full(len(p), 0.05),
             "fixed 8%": np.full(len(p), 0.08), "0.5x ATR": 0.5 * atr, "1x ATR": atr}
    print(f"REWARD:RISK 4:1 and 5:1, rule A picks Binance 2021-26, n {len(p)}, realistic fills")
    for sn, st in stops.items():
        print(f"\n  stop {sn}")
        for rr in (4, 5):
            for hours in (24, 48, 72):
                r, k, ok = trade(hi, lo, cl, entry, st, rr, hours)
                line(f"target {rr}:1, out at {hours} h", r[ok], k[ok], yrs[ok], st, rr)


if __name__ == "__main__":
    wide() if "--wide" in sys.argv else main()
