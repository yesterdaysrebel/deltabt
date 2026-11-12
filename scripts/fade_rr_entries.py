"""Reward:risk >= 2 via ENTRY TIMING, fewer trades allowed (owner 2026-11-12: "I am ok with taking less trades, just
want to work on risk reward, atleast should be 1:2").

    PYTHONPATH=. python scripts/fade_rr_entries.py

Rule A's picks (Binance 2021-26, 3-day 1m paths) are only a WATCHLIST: no trade at 00:00. During the pick day
(00:00-24:00 UTC) the short is opened only on a trigger, with a STRUCTURE stop just above the high made so far and a
target RR x that distance below the entry (RR 2 or 3). The trade lives up to 24 h after entry. One trade per pick.
Triggers, read on 15-minute closes (entry at that close + 0.05% slippage):
  breakdown N h   after the first N hours (N = 1, 2, 4), the first 15m close below that window's low
  failed push     the coin first trades >= x% above the 00:00 open (x = 3, 5, 10), then a 15m close back below the open
  lower high      after a high made >= 2 h after 00:00, a 15m close below the low of the previous hour while the
                  price is below the day's high by >= 0.5 x ATR14%
Stop = (high since 00:00) x 1.002; skipped when the stop is < 1% or > 15% away. Target must trade 0.1% through;
a stop fills at the worse of the stop and that minute's close; 0.1% spread + 0.05% fee a side.
PASS BAR (written first): RR >= 2, mean R > 0 in >= 5 of 6 years, t >= 2. 22 rows: about one t > 2 by chance.
Also shown: $1,500 at 2% risk per trade, compounding (no leverage limits modelled).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_search as S  # noqa: E402

FEE, EXTRA, SLIP, THROUGH = S.FEE, 0.001, 0.0005, 0.001
YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")
DAY = 1440


def load():
    p = pd.read_parquet(S.OUT / "picks.parquet").reset_index(drop=True)
    a = np.load(S.OUT / "paths.npz")["paths"].astype(np.float64)
    hi = np.where(np.isfinite(a[:, 0]), a[:, 0], -np.inf)
    lo = np.where(np.isfinite(a[:, 1]), a[:, 1], np.inf)
    cl = pd.DataFrame(a[:, 2]).ffill(axis=1).to_numpy()
    ok = np.isfinite(p.open.to_numpy()) & (p.open.to_numpy() > 0) & np.isfinite(cl[:, DAY - 1])
    return p[ok].reset_index(drop=True), hi[ok], lo[ok], cl[ok]


def run_trade(hi, lo, cl, m, entry, stop, rr):
    """Short opened at the close of minute m. Returns R or None."""
    end = min(m + 1 + DAY, cl.shape[0])
    tgt = entry - rr * (stop - entry)
    h, l_ = hi[m + 1:end], lo[m + 1:end]
    s = np.nonzero(h >= stop)[0]
    t = np.nonzero(l_ <= tgt * (1 - THROUGH))[0]
    si = s[0] if len(s) else 10 ** 9
    ti = t[0] if len(t) else 10 ** 9
    if si == 10 ** 9 and ti == 10 ** 9:
        c = cl[end - 1]
        if not np.isfinite(c):
            return None
        xp = c
    elif si <= ti:
        xp = max(stop, cl[m + 1 + si])
    else:
        xp = tgt
    ret = -(xp / entry - 1) - FEE - FEE * xp / entry - EXTRA
    return ret / (stop / entry - 1)


def triggers(o, hi, lo, cl, atr):
    """Yield (name, minute) of each trigger's first 15m close on this path (pick day only)."""
    q = np.arange(14, DAY, 15)
    run_hi = np.maximum.accumulate(np.where(np.isfinite(hi[:DAY]), hi[:DAY], -np.inf))
    out = {}
    for N in (1, 2, 4):
        w = N * 60
        low_w = np.min(lo[:w])
        m = next((i for i in q if i >= w and cl[i] < low_w), None)
        out[f"breakdown {N} h"] = m
    for x in (0.03, 0.05, 0.10):
        up = np.nonzero(run_hi >= o * (1 + x))[0]
        m = None
        if len(up):
            m = next((i for i in q if i > up[0] and cl[i] < o), None)
        out[f"failed push +{int(100 * x)}%"] = m
    m = None
    for i in q:
        if i < 180:
            continue
        hmin = int(np.argmax(run_hi[:i + 1] >= run_hi[i]))
        if hmin < 120 or i - hmin < 60:
            continue
        prev_low = np.min(lo[i - 74:i - 14])
        if cl[i] < prev_low and cl[i] <= run_hi[i] * (1 - 0.5 * atr):
            m = i
            break
    out["lower high"] = m
    return out


def main() -> None:
    p, hi, lo, cl = load()
    yrs = p.year.to_numpy()
    atr = p.dist.to_numpy() / 2
    res = {}
    for i in range(len(p)):
        o = p.open.iat[i]
        trig = triggers(o, hi[i], lo[i], cl[i], atr[i])
        for name, m in trig.items():
            if m is None:
                continue
            entry = cl[i, m] * (1 - SLIP)
            stop = np.max(hi[i, :m + 1]) * 1.002
            d = stop / entry - 1
            if not (0.01 <= d <= 0.15):
                continue
            for rr in (2, 3):
                r = run_trade(hi[i], lo[i], cl[i], m, entry, stop, rr)
                if r is not None:
                    res.setdefault((name, rr), []).append((i, r, d, p.time.iat[i]))
    print(f"RULE A picks as a watchlist, Binance 2021-26 ({len(p)} picks); realistic fills; structure stop above the day's high")
    print(f"  {'trigger':22s} {'RR':>3s} {'trades':>7s} {'per yr':>7s} {'stop':>6s} {'win':>5s} {'need':>5s} {'mean R':>8s} {'t':>6s} "
          f"{'+yrs':>5s}   {'by year':48s} {'$1,500 at 2%':>13s}")
    for (name, rr), rows in sorted(res.items()):
        idx = np.array([x[0] for x in rows])
        r = np.array([x[1] for x in rows])
        d = np.array([x[2] for x in rows])
        y = yrs[idx]
        t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 else 0.0
        by = [r[y == yy].mean() if (y == yy).any() else np.nan for yy in YEARS]
        pos = sum(v > 0 for v in by if np.isfinite(v))
        eq = 1500.0
        for _, g in pd.DataFrame({"t": [x[3] for x in rows], "r": r}).groupby("t", sort=True):
            eq *= 1 + 0.02 * g.r.sum()
        need = (1 + (2 * FEE + EXTRA + SLIP) / np.median(d)) / (1 + rr)
        ok = pos >= 5 and t >= 2
        print(f"  {name:22s} {rr:>2d}:1 {len(r):7d} {len(r) / 5.8:7.0f} {100 * np.median(d):5.1f}% {100 * (r > 0).mean():4.0f}% "
              f"{100 * need:4.0f}% {r.mean():+7.3f}R {t:+6.2f} {pos:>3d}/6   " + " ".join(f"{v:+.3f}" for v in by)
              + f"  ${eq:>11,.0f}  {'PASS' if ok else 'fail'}")


if __name__ == "__main__":
    main()
