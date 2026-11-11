"""Rule A on DELTA'S OWN picks, 2025-26, with Delta's real funding (owner 2026-11-11: "how to improve edge then?").

    PYTHONPATH=. python scripts/fade_delta_picks.py

Picks: top 2 Delta perps by D-1 change (turnover >= $250k, stops <= 32.5%), as the bot picks them. Each trade is
simulated on the same coin's Binance 1m prices (weekly returns correlate 0.998); funding is Delta's own, settled
over D (out/binance_paths/delta_funding_daily.parquet from scripts/explore_delta_basis.py). A short RECEIVES a
positive rate. Readings known at D 00:00: Delta funding of D-1, rank, D-1 change, ATR%, D-1 turnover.
Exploratory: 2025 is shown separately so a reading can be checked on 2026 too.
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


def z(v):
    v = np.asarray(v, float)
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 else np.nan


def picks() -> pd.DataFrame:
    g = G.panel()
    g = g[g.eligible & np.isfinite(g.atrp) & (g.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False])
    g = g.groupby("time").head(2).copy()
    g["rank"] = g.groupby("time").cumcount() + 1
    g["dist"] = 2.0 * g.atrp
    g = g[g.dist <= 0.325]
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have = set(bd.symbol)

    def to_b(s):
        b = s[:-3]
        for c in (b + "USDT", "1000" + b + "USDT", b.replace("1000", "", 1) + "USDT"):
            if c in have:
                return c
        return None
    g["bsym"] = g.symbol.map(to_b)
    g = g[g.bsym.notna()].merge(bd[["symbol", "time", "open"]].rename(columns={"symbol": "bsym", "open": "bopen"}),
                                on=["bsym", "time"], how="left")
    paths = list(FD.POOL.map(lambda x: FD.path_day(x.bsym, int(x.time)), g.itertuples()))
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet")
    rows = []
    for x, a in zip(g.itertuples(), paths):
        hi, cl = a[0].astype(float), pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not np.isfinite(x.bopen) or not np.isfinite(cl[-1]):
            continue
        stop = x.bopen * (1 + x.dist)
        s = np.nonzero(np.where(np.isfinite(hi), hi, -np.inf) >= stop)[0]
        xp = max(stop, cl[s[0]]) if len(s) else cl[-1]
        r = (-(xp / x.bopen - 1) - S.FEE - S.FEE * xp / x.bopen) / x.dist
        day, prev = pd.Timestamp(x.time, unit="s"), pd.Timestamp(x.time - 86400, unit="s")
        f_prev = F.at[prev, x.symbol] if prev in F.index and x.symbol in F.columns else np.nan
        f_day = F.at[day, x.symbol] if day in F.index and x.symbol in F.columns else np.nan
        frac = (s[0] + 1) / 1440 if len(s) else 1.0                   # share of the day the position was open
        rows.append(dict(year=str(day.year), time=x.time, symbol=x.symbol, rank=x.rank, r=r, stopped=bool(len(s)),
                         f_prev=f_prev, fund_r=(f_day * frac / x.dist) if np.isfinite(f_day) else np.nan,
                         prev_change=x.prev_change, atrp=x.atrp, turnover=x.prev_turnover))
    return pd.DataFrame(rows)


def main() -> None:
    d = picks()
    d["r_net"] = d.r + d.fund_r.fillna(0)
    print(f"Delta's picks 2025-26: {len(d)} trades; Delta funding known at entry for {int(d.f_prev.notna().sum())}")
    print(f"  rule A before funding {d.r.mean():+.3f}R | funding received during the trade {d.fund_r.mean():+.3f}R | "
          f"after funding {d.r_net.mean():+.3f}R (z {z(d.r_net):+.2f})")
    for y in ("2025", "2026"):
        k = d[d.year == y]
        print(f"    {y}: before {k.r.mean():+.3f}  funding {k.fund_r.mean():+.3f}  after {k.r_net.mean():+.3f}R  (n {len(k)})")

    def split(nm, col, cuts, labels):
        print(f"\n{nm} (net R after funding; 2025 | 2026 | both)")
        b = pd.cut(d[col], cuts, labels=labels)
        for lab in labels:
            k = d[b == lab]
            per = " | ".join(f"{k[k.year == y].r_net.mean():+.3f} (n {int((k.year == y).sum()):3d})" for y in ("2025", "2026"))
            print(f"  {lab:34s} {per} | {k.r_net.mean():+.3f}  z {z(k.r_net):+5.2f}")

    fp = d.f_prev
    split("1. DELTA FUNDING ON D-1, per day (positive = shorts paid)", "f_prev",
          [-np.inf, -0.002, -0.0005, 0.0, 0.0005, 0.002, np.inf],
          ["< -0.20%/day (shorts pay a lot)", "-0.20% to -0.05%", "-0.05% to 0", "0 to +0.05%", "+0.05% to +0.20%", "> +0.20%/day (shorts paid a lot)"])
    split("2. RANK", "rank", [0, 1, 2], ["rank 1 (biggest gainer)", "rank 2"])
    q = d.prev_change.quantile([1 / 3, 2 / 3]).to_numpy()
    split("3. SIZE OF THE PUMP (D-1 change; thirds)", "prev_change", [-np.inf, q[0], q[1], np.inf], ["smallest third", "middle third", "largest third"])
    q = d.atrp.quantile([1 / 3, 2 / 3]).to_numpy()
    split("4. ATR% (thirds)", "atrp", [-np.inf, q[0], q[1], np.inf], ["calmest third", "middle third", "wildest third"])
    q = d.turnover.quantile([1 / 3, 2 / 3]).to_numpy()
    split("5. D-1 TURNOVER ON DELTA (thirds)", "turnover", [-np.inf, q[0], q[1], np.inf], ["lowest third", "middle third", "highest third"])
    print("\n6. SIMPLE RULES (net R after funding)")
    for nm, m in (("all picks", d.r_net.notna()), ("skip if D-1 funding < 0 (shorts paying)", fp >= 0),
                  ("skip if D-1 funding < -0.05%/day", fp >= -0.0005), ("skip if D-1 funding < -0.20%/day", fp >= -0.002),
                  ("only if D-1 funding > 0", fp > 0)):
        k = d[m]
        print(f"  {nm:42s} kept {100 * len(k) / len(d):3.0f}%  {k[k.year == '2025'].r_net.mean():+.3f} | {k[k.year == '2026'].r_net.mean():+.3f} | "
              f"{k.r_net.mean():+.3f}R  z {z(k.r_net):+5.2f}")


if __name__ == "__main__":
    main()
