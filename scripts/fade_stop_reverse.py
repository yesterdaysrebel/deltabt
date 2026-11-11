"""Stop-and-reverse for rule A (owner 2026-11-11: "if we hit SL we buy the coin? run tests around it").

    PYTHONPATH=. python scripts/fade_stop_reverse.py

When rule A's short is stopped (first 1m high >= stop, filled at max(stop, close)), BUY at that same fill price.
The long's result is in R of the SHORT's stop distance (same position size), net of 0.05% taker each side.
Exits tested: day end; 4 h / 24 h / 48 h after the stop; with no stop, a stop at the short's entry, a stop 1x ATR
below, and a take-profit at +1x ATR. Binance rule A picks 2021-2026 (3-day 1m paths), and Delta's own picks
2025-26 (day-end only). Open exploration, not a pre-registered result.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_delta_picks as DP  # noqa: E402
import fade_families_data as FD  # noqa: E402
import fade_families_search as S  # noqa: E402

FEE, DAY = S.FEE, 1440
YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")


def z(v):
    v = np.asarray(v, float)
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 else np.nan


def long_leg(hi, lo, cl, s, fill, dist, T, sl=None, tp=None):
    """Long from minute s+1 at `fill` to minute T-1; sl/tp prices (or None). Returns net R in short-distance units."""
    for m in range(s + 1, min(T, cl.shape[0])):
        if sl is not None and np.isfinite(lo[m]) and lo[m] <= sl:
            xp = min(sl, cl[m])
            return ((xp / fill - 1) - FEE - FEE * xp / fill) / dist
        if tp is not None and np.isfinite(hi[m]) and hi[m] >= tp:
            return ((tp / fill - 1) - FEE - FEE * tp / fill) / dist
    xp = cl[min(T, cl.shape[0]) - 1]
    return ((xp / fill - 1) - FEE - FEE * xp / fill) / dist


def main() -> None:
    A, _, _ = S.load()
    p = A.p
    rows = []
    for i in range(A.n):
        t = A.trade(i)
        if t is None:
            continue
        r_short, x = t
        if r_short > -0.9 or x >= DAY - 1:                          # only trades that hit the stop
            continue
        dist, op = p.dist.iat[i], p.open.iat[i]
        stop = op * (1 + dist)
        fill = max(stop, A.cl[i, x])
        hi, lo, cl = A.hi[i], A.lo[i], A.cl[i]
        atr = (dist / 2.0) * fill                                    # dist = 2 x ATR14%
        rec = dict(year=p.year.iat[i], short=r_short)
        for nm, T in (("day end", DAY), ("+4 h", x + 240), ("+24 h", x + DAY), ("+48 h", x + 2 * DAY)):
            if T > cl.shape[0] or not np.isfinite(cl[min(T, cl.shape[0]) - 1]):
                continue
            rec[f"{nm} | no stop"] = long_leg(hi, lo, cl, x, fill, dist, T)
            rec[f"{nm} | stop at short entry"] = long_leg(hi, lo, cl, x, fill, dist, T, sl=op)
            rec[f"{nm} | stop 1x ATR below"] = long_leg(hi, lo, cl, x, fill, dist, T, sl=fill - atr)
            rec[f"{nm} | stop 1x ATR, target 1x ATR"] = long_leg(hi, lo, cl, x, fill, dist, T, sl=fill - atr, tp=fill + atr)
        rows.append(rec)
    d = pd.DataFrame(rows)
    allA = np.array([(A.trade(i) or (np.nan,))[0] for i in range(A.n)])
    print(f"BINANCE rule A picks 2021-26: {int(np.isfinite(allA).sum())} trades, {len(d)} stopped "
          f"({100 * len(d) / np.isfinite(allA).sum():.0f}%); the stopped shorts averaged {d.short.mean():+.3f}R")
    print("THE REVERSE LONG ALONE (R per stopped trade, short-distance units; positive = the long made money)")
    print(f"  {'variant':44s} {'n':>5s} {'mean':>7s} {'z':>6s} {'win':>4s} | " + " ".join(f"{y:>6s}" for y in YEARS))
    cols = [c for c in d.columns if "|" in c]
    for c in cols:
        v = d[c].dropna()
        per = " ".join(f"{d.loc[d.year == y, c].mean():+6.3f}" for y in YEARS)
        print(f"  {c:44s} {len(v):5d} {v.mean():+7.3f} {z(v):+6.2f} {100 * (v > 0).mean():3.0f}% | {per}")
    best = max(cols, key=lambda c: d[c].mean())
    tot_before = np.nanmean(allA)
    add = d[best].sum() / np.isfinite(allA).sum()
    print(f"\n  rule A per trade: {tot_before:+.3f}R; adding the best reverse ({best}) changes it by {add:+.3f}R to {tot_before + add:+.3f}R")

    print("\nDELTA'S OWN PICKS 2025-26 (day-end exits only; same coins' Binance 1m prices)")
    g = DP.picks()
    gp = DP.G.panel()
    gp = gp[gp.eligible & np.isfinite(gp.atrp) & (gp.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2)
    gp["dist"] = 2.0 * gp.atrp
    gp = gp[gp.dist <= 0.325].merge(g[["time", "symbol", "r", "stopped"]], on=["time", "symbol"])
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)
    to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731
    gp["bsym"] = gp.symbol.map(to_b)
    gp = gp[gp.stopped & gp.bsym.notna()].merge(bd[["symbol", "time", "open"]].rename(columns={"symbol": "bsym", "open": "bopen"}), on=["bsym", "time"])
    paths = list(FD.POOL.map(lambda x: FD.path_day(x.bsym, int(x.time)), gp.itertuples()))
    out = []
    for x, a in zip(gp.itertuples(), paths):
        hi, lo = np.where(np.isfinite(a[0]), a[0], -np.inf).astype(float), np.where(np.isfinite(a[1]), a[1], np.inf).astype(float)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        stop = x.bopen * (1 + x.dist)
        s = np.nonzero(hi >= stop)[0]
        if not len(s) or s[0] >= DAY - 1:
            continue
        fill = max(stop, cl[s[0]])
        atr = x.atrp * fill
        out.append(dict(year=str(pd.Timestamp(x.time, unit="s").year),
                        nostop=long_leg(hi, lo, cl, s[0], fill, x.dist, DAY),
                        sl_entry=long_leg(hi, lo, cl, s[0], fill, x.dist, DAY, sl=x.bopen),
                        sl_atr=long_leg(hi, lo, cl, s[0], fill, x.dist, DAY, sl=fill - atr),
                        sl_tp=long_leg(hi, lo, cl, s[0], fill, x.dist, DAY, sl=fill - atr, tp=fill + atr)))
    e = pd.DataFrame(out)
    print(f"  {len(e)} stopped trades of {len(g)} picks")
    for c, nm in (("nostop", "day end | no stop"), ("sl_entry", "day end | stop at short entry"),
                  ("sl_atr", "day end | stop 1x ATR below"), ("sl_tp", "day end | stop 1x ATR, target 1x ATR")):
        print(f"  {nm:44s} {len(e):5d} {e[c].mean():+7.3f} {z(e[c]):+6.2f} {100 * (e[c] > 0).mean():3.0f}% | "
              f"2025 {e[e.year == '2025'][c].mean():+.3f}  2026 {e[e.year == '2026'][c].mean():+.3f}")


if __name__ == "__main__":
    main()
