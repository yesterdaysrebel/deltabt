"""Rule A held longer than 24 h (owner 2026-11-11: "how about holding for more than 24h? maybe 48h?").

    PYTHONPATH=. python scripts/fade_hold_length.py

Same picks, entry, 2x ATR14% stop and fills as rule A; only the exit time moves: 24, 36, 48, 72 h after 00:00.
Binance rule A picks 2021-2026 (3-day 1m paths), and Delta's own picks 2025-26 on the same coins' Binance 1m prices
with Delta's real funding over the days held (a short RECEIVES a positive rate). Open exploration.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_data as FD  # noqa: E402
import fade_families_search as S  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402

DAY = 1440
HOLDS = (24, 36, 48, 72)
YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")


def z(v):
    v = np.asarray(v, float)
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 else np.nan


def show(nm, v, years, yrs):
    v, yrs = np.asarray(v, float), np.asarray(yrs)
    ok = np.isfinite(v)
    per = " ".join(f"{np.nanmean(v[ok & (yrs == y)]):+.3f}" for y in years)
    pos = sum(np.nanmean(v[ok & (yrs == y)]) > 0 for y in years)
    stopped = None
    print(f"  {nm:30s} n {ok.sum():4d}  mean {np.nanmean(v):+.3f}R  z {z(v[ok]):+5.2f}  win {100 * (v[ok] > 0).mean():3.0f}% | {per}  {pos}/{len(years)}")


def main() -> None:
    A, _, _ = S.load()
    p = A.p
    print("BINANCE rule A picks 2021-26 (before funding): net R per trade by exit time")
    stop_share = {}
    for h in HOLDS:
        r, st = [], []
        for i in range(A.n):
            t = A.trade(i, T=h * 60)
            r.append(np.nan if t is None else t[0])
            st.append(np.nan if t is None else float(t[1] < h * 60 - 1))
        stop_share[h] = np.nanmean(st)
        show(f"exit at {h} h (stopped {100 * stop_share[h]:.0f}%)", r, YEARS, p.year.to_numpy())

    print("\nDELTA'S OWN PICKS 2025-26, with Delta's real funding")
    g = G.panel()
    g = g[g.eligible & np.isfinite(g.atrp) & (g.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False])
    g = g.groupby("time").head(2).copy()
    g["dist"] = 2.0 * g.atrp
    g = g[g.dist <= 0.325]
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)
    to_b = lambda s: next((c for c in (s[:-3] + "USDT", "1000" + s[:-3] + "USDT", s[:-3].replace("1000", "", 1) + "USDT") if c in have), None)  # noqa: E731
    g["bsym"] = g.symbol.map(to_b)
    g = g[g.bsym.notna()].merge(bd[["symbol", "time", "open"]].rename(columns={"symbol": "bsym", "open": "bopen"}), on=["bsym", "time"])
    paths = list(FD.POOL.map(lambda x: np.concatenate([FD.path_day(x.bsym, int(x.time) + k * 86400) for k in range(3)], axis=1), g.itertuples()))
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    res = {h: [] for h in HOLDS}
    fres = {h: [] for h in HOLDS}
    yrs = []
    for x, a in zip(g.itertuples(), paths):
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf).astype(float)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not np.isfinite(x.bopen):
            continue
        yrs.append(str(pd.Timestamp(x.time, unit="s").year))
        stop = x.bopen * (1 + x.dist)
        s = np.nonzero(hi >= stop)[0]
        for h in HOLDS:
            T = h * 60
            if not np.isfinite(cl[T - 1]):
                res[h].append(np.nan); fres[h].append(np.nan)
                continue
            if len(s) and s[0] < T:
                xp, held = max(stop, cl[s[0]]), (s[0] + 1) / DAY
            else:
                xp, held = cl[T - 1], T / DAY
            r = (-(xp / x.bopen - 1) - S.FEE - S.FEE * xp / x.bopen) / x.dist
            fund = 0.0
            for k in range(int(np.ceil(held))):
                day = pd.Timestamp(x.time + k * 86400, unit="s")
                f = F.at[day, x.symbol] if day in F.index and x.symbol in F.columns else 0.0
                fund += (0.0 if not np.isfinite(f) else f) * min(1.0, held - k)
            res[h].append(r)
            fres[h].append(r + fund / x.dist)
    yrs = np.array(yrs)
    for h in HOLDS:
        show(f"exit at {h} h, before funding", res[h], ("2025", "2026"), yrs)
        show(f"exit at {h} h, AFTER funding", fres[h], ("2025", "2026"), yrs)


if __name__ == "__main__":
    main()
