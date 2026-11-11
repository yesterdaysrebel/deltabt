"""Tests of the skip filter for rule A (owner 2026-11-11: "run some tests with the rule here and present results").

    PYTHONPATH=. python scripts/fade_filter_tests.py

FILTER: skip a pick if BTC rose more than BTC_CUT on D-1, or the coin's D-1 taker-buy share is above TAKER_CUT.
Cut-offs were fixed from Binance 2021-2023 (the top third of each reading). Exploratory: the filter was found by
open exploration, so 2024-2026 is NOT a clean hold-out for it; these are robustness checks, not a verdict.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_families_data as FD  # noqa: E402
import fade_families_search as S  # noqa: E402
from live.fade import PAPER_MM_PCT, ladder_risk, lever_plan  # noqa: E402

BTC_CUT, TAKER_CUT = 0.0087, 0.5095
YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")
START = 1500.0


def z(v):
    v = np.asarray(v, float)
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 2 else np.nan


def main() -> None:
    A, sets, btc = S.load()
    p = A.p.copy()
    p["r"] = [(A.trade(i) or (np.nan,))[0] for i in range(A.n)]
    p = p[np.isfinite(p.r)].reset_index(drop=True)
    p["keep"] = (p.btc_prev <= BTC_CUT) & (p.taker_share <= TAKER_CUT)
    late = p.year.isin(("2024", "2025", "2026"))

    print("1. YEAR BY YEAR (Binance, rule A trades; net R per trade)")
    print(f"   {'year':6s} {'all':>14s} {'KEPT':>22s} {'SKIPPED':>16s}   kept - skipped")
    for y in YEARS + ("2021-23", "2024-26", "all"):
        m = p.year.eq(y) if y in YEARS else (~late if y == "2021-23" else (late if y == "2024-26" else p.year.notna()))
        a, k, s = p[m], p[m & p.keep], p[m & ~p.keep]
        print(f"   {y:7s} {a.r.mean():+.3f} (n {len(a):4d})   {k.r.mean():+.3f} (n {len(k):4d}, win {100 * (k.r > 0).mean():2.0f}%)   "
              f"{s.r.mean():+.3f} (n {len(s):4d})   {k.r.mean() - s.r.mean():+.3f}")

    print("\n2. EACH CONDITION ALONE, 2024-26 (cut-offs from 2021-23)")
    for nm, c in (("BTC not strong (<= +0.87%)", p.btc_prev <= BTC_CUT), ("not buyer-driven (share <= 0.5095)", p.taker_share <= TAKER_CUT),
                  ("both (the filter)", p.keep), ("either one fails (skipped)", ~p.keep),
                  ("BOTH fail (strong BTC and buyer-driven)", (p.btc_prev > BTC_CUT) & (p.taker_share > TAKER_CUT))):
        k = p[late & c]
        per = " ".join(f"{k[k.year == y].r.mean():+.3f}" for y in ("2024", "2025", "2026"))
        print(f"   {nm:42s} n {len(k):4d}  mean {k.r.mean():+.3f}R  z {z(k.r):+5.2f} | {per}")

    print("\n3. SENSITIVITY: kept trades' mean R on 2024-26 as the two cut-offs move (rows BTC cut, columns taker cut)")
    tc = (0.495, 0.500, 0.505, 0.5095, 0.515, 0.520, 0.530)
    print("   BTC cut \\ taker " + "".join(f"{t:>9.4f}" for t in tc) + "     (share of trades kept at 0.5095)")
    for bc in (-0.01, 0.0, 0.005, 0.0087, 0.015, 0.025, 0.05):
        cells = []
        for t in tc:
            k = p[late & (p.btc_prev <= bc) & (p.taker_share <= t)]
            cells.append(f"{k.r.mean():+9.3f}")
        kept = (late & (p.btc_prev <= bc) & (p.taker_share <= TAKER_CUT)).sum() / late.sum()
        print(f"   {100 * bc:+6.2f}%        " + "".join(cells) + f"     {100 * kept:3.0f}%")
    print(f"   (no filter: {p[late].r.mean():+.3f})")

    print("\n4. WALK-FORWARD CUT-OFFS (each day's cut-offs = the top-third boundaries of all EARLIER picks; first year skipped)")
    q = p.sort_values("time").reset_index(drop=True)
    kb = q.btc_prev.expanding(300).quantile(2 / 3).shift(1)
    kt = q.taker_share.expanding(300).quantile(2 / 3).shift(1)
    wf = (q.btc_prev <= kb) & (q.taker_share <= kt) & kb.notna()
    have = kb.notna()
    for y in YEARS + ("all",):
        m = have & (q.year.eq(y) if y in YEARS else True)
        if m.sum() < 30:
            continue
        k, s = q[m & wf], q[m & ~wf]
        print(f"   {y:5s} kept {k.r.mean():+.3f} (n {len(k):4d})   skipped {s.r.mean():+.3f} (n {len(s):4d})   difference {k.r.mean() - s.r.mean():+.3f}")
    k, s = q[have & wf], q[have & ~wf]
    se = np.sqrt(k.r.var(ddof=1) / len(k) + s.r.var(ddof=1) / len(s))
    print(f"   kept z {z(k.r):+.2f}; kept minus skipped z {(k.r.mean() - s.r.mean()) / se:+.2f}")

    print("\n5. THE ACCOUNT, each year from $1,500 (the bot's sizing, no floor/refills, before funding): year-end balance")
    print(f"   {'year':5s} {'ladder, no filter':>20s} {'ladder + filter':>20s} {'5%, no filter':>18s} {'5% + filter':>16s}   trades kept")

    def account(d, mode):
        eq = hw = START
        worst = 0.0
        for _, g in d.groupby("time"):
            risk = 0.05 if mode == "5%" else ladder_risk(eq, hw)
            pnl = sum(eq * lever_plan(risk, dist, PAPER_MM_PCT)[0] * dist * r for dist, r in zip(g.dist, g.r))
            eq = max(eq + pnl, 0.0)
            hw = max(hw, eq)
            worst = max(worst, 1 - eq / hw)
        return eq, worst
    for y in YEARS:
        d = p[p.year == y].sort_values(["time", "rank"])
        k = d[d.keep]
        cells = []
        for dd, mode in ((d, "ladder"), (k, "ladder"), (d, "5%"), (k, "5%")):
            eq, w = account(dd, mode)
            cells.append(f"${eq:7,.0f} (drop {100 * w:2.0f}%)")
        print(f"   {y:5s} {cells[0]:>20s} {cells[1]:>20s} {cells[2]:>18s} {cells[3]:>16s}   {len(k)}/{len(d)}")

    # ---- 6. Delta's own picks, 2025-26
    print("\n6. DELTA'S OWN PICKS (top 2 gainers on Delta, turnover >= $250k, stops <= 32.5%), 2025-26; each trade")
    print("   simulated on the same coin's Binance 1m prices (Delta and Binance weekly returns correlate 0.998)")
    import gainer_fade_2025_lab as G
    g = G.panel()
    g = g[g.eligible & np.isfinite(g.atrp) & (g.atrp > 0)].sort_values(["time", "prev_change"], ascending=[True, False])
    g = g.groupby("time").head(2).copy()
    g["dist"] = 2.0 * g.atrp
    g = g[g.dist <= 0.325]
    bd = pd.read_parquet("out/binance_control/daily.parquet").drop_duplicates(["symbol", "time"])
    have_b = set(bd.symbol)
    def to_b(s):
        b = s[:-3]
        for c in (b + "USDT", "1000" + b + "USDT", b.replace("1000", "", 1) + "USDT"):
            if c in have_b:
                return c
        return None
    g["bsym"] = g.symbol.map(to_b)
    print(f"   Delta picks {len(g)}; with a Binance perp {int(g.bsym.notna().sum())}")
    g = g[g.bsym.notna()].merge(bd[["symbol", "time", "open"]].rename(columns={"symbol": "bsym", "open": "bopen"}), on=["bsym", "time"], how="left")
    bb = bd[bd.symbol == "BTCUSDT"].set_index("time")
    btc_chg = (bb.close / bb.open - 1).to_dict()
    kl = dict(FD.POOL.map(FD.month_kline, sorted({("fut", s, FD.ymd(t - 86400, "%Y-%m")) for s, t in zip(g.bsym, g.time)})))
    paths = list(FD.POOL.map(lambda x: FD.path_day(x.bsym, int(x.time)), g.itertuples()))
    rows = []
    for x, a in zip(g.itertuples(), paths):
        hi, cl = a[0].astype(float), pd.Series(a[2].astype(float)).ffill().to_numpy()
        if not np.isfinite(x.bopen) or not np.isfinite(cl[-1]):
            continue
        stop = x.bopen * (1 + x.dist)
        s = np.nonzero(np.where(np.isfinite(hi), hi, -np.inf) >= stop)[0]
        xp = max(stop, cl[s[0]]) if len(s) else cl[-1]
        r = (-(xp / x.bopen - 1) - S.FEE - S.FEE * xp / x.bopen) / x.dist
        share = kl.get(("fut", x.bsym, FD.ymd(x.time - 86400, "%Y-%m")), {}).get(int(x.time) - 86400, (np.nan, np.nan))[1]
        rows.append(dict(year=str(pd.Timestamp(x.time, unit="s").year), time=x.time, symbol=x.symbol, r=r,
                         btc_prev=btc_chg.get(int(x.time) - 86400, np.nan), taker=share))
    d = pd.DataFrame(rows).dropna()
    d["keep"] = (d.btc_prev <= BTC_CUT) & (d.taker <= TAKER_CUT)
    for y in ("2025", "2026", "both"):
        m = d.year.eq(y) if y != "both" else d.year.notna()
        a, k, s = d[m], d[m & d.keep], d[m & ~d.keep]
        print(f"   {y:5s} all {a.r.mean():+.3f} (n {len(a)})   KEPT {k.r.mean():+.3f} (n {len(k)}, win {100 * (k.r > 0).mean():.0f}%, z {z(k.r):+.2f})   "
              f"SKIPPED {s.r.mean():+.3f} (n {len(s)})   difference {k.r.mean() - s.r.mean():+.3f}")
    k, s = d[d.keep], d[~d.keep]
    se = np.sqrt(k.r.var(ddof=1) / len(k) + s.r.var(ddof=1) / len(s))
    print(f"   kept minus skipped z {(k.r.mean() - s.r.mean()) / se:+.2f}; kept {100 * len(k) / len(d):.0f}% of Delta's picks")
    rng = np.random.default_rng(0)
    hits = sum(d.r.to_numpy()[rng.choice(len(d), len(k), replace=False)].mean() >= k.r.mean() for _ in range(20000))
    print(f"   a random subset of the same size does at least as well {100 * hits / 20000:.1f}% of the time")


if __name__ == "__main__":
    main()
