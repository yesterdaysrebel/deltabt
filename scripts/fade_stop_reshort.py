"""Re-short the same coin after rule A's stop (owner 2026-11-11: "how about selling the same coin post SL?").

    PYTHONPATH=. python scripts/fade_stop_reshort.py

After rule A's short is stopped at minute s, SHORT the same coin again by one of these triggers, with the new
stop and exit given. Result in R of the ORIGINAL stop distance (same position size), net of 0.05% taker a side.
Binance rule A picks 2021-2026 (3-day 1m paths); Delta's own picks 2025-26 for the exits that end on day D.
Open exploration, not a pre-registered result.
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


def short_leg(hi, cl, e, entry, dist, T, stop):
    """Short from minute e (entered at `entry`) to minute T-1 with a stop price; net R in original-distance units."""
    T = min(T, cl.shape[0])
    for m in range(e, T):
        if hi[m] >= stop:
            xp = max(stop, cl[m])
            return (-(xp / entry - 1) - FEE - FEE * xp / entry) / dist
    xp = cl[T - 1]
    return (-(xp / entry - 1) - FEE - FEE * xp / entry) / dist


def variants(hi, lo, cl, s, op, dist, span):
    """name -> net R (or NaN if the trigger never fires). `span` = minutes of path available."""
    out = {}
    atr = dist / 2.0
    stop0 = op * (1 + dist)
    fill = max(stop0, cl[s])
    ends = {"day end": DAY, "+24 h": s + DAY, "+48 h": s + 2 * DAY}

    def add(name, e, entry, stop_px, end):
        T = ends[end]
        if e is None or e >= min(T, span) - 1 or not np.isfinite(entry):
            out[f"{name} | {end}"] = np.nan
            return
        out[f"{name} | {end}"] = short_leg(hi, cl, e, entry, dist, T, stop_px)

    for end in ends:
        add("at the stop, new stop 2x ATR above", s + 1, fill, fill * (1 + dist), end)
        add("at the stop, new stop 1x ATR above", s + 1, fill, fill * (1 + atr), end)
        for wait in (60, 240):
            e = s + wait
            if e < span:
                px = cl[e - 1]
                add(f"wait {wait // 60} h, stop 2x ATR above", e, px, px * (1 + dist), end)
        # back under the old stop: the first minute after s whose close is below stop0; stop = the high since s
        b = np.nonzero(cl[s + 1:span] < stop0)[0]
        if len(b):
            e = s + 1 + b[0] + 1
            peak = np.nanmax(hi[s:e])
            add("back under the old stop, stop at the high", e, cl[e - 1], peak * 1.001, end)
            add("back under the old stop, stop 1x ATR above", e, cl[e - 1], cl[e - 1] * (1 + atr), end)
        else:
            out[f"back under the old stop, stop at the high | {end}"] = np.nan
            out[f"back under the old stop, stop 1x ATR above | {end}"] = np.nan
        # half an ATR off the high made since the stop
        run_hi = np.maximum.accumulate(np.where(np.isfinite(hi[s:span]), hi[s:span], -np.inf))
        off = np.nonzero(cl[s:span] <= run_hi * (1 - atr / 2))[0]
        if len(off):
            e = s + off[0] + 1
            add("0.5 ATR off the post-stop high, stop at the high", e, cl[e - 1], run_hi[off[0]] * 1.001, end)
        else:
            out[f"0.5 ATR off the post-stop high, stop at the high | {end}"] = np.nan
    # next day 00:00, only if day D closed above the old stop
    if span >= 2 * DAY and np.isfinite(cl[DAY - 1]) and cl[DAY - 1] > stop0:
        px = cl[DAY - 1]
        out["next day 00:00 if D closed above the stop, 2x ATR stop | 24 h"] = short_leg(hi, cl, DAY, px, dist, 2 * DAY, px * (1 + dist))
    else:
        out["next day 00:00 if D closed above the stop, 2x ATR stop | 24 h"] = np.nan
    return out


def main() -> None:
    A, _, _ = S.load()
    p = A.p
    rows = []
    for i in range(A.n):
        t = A.trade(i)
        if t is None or t[0] > -0.9 or t[1] >= DAY - 1:
            continue
        hi = A.hi[i]
        rows.append(dict(year=p.year.iat[i], **variants(hi, A.lo[i], A.cl[i], t[1], p.open.iat[i], p.dist.iat[i], A.cl.shape[1])))
    d = pd.DataFrame(rows)
    n_all = sum(1 for i in range(A.n) if A.trade(i) is not None)
    print(f"BINANCE rule A picks 2021-26: {n_all} trades, {len(d)} stopped ({100 * len(d) / n_all:.0f}%)")
    print("THE RE-SHORT ALONE (R per re-short taken, original-distance units; positive = the re-short made money)")
    print(f"  {'trigger | exit':66s} {'taken':>5s} {'mean':>7s} {'z':>6s} {'win':>4s} | " + " ".join(f"{y:>6s}" for y in YEARS))
    res = []
    for c in [c for c in d.columns if c != "year"]:
        v = d[c].dropna()
        if len(v) < 30:
            continue
        per = [d.loc[d.year == y, c].mean() for y in YEARS]
        pos = sum(np.isfinite(m) and m > 0 for m in per)
        res.append((c, v.mean()))
        print(f"  {c:66s} {len(v):5d} {v.mean():+7.3f} {z(v):+6.2f} {100 * (v > 0).mean():3.0f}% | "
              + " ".join(f"{m:+6.3f}" if np.isfinite(m) else "   n/a" for m in per) + f"  {pos}/6")
    best, bm = max(res, key=lambda x: x[1])
    base = np.nanmean([(A.trade(i) or (np.nan,))[0] for i in range(A.n)])
    print(f"\n  rule A per trade {base:+.3f}R; adding the best re-short ({best}) adds {d[best].sum() / n_all:+.3f}R per rule A trade")

    print("\nDELTA'S OWN PICKS 2025-26 (exits on day D only; same coins' Binance 1m prices)")
    g = DP.picks()
    gp = DP.G.panel()
    gp = gp[gp.eligible & np.isfinite(gp.atrp) & (gp.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False]).groupby("time").head(2)
    gp["dist"] = 2.0 * gp.atrp
    gp = gp[gp.dist <= 0.325].merge(g[["time", "symbol", "stopped"]], on=["time", "symbol"])
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)
    to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731
    gp["bsym"] = gp.symbol.map(to_b)
    gp = gp[gp.stopped & gp.bsym.notna()].merge(bd[["symbol", "time", "open"]].rename(columns={"symbol": "bsym", "open": "bopen"}), on=["bsym", "time"])
    paths = list(FD.POOL.map(lambda x: FD.path_day(x.bsym, int(x.time)), gp.itertuples()))
    out = []
    for x, a in zip(gp.itertuples(), paths):
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf).astype(float)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf).astype(float)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        s = np.nonzero(hi >= x.bopen * (1 + x.dist))[0]
        if not len(s) or s[0] >= DAY - 1:
            continue
        v = variants(hi, lo, cl, s[0], x.bopen, x.dist, DAY)
        out.append(dict(year=str(pd.Timestamp(x.time, unit="s").year), **{k: v[k] for k in v if k.endswith("| day end")}))
    e = pd.DataFrame(out)
    print(f"  {len(e)} stopped trades")
    for c in [c for c in e.columns if c != "year"]:
        v = e[c].dropna()
        if len(v) < 15:
            continue
        print(f"  {c:66s} {len(v):5d} {v.mean():+7.3f} {z(v):+6.2f} {100 * (v > 0).mean():3.0f}% | "
              f"2025 {e[e.year == '2025'][c].mean():+.3f}  2026 {e[e.year == '2026'][c].mean():+.3f}")


if __name__ == "__main__":
    main()
