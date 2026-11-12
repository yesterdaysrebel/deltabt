"""Three owner ideas for rule A (2026-11-12): "more leverage i.e more position size", "close it if coin moves
certain % in favour", "cut the position if the price is stuck for couple of hours in a band ... we can use filters".

    PYTHONPATH=. python scripts/fade_owner_exits.py

Rule A picks on Binance 2021-26 (out/binance_paths: 3,608 picks, 1m high/low/close, incl. delisted coins). Same
entry (the day's open), same 2x ATR14% stop, same 24 h time exit; fee 0.05% a side (scripts/fade_families_search).
Only the exit changes, so every idea is PAIRED with the baseline on the same trades.

  favour  close when the 1m LOW reaches entry x (1 - p): filled AT that price (as paper QUICK; optimistic for a
          real trigger on mark). A stop and a target in the same minute count as the stop.
  stall   from hour N on, close at the minute's close when the last N hours' 1m closes all sat inside a band of
          b% of the entry (max - min). Filters: always / only while in profit / only while in loss.
  size    the baseline's R per trade, compounded in time order at risk 3..25% of the balance per trade (two
          picks a day share the same start-of-day balance), no notional cap -- i.e. whatever leverage it takes.

PASS BAR for an exit idea, written before the run: mean R beats the baseline in >= 5 of the 6 years AND the paired
z of (idea - baseline) over all trades is >= 2. Size is not judged: it cannot change R per trade.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_search as S  # noqa: E402

T = 1440
YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")
FEE = S.FEE


def load():
    p = pd.read_parquet(S.OUT / "picks.parquet").reset_index(drop=True)
    a = np.load(S.OUT / "paths.npz")["paths"][:, :, :T].astype(np.float64)
    hi = np.where(np.isfinite(a[:, 0]), a[:, 0], -np.inf)
    lo = np.where(np.isfinite(a[:, 1]), a[:, 1], np.inf)
    cl = pd.DataFrame(a[:, 2]).ffill(axis=1).to_numpy()
    ok = np.isfinite(p.open.to_numpy()) & (p.open.to_numpy() > 0) & np.isfinite(cl[:, T - 1])
    return p[ok].reset_index(drop=True), hi[ok], lo[ok], cl[ok]


def first(mask: np.ndarray) -> np.ndarray:
    """First True column per row; T where none."""
    has = mask.any(axis=1)
    return np.where(has, mask.argmax(axis=1), T)


def simulate(p, hi, lo, cl, extra_exit=None):
    """Net R per trade. extra_exit: (minute array, price array) of a non-stop early exit, or None."""
    entry, dist = p.open.to_numpy(), p.dist.to_numpy()
    stop = entry * (1 + dist)
    s = first(hi >= stop[:, None])
    n = len(p)
    x = np.full(n, T - 1)
    xp = cl[:, T - 1].copy()
    hit = s < T
    xp[hit] = np.maximum(stop[hit], cl[np.arange(n)[hit], s[hit]])
    x[hit] = s[hit]
    if extra_exit is not None:
        m, px = extra_exit
        early = (m < x) | ((m == x) & ~hit & (m < T - 1))
        early &= m < T
        x = np.where(early, m, x)
        xp = np.where(early, px, xp)
    return (-(xp / entry - 1) - FEE - FEE * xp / entry) / dist


def favour(p, lo, frac):
    tgt = p.open.to_numpy() * (1 - frac)
    m = first(lo <= tgt[:, None])
    return m, tgt


def favour_r(p, lo, k):
    tgt = p.open.to_numpy() * (1 - k * p.dist.to_numpy())
    m = first(lo <= tgt[:, None])
    return m, tgt


def stall(p, cl, hours, band, when="always"):
    w = hours * 60
    df = pd.DataFrame(cl.T)
    rng = (df.rolling(w).max() - df.rolling(w).min()).to_numpy().T
    entry = p.open.to_numpy()
    cond = rng <= band * entry[:, None]
    cond[:, :w] = False
    if when == "profit":
        cond &= cl < entry[:, None]
    elif when == "loss":
        cond &= cl > entry[:, None]
    m = first(cond)
    px = cl[np.arange(len(p)), np.minimum(m, T - 1)]
    return m, px


def line(name, r, base, yrs):
    d = r - base
    z = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if d.std() > 0 else 0.0
    per = [r[yrs == y].mean() for y in YEARS]
    better = sum(r[yrs == y].mean() > base[yrs == y].mean() for y in YEARS)
    ok = better >= 5 and z >= 2
    print(f"  {name:46s} {r.mean():+.3f}R  win {100 * (r > 0).mean():3.0f}%  diff {d.mean():+.3f}R z {z:+5.2f}  "
          f"better {better}/6 | " + " ".join(f"{v:+.3f}" for v in per) + f"  {'PASS' if ok else 'fail'}")


def account(p, r, risk, start=1500.0):
    eq, path = start, []
    for _, g in pd.DataFrame({"t": p.time.to_numpy(), "r": r}).groupby("t", sort=True):
        eq = max(eq * (1 + risk * g.r.sum()), 0.0)
        path.append(eq)
    path = np.array(path)
    dd = (1 - path / np.maximum.accumulate(path)).max()
    return path[-1], dd, path


def main() -> None:
    p, hi, lo, cl = load()
    yrs = p.year.to_numpy()
    base = simulate(p, hi, lo, cl)
    print(f"RULE A picks on Binance 2021-26, n {len(p)}; baseline (stop 2x ATR, 24 h) {base.mean():+.3f}R, "
          f"win {100 * (base > 0).mean():.0f}% | by year " + " ".join(f"{y} {base[yrs == y].mean():+.3f}" for y in YEARS))
    print(f"  {'':46s} {'mean':>7s}  {'win':>8s}  {'vs baseline':>18s}  {'':10s} | " + " ".join(f"{y:>6s}" for y in YEARS))

    print("\n1. CLOSE WHEN THE COIN MOVES p% IN OUR FAVOUR")
    for frac in (0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20):
        line(f"take profit at -{100 * frac:.0f}%", simulate(p, hi, lo, cl, favour(p, lo, frac)), base, yrs)
    for k in (0.2, 0.5, 1.0):
        line(f"take profit at {k}x the stop distance" + (" (= QUICK)" if k == 0.2 else ""),
             simulate(p, hi, lo, cl, favour_r(p, lo, k)), base, yrs)

    print("\n2. CLOSE WHEN THE PRICE IS STUCK IN A BAND FOR N HOURS")
    for when in ("always", "profit", "loss"):
        for hours in (2, 3, 4, 6):
            for band in (0.02, 0.03, 0.05):
                line(f"stuck {hours} h in {100 * band:.0f}% ({when})", simulate(p, hi, lo, cl, stall(p, cl, hours, band, when)), base, yrs)

    print("\n3. MORE POSITION SIZE / LEVERAGE (baseline trades, compounded from $1,500, no notional cap)")
    for nm, r in (("rule A (baseline)", base), ("QUICK (0.2x target)", simulate(p, hi, lo, cl, favour_r(p, lo, 0.2)))):
        print(f"  {nm}")
        for risk in (0.03, 0.05, 0.10, 0.15, 0.20, 0.25):
            end, dd, path = account(p, r, risk)
            yr_end = pd.Series(path, index=pd.to_datetime(sorted(set(p.time)), unit="s")).groupby(lambda t: t.year).last()
            ys = " ".join(f"{y}:{v:,.0f}" for y, v in yr_end.items())
            print(f"    risk {100 * risk:4.0f}%  $1,500 -> ${end:12,.0f}  worst drop {100 * dd:3.0f}% | year-end {ys}")


if __name__ == "__main__":
    main()
