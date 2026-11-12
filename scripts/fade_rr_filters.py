"""Filters that keep fewer rule A trades so a TIGHT stop with a 2:1 target works (owner 2026-11-12: "SL might hit often
with large position but we can work on some filters where we trade less that way keep the win rate and R:R").

    PYTHONPATH=. python scripts/fade_rr_filters.py

Rule A picks on Binance 2021-26 (3,608), REALISTIC fills (entry at the 00:03 close, target 0.1% through, stop at the
worse of the stop and the minute's close, 0.1% spread, 0.05% fee a side). Trades: stop 1x ATR14% / fixed 5% / fixed
8%, target 2x the stop, out at 24 h. R in units of the stop.
Features known at 00:00 (prior day = D-1, from out/binance*/daily.parquet; others out/binance_paths/features.parquet):
  pump      D-1 close / open - 1                        wick     (high - close) / (high - low) of D-1
  ret3      3-day change to D-1 close                   volx     D-1 quote volume / mean of the 7 days before
  brk7      D-1 close vs the highest high of D-8..D-2   atr      ATR14% (stop / 2)
  btc_prev  BTC's D-1 change                            fund     last funding rate before 00:00
  oi_chg    open-interest change on D-1                 ls_acct  long/short account ratio
METHOD (no peeking): thirds and the chosen third come from DEV = 2021-2023 ONLY; then one look at TEST = 2024-2026.
Also every pair of the two best single-feature thirds on DEV.
PASS BAR (written first): TEST mean R > 0 in each of 2024, 2025, 2026 AND TEST t >= 2.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_reward_risk as RR  # noqa: E402

DEV, TEST = ("2021", "2022", "2023"), ("2024", "2025", "2026")


def features(p: pd.DataFrame) -> pd.DataFrame:
    d = pd.concat([pd.read_parquet(f"out/{x}/daily.parquet") for x in ("binance", "binance_control")])
    d = d.drop_duplicates(["symbol", "time"]).sort_values(["symbol", "time"])
    g = d.groupby("symbol")
    d["pump"] = d.close / d.open - 1
    d["wick"] = (d.high - d.close) / (d.high - d.low).replace(0, np.nan)
    d["ret3"] = d.close / g.close.shift(3) - 1
    d["volx"] = d.quote_volume / g.quote_volume.transform(lambda v: v.shift(1).rolling(7, min_periods=4).mean())
    d["brk7"] = d.close / g.high.transform(lambda h: h.shift(1).rolling(7, min_periods=4).max()) - 1
    d["time"] = d.time + 86400                                          # D-1's values, known at D's 00:00
    f = p[["symbol", "time"]].merge(d[["symbol", "time", "pump", "wick", "ret3", "volx", "brk7"]], on=["symbol", "time"], how="left")
    x = pd.read_parquet("out/binance_paths/features.parquet")[["symbol", "time", "btc_prev", "fund_last", "oi_chg", "ls_acct"]]
    f = f.merge(x, on=["symbol", "time"], how="left").rename(columns={"fund_last": "fund"})
    f["atr"] = p.dist.to_numpy() / 2
    return f.drop(columns=["symbol", "time"])


def stats(r, y, years):
    v = [r[y == yy].mean() if (y == yy).any() else np.nan for yy in years]
    t = r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 2 else 0.0
    return r.mean(), t, v


def main() -> None:
    p, hi, lo, cl, entry = RR.load()
    yrs = p.year.to_numpy()
    F = features(p)
    dev, test = np.isin(yrs, DEV), np.isin(yrs, TEST)
    stops = {"1x ATR": p.dist.to_numpy() / 2, "fixed 5%": np.full(len(p), 0.05), "fixed 8%": np.full(len(p), 0.08)}
    for sn, st in stops.items():
        r, k, ok = RR.trade(hi, lo, cl, entry, st, 2, 24)
        r = np.where(ok, r, np.nan)
        need = (1 + (2 * RR.FEE + RR.EXTRA) / np.median(st)) / 3
        m_all, t_all, _ = stats(r[test & ok], yrs[test & ok], TEST)
        print(f"\nSTOP {sn}, TARGET 2:1, 24 h. All picks: DEV {np.nanmean(r[dev]):+.3f}R, TEST {m_all:+.3f}R (t {t_all:+.2f}); "
              f"win needed ~{100 * need:.0f}%, all-picks win {100 * np.nanmean(r[ok] > 0):.0f}%")
        cands = []
        for col in F.columns:
            v = F[col].to_numpy(float)
            okv = np.isfinite(v) & ok
            q1, q2 = np.nanquantile(v[dev & okv], [1 / 3, 2 / 3])
            for name, mask in ((f"{col} low third", v <= q1), (f"{col} mid third", (v > q1) & (v <= q2)), (f"{col} high third", v > q2)):
                m = mask & okv
                if (m & dev).sum() < 60:
                    continue
                cands.append((np.nanmean(r[m & dev]), name, m))
        cands.sort(key=lambda c: -c[0])
        print(f"  {'filter (chosen on 2021-23)':34s} {'DEV R':>7s} {'n/yr':>5s} | {'TEST R':>7s} {'t':>6s} {'win':>4s}  2024   2025   2026")
        best = cands[:6]
        rows = best + [((np.nanmean(r[a[2] & b[2] & dev])), f"{a[1]} & {b[1]}", a[2] & b[2]) for a, b in itertools.combinations(best[:3], 2)]
        for dm, name, m in rows:
            mt, tt, v = stats(r[m & test], yrs[m & test], TEST)
            n_yr = m.sum() / 5.8
            ok_pass = all(x > 0 for x in v) and tt >= 2
            print(f"  {name:34s} {dm:+7.3f} {n_yr:5.0f} | {mt:+7.3f} {tt:+6.2f} {100 * (r[m & test] > 0).mean():3.0f}%  "
                  + " ".join(f"{x:+.3f}" for x in v) + f"  {'PASS' if ok_pass else 'fail'}")


if __name__ == "__main__":
    main()
