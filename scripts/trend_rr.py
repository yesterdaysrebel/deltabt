"""Trend strategies built for reward:risk 1:4-1:5 on the large coins (owner 2026-11-12: "option 2" -- many small
losses, a few big winners).

    PYTHONPATH=. python scripts/trend_data.py && PYTHONPATH=. python scripts/trend_rr.py

Data: Binance 1h perpetual candles 2021-01..2026-10 for BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC (out/trend; all
listed on Delta). Signals on closed bars; entry at the NEXT 1h open. Exits walk the 1h bars: a stop and a target in the
same hour count as the stop; a bar opening beyond the stop fills at that open; a target must trade 0.05% through.
Costs: 0.05% fee + 0.02% slippage a side; longs pay 0.03% a day funding, shorts get nothing (conservative).
One position per coin at a time. Long and short. R = net result / the entry-to-stop distance.
FAMILIES
  F1 breakout   4h close beyond the highest high / lowest low of the previous N 4h bars (N 20, 55); stop k x ATR14(4h)
                (k 1, 2); target 4R or 5R; or "trail" = no target, exit on a 4h close beyond the N/2 opposite channel.
                Cap 30 days.
  F2 trend+1h   daily close vs its 50-day mean sets the side; enter on a 1h close beyond the previous 24 h's high
                (long) / low (short); stop at the other side of that 24 h range (0.5%-6% away, else skip); target 4R/5R
                or trail (exit on a 1h close beyond the previous 48 h's opposite extreme). Cap 10 days.
  F3 squeeze    4h Bollinger(20, 2) width in its lowest fifth of the last 100 bars, then a 4h close outside the band;
                stop at the middle band; target 4R/5R or trail (close back past the middle band). Cap 20 days.
METHOD: every cell runs on all years; the best cell PER FAMILY is chosen on DEV 2021-2023 only (mean R, n >= 100) and
judged once on TEST 2024-2026.
PASS BAR (written first): on TEST mean R > 0, t >= 2, and each of 2024, 2025, 2026 positive.
Also: a $1,640 account at 1% risk per trade (up to 10 positions open), compounding by exit date.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import trend_data as TD  # noqa: E402

FEE, SLIP, THR, FUND_DAY = 0.0005, 0.0002, 0.0005, 0.0003
DEV, TEST = (2021, 2022, 2023), (2024, 2025, 2026)


def load(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(TD.OUT / f"{sym}_1h.parquet")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    full = np.arange(df.time.min(), df.time.max() + 3600, 3600)
    df = df.set_index("time").reindex(full)
    df["close"] = df.close.ffill()
    for c in ("open", "high", "low"):
        df[c] = df[c].fillna(df.close)
    return df.rename_axis("time").reset_index()


def walk(h, side, i0, entry, stop, target, cap, trail_exit=None):
    """Walk 1h bars from index i0 (entry at open[i0]). trail_exit(i) -> True when a close at bar i says exit (fill at
    open[i+1]). Returns (exit index, exit price, how)."""
    o, hi, lo, cl = h["o"], h["h"], h["l"], h["c"]
    end = min(i0 + cap, len(o) - 1)
    for i in range(i0, end):
        if side > 0:
            if o[i] <= stop and i > i0:
                return i, o[i], "stop"
            if lo[i] <= stop:
                return i, stop * (1 - SLIP), "stop"
            if target is not None and hi[i] >= target * (1 + THR):
                return i, target, "target"
        else:
            if o[i] >= stop and i > i0:
                return i, o[i], "stop"
            if hi[i] >= stop:
                return i, stop * (1 + SLIP), "stop"
            if target is not None and lo[i] <= target * (1 - THR):
                return i, target, "target"
        if trail_exit is not None and trail_exit(i):
            return i + 1, o[i + 1], "trail"
    return end, cl[end], "time"


def result(side, entry, xp, stop_dist, hours):
    gross = side * (xp / entry - 1)
    cost = (FEE + SLIP) * (1 + xp / entry) + (FUND_DAY * hours / 24 if side > 0 else 0.0)
    return (gross - cost) / stop_dist


def f1(df, N, k, rr):
    h = {"o": df.open.to_numpy(), "h": df.high.to_numpy(), "l": df.low.to_numpy(), "c": df.close.to_numpy()}
    t = df.time.to_numpy()
    g = df.groupby(df.time // 14400)
    b = pd.DataFrame({"o": g.open.first(), "h": g.high.max(), "l": g.low.min(), "c": g.close.last(), "end": g.time.max()})
    tr = np.maximum(b.h - b.l, np.maximum((b.h - b.c.shift()).abs(), (b.l - b.c.shift()).abs()))
    atr = tr.rolling(14).mean().to_numpy()
    up = b.h.shift(1).rolling(N).max().to_numpy()
    dn = b.l.shift(1).rolling(N).min().to_numpy()
    ex_up = b.h.shift(1).rolling(N // 2).max().to_numpy()
    ex_dn = b.l.shift(1).rolling(N // 2).min().to_numpy()
    bc, bend = b.c.to_numpy(), b.end.to_numpy()
    pos1h = {int(x): i for i, x in enumerate(t)}
    bar_of_1h = (t // 14400)
    bidx = {int(x): i for i, x in enumerate(b.index)}
    out, free_at = [], 0
    for j in range(N + 15, len(b) - 1):
        side = 1 if bc[j] > up[j] else -1 if bc[j] < dn[j] else 0
        if not side or not np.isfinite(atr[j]):
            continue
        i0 = pos1h.get(int(bend[j]) + 3600)
        if i0 is None or i0 < free_at:
            continue
        entry = h["o"][i0] * (1 + side * SLIP)
        stop = entry - side * k * atr[j]
        dist = abs(entry - stop) / entry
        target = None if rr is None else entry + side * rr * abs(entry - stop)

        def trail(i, side=side):
            if (t[i] + 3600) % 14400:
                return False
            jj = bidx[int(bar_of_1h[i])]
            return bc[jj] < ex_dn[jj] if side > 0 else bc[jj] > ex_up[jj]
        xi, xp, how = walk(h, side, i0, entry, stop, target, 30 * 24, trail if rr is None else None)
        out.append((t[i0], side, result(side, entry, xp, dist, xi - i0), how, t[min(xi, len(t) - 1)]))
        free_at = xi + 1
    return out


def f2(df, rr):
    h = {"o": df.open.to_numpy(), "h": df.high.to_numpy(), "l": df.low.to_numpy(), "c": df.close.to_numpy()}
    t = df.time.to_numpy()
    c = h["c"]
    day = df.groupby(df.time // 86400).close.last()
    trend = (day > day.rolling(50).mean()).astype(int) * 2 - 1
    trend[day.rolling(50).mean().isna()] = 0
    tr_map = trend.shift(1).to_dict()                                  # yesterday's close decides today
    hh = pd.Series(h["h"]).shift(1).rolling(24).max().to_numpy()
    ll = pd.Series(h["l"]).shift(1).rolling(24).min().to_numpy()
    hh48 = pd.Series(h["h"]).shift(1).rolling(48).max().to_numpy()
    ll48 = pd.Series(h["l"]).shift(1).rolling(48).min().to_numpy()
    out, free_at = [], 0
    for i in range(60 * 24, len(c) - 2):
        side = tr_map.get(int(t[i] // 86400), 0)
        if not side or i + 1 < free_at:
            continue
        if not ((side > 0 and c[i] > hh[i]) or (side < 0 and c[i] < ll[i])):
            continue
        i0 = i + 1
        entry = h["o"][i0] * (1 + side * SLIP)
        stop = ll[i] if side > 0 else hh[i]
        dist = abs(entry - stop) / entry
        if not 0.005 <= dist <= 0.06 or (side > 0 and stop >= entry) or (side < 0 and stop <= entry):
            continue
        target = None if rr is None else entry + side * rr * abs(entry - stop)

        def trail(k, side=side):
            return c[k] < ll48[k] if side > 0 else c[k] > hh48[k]
        xi, xp, how = walk(h, side, i0, entry, stop, target, 10 * 24, trail if rr is None else None)
        out.append((t[i0], side, result(side, entry, xp, dist, xi - i0), how, t[min(xi, len(t) - 1)]))
        free_at = xi + 1
    return out


def f3(df, rr):
    h = {"o": df.open.to_numpy(), "h": df.high.to_numpy(), "l": df.low.to_numpy(), "c": df.close.to_numpy()}
    t = df.time.to_numpy()
    g = df.groupby(df.time // 14400)
    b = pd.DataFrame({"c": g.close.last(), "end": g.time.max()})
    mid = b.c.rolling(20).mean()
    sd = b.c.rolling(20).std()
    width = (4 * sd / mid)
    low_w = width.rolling(100).quantile(0.2)
    bc, bend, m, s = b.c.to_numpy(), b.end.to_numpy(), mid.to_numpy(), sd.to_numpy()
    sq = (width.shift(1) <= low_w.shift(1)).to_numpy()
    pos1h = {int(x): i for i, x in enumerate(t)}
    bar_of_1h = t // 14400
    bidx = {int(x): i for i, x in enumerate(b.index)}
    out, free_at = [], 0
    for j in range(120, len(b) - 1):
        if not sq[j]:
            continue
        side = 1 if bc[j] > m[j] + 2 * s[j] else -1 if bc[j] < m[j] - 2 * s[j] else 0
        if not side:
            continue
        i0 = pos1h.get(int(bend[j]) + 3600)
        if i0 is None or i0 < free_at:
            continue
        entry = h["o"][i0] * (1 + side * SLIP)
        stop = m[j]
        dist = abs(entry - stop) / entry
        if not 0.003 <= dist <= 0.10 or (side > 0 and stop >= entry) or (side < 0 and stop <= entry):
            continue
        target = None if rr is None else entry + side * rr * abs(entry - stop)

        def trail(i, side=side):
            if (t[i] + 3600) % 14400:
                return False
            jj = bidx[int(bar_of_1h[i])]
            return bc[jj] < m[jj] if side > 0 else bc[jj] > m[jj]
        xi, xp, how = walk(h, side, i0, entry, stop, target, 20 * 24, trail if rr is None else None)
        out.append((t[i0], side, result(side, entry, xp, dist, xi - i0), how, t[min(xi, len(t) - 1)]))
        free_at = xi + 1
    return out


def summarise(rows):
    d = pd.DataFrame(rows, columns=["time", "side", "r", "how", "exit", "coin"])
    d["year"] = pd.to_datetime(d.time, unit="s").dt.year
    return d


def stat(d):
    r = d.r.to_numpy()
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 else 0.0
    return r.mean(), t


def account(d, risk=0.01, start=1640.0):
    eq, peak, dd = start, start, 0.0
    for r in d.sort_values("time").r:
        eq *= 1 + risk * r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    return eq, dd


def main() -> None:
    data = {s: load(s) for s in TD.COINS}
    cells = {}
    for N in (20, 55):
        for k in (1.0, 2.0):
            for rr in (4, 5, None):
                cells[("F1 breakout", f"N{N} stop {k}xATR " + (f"target {rr}R" if rr else "trail"))] = (f1, (N, k, rr))
    for rr in (4, 5, None):
        cells[("F2 trend+1h", "target " + (f"{rr}R" if rr else "trail"))] = (f2, (rr,))
        cells[("F3 squeeze", "target " + (f"{rr}R" if rr else "trail"))] = (f3, (rr,))
    res = {}
    for key, (fn, args) in cells.items():
        rows = []
        for s, df in data.items():
            rows += [(*x, s) for x in fn(df, *args)]
        res[key] = summarise(rows)
    print("ALL CELLS, all years 2021-26 (n, mean R, win rate, avg win / avg loss, exits, R by year)")
    for (fam, cell), d in res.items():
        m, t = stat(d)
        w, l_ = d.r[d.r > 0], d.r[d.r <= 0]
        by = " ".join(f"{d.r[d.year == y].mean():+.3f}" for y in range(2021, 2027))
        hw = d.how.value_counts(normalize=True)
        print(f"  {fam:12s} {cell:30s} n {len(d):5d}  {m:+.3f}R t {t:+5.2f}  win {100 * (d.r > 0).mean():3.0f}%  "
              f"{w.mean():+.2f}/{l_.mean():+.2f}R  stop {100 * hw.get('stop', 0):3.0f}% tgt {100 * hw.get('target', 0):3.0f}% "
              f"trail {100 * hw.get('trail', 0):3.0f}% | {by}")
    print("\nCHOSEN ON 2021-23 (best mean R per family, n >= 100), JUDGED ON 2024-26")
    for fam in ("F1 breakout", "F2 trend+1h", "F3 squeeze"):
        cand = [(stat(d[d.year.isin(DEV)])[0], cell, d) for (f, cell), d in res.items() if f == fam and (d.year.isin(DEV)).sum() >= 100]
        dm, cell, d = max(cand, key=lambda x: x[0])
        te = d[d.year.isin(TEST)]
        m, t = stat(te)
        yrs = [te.r[te.year == y].mean() for y in TEST]
        ok = m > 0 and t >= 2 and all(y > 0 for y in yrs)
        eq, dd = account(te)
        print(f"  {fam:12s} {cell:30s} DEV {dm:+.3f}R | TEST n {len(te)} {m:+.3f}R t {t:+.2f}  "
              + " ".join(f"{y}: {v:+.3f}" for y, v in zip(TEST, yrs))
              + f" | $1,640 at 1% risk -> ${eq:,.0f}, drop {100 * dd:.0f}%  {'PASS' if ok else 'fail'}")


def robust() -> None:
    """The group that stood out after the run (picked AFTER seeing all years, so a lead): F1 breakout, 2x-ATR stop,
    fixed 4R / 5R target, N 20 / 55. Per coin, long vs short, double costs, and a $1,640 account at 1% risk."""
    global FEE, SLIP
    data = {s: load(s) for s in TD.COINS}
    for N in (20, 55):
        for rr in (4, 5):
            rows = []
            for s, df in data.items():
                rows += [(*x, s) for x in f1(df, N, 2.0, rr)]
            d = summarise(rows)
            m, t = stat(d)
            print(f"\nN{N} stop 2xATR target {rr}R: n {len(d)}, {m:+.3f}R t {t:+.2f}, win {100 * (d.r > 0).mean():.0f}%")
            print("  by coin: " + "  ".join(f"{c[:-4]} {g.r.mean():+.2f}({len(g)})" for c, g in d.groupby("coin")))
            pos_coins = (d.groupby("coin").r.mean() > 0).sum()
            print(f"  coins positive: {pos_coins}/10 | long {d.r[d.side > 0].mean():+.3f}R ({(d.side > 0).sum()})  "
                  f"short {d.r[d.side < 0].mean():+.3f}R ({(d.side < 0).sum()})")
            streak = max((len(list(g)) for k, g in __import__("itertools").groupby(d.sort_values("time").r <= 0) if k), default=0)
            print(f"  longest losing streak {streak} trades")
            for risk in (0.01, 0.02):
                eq, dd = account(d, risk)
                print(f"  $1,640 at {100 * risk:.0f}% risk, 2021-26 -> ${eq:,.0f}, worst drop {100 * dd:.0f}%")
            ye = d.groupby("year").r.agg(["mean", "count"])
            print("  by year: " + "  ".join(f"{y} {v['mean']:+.3f} ({int(v['count'])})" for y, v in ye.iterrows()))
            FEE, SLIP = 0.0005, 0.0004                                  # double slippage
            rows = []
            for s, df in data.items():
                rows += [(*x, s) for x in f1(df, N, 2.0, rr)]
            d2 = summarise(rows)
            FEE, SLIP = 0.0005, 0.0002
            print(f"  double slippage: {stat(d2)[0]:+.3f}R t {stat(d2)[1]:+.2f}; by year "
                  + " ".join(f"{d2.r[d2.year == y].mean():+.3f}" for y in range(2021, 2027)))


def holdout() -> None:
    """CONFIRMATION on 10 coins never used (trend_data.HOLDOUT). Rule fixed before this run: F1 breakout N55, stop 2x
    ATR14(4h), target 5R (backup: N20, 2x ATR, 4R). PASS: pooled mean R > 0, t >= 2, positive in >= 4 of 6 years."""
    data = {s: load(s) for s in TD.HOLDOUT}
    for N, rr in ((55, 5), (20, 4)):
        rows = []
        for s, df in data.items():
            rows += [(*x, s) for x in f1(df, N, 2.0, rr)]
        d = summarise(rows)
        m, t = stat(d)
        by = {y: d.r[d.year == y].mean() for y in range(2021, 2027)}
        pos = sum(v > 0 for v in by.values())
        ok = m > 0 and t >= 2 and pos >= 4
        print(f"HOLD-OUT N{N} 2xATR {rr}R: n {len(d)}, {m:+.3f}R t {t:+.2f}, win {100 * (d.r > 0).mean():.0f}%, years + {pos}/6 | "
              + " ".join(f"{y} {v:+.3f}" for y, v in by.items()) + f"  {'PASS' if ok else 'fail'}")
        print("  by coin: " + "  ".join(f"{c[:-4]} {g.r.mean():+.2f}({len(g)})" for c, g in d.groupby("coin"))
              + f" | long {d.r[d.side > 0].mean():+.3f} short {d.r[d.side < 0].mean():+.3f}")
        eq, dd = account(d, 0.01)
        print(f"  $1,640 at 1% risk -> ${eq:,.0f}, worst drop {100 * dd:.0f}%")


def money() -> None:
    """N20 / 2x ATR / 4R on all 20 coins (10 found + 10 held out): a $1,640 account by risk per trade, year by year,
    with the most positions open at once (crypto breakouts cluster, so losses do too)."""
    rows = []
    for s in TD.COINS + TD.HOLDOUT:
        rows += [(*x, s) for x in f1(load(s), 20, 2.0, 4)]
    d = summarise(rows).sort_values("time").reset_index(drop=True)
    m, t = stat(d)
    print(f"N20 2xATR 4R, 20 coins 2021-26: n {len(d)} ({len(d) / 5.83:.0f} a year), {m:+.3f}R t {t:+.2f}, win {100 * (d.r > 0).mean():.0f}%")
    for risk in (0.0025, 0.005, 0.01):
        eq, peak, dd, ye = 1640.0, 1640.0, 0.0, {}
        for x in d.itertuples():
            eq *= 1 + risk * x.r
            peak = max(peak, eq)
            dd = max(dd, 1 - eq / peak)
            ye[x.year] = eq
        prev, cells = 1640.0, []
        for y, v in ye.items():
            cells.append(f"{y} {100 * (v / prev - 1):+.0f}%")
            prev = v
        print(f"  risk {100 * risk:.2f}% -> ${eq:>9,.0f}  worst drop {100 * dd:3.0f}% | " + "  ".join(cells))


if __name__ == "__main__":
    money() if "--money" in sys.argv else holdout() if "--holdout" in sys.argv else robust() if "--robust" in sys.argv else main()
