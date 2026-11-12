"""Search for strategies with reward:risk >= 2, a decent win rate, low risk and good returns (owner 2026-11-12: "check
more idea's with better R&R, less risk, good win rate and good retruns").

    PYTHONPATH=. python scripts/trend_data.py --holdout && PYTHONPATH=. python scripts/rr_search.py

Coins: the 20 large coins in out/trend (scripts/trend_data COINS + HOLDOUT; Binance 1h, 2021-01..2026-10). A FINAL
check for anything that passes uses 10 coins never touched (FRESH below). Engine as scripts/trend_rr (next-1h-open
entries, stop first within an hour, a gap fills at the open, targets 0.05% through, 0.05% fee + 0.02% slip a side,
longs pay 0.03%/day). Targets are fixed multiples of the stop (RR 2 or 3) unless said otherwise.
FAMILIES (long and short; "uptrend" = yesterday's daily close above its 50-day mean, for longs; below for shorts)
  A pullback   uptrend; the 1h close crosses back above its 20-hour EMA after >= 3 hours below it, with the 14-hour
               RSI under 40 in the last 6 hours; stop under the lowest low of the last 12 hours; cap 3 days
  B dip        uptrend; a 1h close below the lower Bollinger band (20, 2); stop 1.5 x ATR(1h, 14); cap 3 days
  C breakout+  4h close beyond the 20-bar channel, stop 2 x ATR(4h), target 4R (the hold-out pass), ONLY in BTC's
               direction (BTC's daily close vs its 50-day mean); cap 30 days
  D asia       range of 00-08 UTC; the first 1h close beyond it between 08 and 16 UTC; stop at the other side
               (0.5-5% away); out at 24:00 UTC
  E nr7        a day with the narrowest range of the last 7; next day, a 1h close beyond that day's high / low; stop at
               the other side (0.5-8% away); cap 5 days
  F momentum   daily close beyond the previous 20 days' high / low; stop 2 x ATR(daily, 14); target 3R / 4R; cap 30 days
METHOD: per family, the cell (RR, and its one setting) with the best mean R on DEV 2021-2023 (n >= 150) is the
family's pick; it is judged ONCE on TEST 2024-2026. PASS BAR (written first): TEST mean R > 0, t >= 2, each of 2024,
2025, 2026 positive. Then any pass goes to the FRESH coins (same bar, all years).
ACCOUNT: $1,640, 0.5% risk per trade, at most 5 positions open at once (later signals skipped), compounding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import trend_data as TD  # noqa: E402
import trend_rr as E  # noqa: E402

DEV, TEST = (2021, 2022, 2023), (2024, 2025, 2026)
FRESH = ["ICPUSDT", "HBARUSDT", "VETUSDT", "ALGOUSDT", "SANDUSDT", "MANAUSDT", "AXSUSDT", "EGLDUSDT", "THETAUSDT", "XTZUSDT"]


def arrays(df):
    return {"o": df.open.to_numpy(), "h": df.high.to_numpy(), "l": df.low.to_numpy(), "c": df.close.to_numpy()}


def daily_trend(df):
    day = df.groupby(df.time // 86400).close.last()
    ma = day.rolling(50).mean()
    tr = np.sign(day - ma).where(ma.notna(), 0)
    return tr.shift(1).to_dict()                                         # yesterday's close sets today's side


def trade(h, t, side, i0, stop, rr, cap, out, free):
    entry = h["o"][i0] * (1 + side * E.SLIP)
    dist = (entry - stop) / entry * side
    if not dist > 0:
        return free
    target = None if rr is None else entry + side * rr * abs(entry - stop)
    xi, xp, how = E.walk(h, side, i0, entry, stop, target, cap)
    out.append((t[i0], t[min(xi, len(t) - 1)], side, E.result(side, entry, xp, dist, xi - i0), how))
    return xi + 1


def fam_a(df, rr, rsi_lvl=40):
    h, t = arrays(df), df.time.to_numpy()
    c = pd.Series(h["c"])
    ema = c.ewm(span=20, adjust=False).mean().to_numpy()
    d = c.diff()
    rs = d.clip(lower=0).ewm(alpha=1 / 14).mean() / (-d.clip(upper=0)).ewm(alpha=1 / 14).mean()
    rsi = (100 - 100 / (1 + rs)).to_numpy()
    lo12 = pd.Series(h["l"]).rolling(12).min().to_numpy()
    hi12 = pd.Series(h["h"]).rolling(12).max().to_numpy()
    below = (c < ema).astype(int).rolling(3).sum().shift(1).to_numpy()
    above = (c > ema).astype(int).rolling(3).sum().shift(1).to_numpy()
    rmin = pd.Series(rsi).rolling(6).min().to_numpy()
    rmax = pd.Series(rsi).rolling(6).max().to_numpy()
    tr = daily_trend(df)
    out, free = [], 0
    cv = h["c"]
    for i in range(60 * 24, len(t) - 2):
        if i + 1 < free:
            continue
        side = tr.get(int(t[i] // 86400), 0)
        if side > 0 and below[i] == 3 and cv[i] > ema[i] and rmin[i] < rsi_lvl:
            stop = lo12[i] * 0.999
        elif side < 0 and above[i] == 3 and cv[i] < ema[i] and rmax[i] > 100 - rsi_lvl:
            stop = hi12[i] * 1.001
        else:
            continue
        if not 0.004 <= abs(cv[i] / stop - 1) <= 0.06:
            continue
        free = trade(h, t, int(side), i + 1, stop, rr, 72, out, free)
    return out


def fam_b(df, rr, k=1.5):
    h, t = arrays(df), df.time.to_numpy()
    c = pd.Series(h["c"])
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    lo_b, hi_b = (mid - 2 * sd).to_numpy(), (mid + 2 * sd).to_numpy()
    trr = np.maximum(h["h"] - h["l"], np.maximum(abs(h["h"] - np.roll(h["c"], 1)), abs(h["l"] - np.roll(h["c"], 1))))
    atr = pd.Series(trr).rolling(14).mean().to_numpy()
    tr = daily_trend(df)
    out, free = [], 0
    for i in range(60 * 24, len(t) - 2):
        if i + 1 < free:
            continue
        side = tr.get(int(t[i] // 86400), 0)
        if side > 0 and h["c"][i] < lo_b[i]:
            stop = h["o"][i + 1] - k * atr[i]
        elif side < 0 and h["c"][i] > hi_b[i]:
            stop = h["o"][i + 1] + k * atr[i]
        else:
            continue
        free = trade(h, t, int(side), i + 1, stop, rr, 72, out, free)
    return out


_BTC = {}


def btc_trend():
    if not _BTC:
        _BTC.update(daily_trend(E.load("BTCUSDT")))
    return _BTC


def fam_c(df, rr=4):
    rows = E.f1(df, 20, 2.0, rr)
    bt = btc_trend()
    return [(x[0], x[4], x[1], x[2], x[3]) for x in rows if bt.get(int(x[0] // 86400), 0) == x[1]]


def fam_d(df, rr):
    h, t = arrays(df), df.time.to_numpy()
    out, free = [], 0
    days = t // 86400
    starts = np.nonzero(np.r_[True, days[1:] != days[:-1]])[0]
    for s in starts:
        if s + 24 > len(t) - 1 or s < free:
            continue
        rh, rl = h["h"][s:s + 8].max(), h["l"][s:s + 8].min()
        for i in range(s + 8, s + 16):
            side = 1 if h["c"][i] > rh else -1 if h["c"][i] < rl else 0
            if not side:
                continue
            stop = rl if side > 0 else rh
            if 0.005 <= abs(h["c"][i] / stop - 1) <= 0.05 and i + 1 >= free:
                free = trade(h, t, side, i + 1, stop, rr, s + 24 - (i + 1), out, free)
            break
    return out


def fam_e(df, rr):
    h, t = arrays(df), df.time.to_numpy()
    g = df.groupby(df.time // 86400)
    dd = pd.DataFrame({"h": g.high.max(), "l": g.low.min(), "first": g.time.min()})
    rng = dd.h - dd.l
    nr7 = (rng == rng.rolling(7).min()).to_numpy()
    pos = {int(x): i for i, x in enumerate(t)}
    out, free = [], 0
    dh, dl, first = dd.h.to_numpy(), dd.l.to_numpy(), dd["first"].to_numpy()
    for j in range(7, len(dd) - 1):
        if not nr7[j]:
            continue
        s = pos.get(int(first[j + 1]))
        if s is None:
            continue
        for i in range(s, min(s + 24, len(t) - 2)):
            side = 1 if h["c"][i] > dh[j] else -1 if h["c"][i] < dl[j] else 0
            if not side:
                continue
            stop = dl[j] if side > 0 else dh[j]
            if 0.005 <= abs(h["c"][i] / stop - 1) <= 0.08 and i + 1 >= free:
                free = trade(h, t, side, i + 1, stop, rr, 5 * 24, out, free)
            break
    return out


def fam_f(df, rr):
    h, t = arrays(df), df.time.to_numpy()
    g = df.groupby(df.time // 86400)
    dd = pd.DataFrame({"h": g.high.max(), "l": g.low.min(), "c": g.close.last(), "last": g.time.max()})
    trr = np.maximum(dd.h - dd.l, np.maximum((dd.h - dd.c.shift()).abs(), (dd.l - dd.c.shift()).abs()))
    atr = trr.rolling(14).mean().to_numpy()
    up, dn = dd.h.shift(1).rolling(20).max().to_numpy(), dd.l.shift(1).rolling(20).min().to_numpy()
    pos = {int(x): i for i, x in enumerate(t)}
    out, free = [], 0
    for j in range(25, len(dd) - 1):
        side = 1 if dd.c.iat[j] > up[j] else -1 if dd.c.iat[j] < dn[j] else 0
        i0 = pos.get(int(dd["last"].iat[j]) + 3600)
        if not side or i0 is None or i0 < free:
            continue
        stop = h["o"][i0] - side * 2 * atr[j]
        free = trade(h, t, side, i0, stop, rr, 30 * 24, out, free)
    return out


FAMILIES = {
    "A pullback": [(fam_a, (rr, lv), f"RR {rr}, RSI < {lv}") for rr in (2, 3) for lv in (35, 40)],
    "B dip": [(fam_b, (rr, k), f"RR {rr}, stop {k} ATR") for rr in (2, 3) for k in (1.0, 1.5)],
    "C breakout+BTC": [(fam_c, (rr,), f"RR {rr}") for rr in (3, 4)],
    "D asia": [(fam_d, (rr,), f"RR {rr}") for rr in (2, 3)],
    "E nr7": [(fam_e, (rr,), f"RR {rr}") for rr in (2, 3)],
    "F momentum": [(fam_f, (rr,), f"RR {rr}") for rr in (3, 4)],
}


def frame(rows):
    d = pd.DataFrame(rows, columns=["time", "exit", "side", "r", "how", "coin"])
    d["year"] = pd.to_datetime(d.time, unit="s").dt.year
    return d.sort_values("time").reset_index(drop=True)


def account(d, risk=0.005, cap=5, start=1640.0):
    """Take signals in time order while fewer than `cap` are open; compound at each exit."""
    open_until, taken = [], []
    for x in d.itertuples():
        open_until = [e for e in open_until if e > x.time]
        if len(open_until) < cap:
            open_until.append(x.exit)
            taken.append(x)
    eq, peak, dd, ye = start, start, 0.0, {}
    for x in sorted(taken, key=lambda z: z.exit):
        eq *= 1 + risk * x.r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
        ye[pd.Timestamp(x.exit, unit="s").year] = eq
    prev, yr = start, {}
    for y, v in ye.items():
        yr[y] = v / prev - 1
        prev = v
    return eq, dd, yr, len(taken)


def stat(r):
    return r.mean(), (r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 else 0.0)


def run_family(cells, coins):
    data = {s: E.load(s) for s in coins}
    res = {}
    for fn, args, label in cells:
        rows = []
        for s, df in data.items():
            rows += [(*x, s) for x in fn(df, *args)]
        res[label] = frame(rows)
    return res


def main() -> None:
    coins = TD.COINS + TD.HOLDOUT
    picks = {}
    print("ALL CELLS on 20 coins, 2021-26: n, mean R, t, win rate (needed for the RR), avg win / loss, R by year")
    for fam, cells in FAMILIES.items():
        res = run_family(cells, coins)
        for label, d in res.items():
            m, t = stat(d.r)
            rr = float(label.split("RR ")[1].split(",")[0]) if "RR" in label else 3.0
            need = 1 / (1 + rr)
            by = " ".join(f"{d.r[d.year == y].mean():+.3f}" for y in range(2021, 2027))
            print(f"  {fam:15s} {label:20s} n {len(d):5d}  {m:+.3f}R t {t:+5.2f}  win {100 * (d.r > 0).mean():3.0f}% "
                  f"(need {100 * need:2.0f}%)  {d.r[d.r > 0].mean():+.2f}/{d.r[d.r <= 0].mean():+.2f}R | {by}")
        dev = [(d[d.year.isin(DEV)].r.mean(), label, d) for label, d in res.items() if d.year.isin(DEV).sum() >= 150]
        if dev:
            picks[fam] = max(dev, key=lambda x: x[0])
    print("\nPICKED ON 2021-23, JUDGED ON 2024-26 (pass: mean > 0, t >= 2, every test year > 0)")
    passed = []
    for fam, (dm, label, d) in picks.items():
        te = d[d.year.isin(TEST)]
        m, t = stat(te.r)
        yrs = [te.r[te.year == y].mean() for y in TEST]
        ok = m > 0 and t >= 2 and all(v > 0 for v in yrs)
        eq, dd, yr, n = account(d)
        print(f"  {fam:15s} {label:20s} DEV {dm:+.3f} | TEST {m:+.3f}R t {t:+.2f} win {100 * (te.r > 0).mean():.0f}% | "
              + " ".join(f"{y} {v:+.3f}" for y, v in zip(TEST, yrs))
              + f" | account 0.5%, max 5 open: ${eq:,.0f}, drop {100 * dd:.0f}%, "
              + " ".join(f"{y} {100 * v:+.0f}%" for y, v in yr.items()) + f"  {'PASS' if ok else 'fail'}")
        if ok:
            passed.append((fam, label))
    if passed:
        print("\nFRESH COINS (never used): " + ", ".join(c[:-4] for c in FRESH))
        TD.OUT.mkdir(parents=True, exist_ok=True)
        for fam, label in passed:
            cell = [c for c in FAMILIES[fam] if c[2] == label]
            d = run_family(cell, FRESH)[label]
            m, t = stat(d.r)
            by = {y: d.r[d.year == y].mean() for y in range(2021, 2027)}
            ok = m > 0 and t >= 2 and sum(v > 0 for v in by.values() if np.isfinite(v)) >= 4
            eq, dd, yr, n = account(d)
            print(f"  {fam:15s} {label:20s} n {len(d)} {m:+.3f}R t {t:+.2f} win {100 * (d.r > 0).mean():.0f}% | "
                  + " ".join(f"{y} {v:+.3f}" for y, v in by.items())
                  + f" | account ${eq:,.0f} drop {100 * dd:.0f}%  {'PASS' if ok else 'fail'}")


if __name__ == "__main__":
    main()
