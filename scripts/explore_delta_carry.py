"""Funding collection on Delta, built several ways to cut the price noise. Real Delta funding and prices,
coins with >= $250k/day turnover and 45 days of history, 2025-2026. Open exploration, not a result.

    PYTHONPATH=. python scripts/explore_delta_carry.py     (needs scripts/explore_delta_basis.py's funding cache)

Position each day: SHORT coins with high recent funding (shorts are paid), LONG coins with negative funding
(longs are paid). Net = price move + funding received - costs (0.158% fee + 0.10% spread per round trip traded).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as G  # noqa: E402

COST_RT = 0.00158 + 0.0010


def main() -> None:
    d = G.daily()
    d["date"] = pd.to_datetime(d.time, unit="s")
    C = d.pivot(index="date", columns="symbol", values="close").ffill(limit=7)
    V = d.pivot(index="date", columns="symbol", values="turnover")
    F = pd.read_parquet("out/binance_paths/delta_funding_daily.parquet").reindex(index=C.index, columns=C.columns)
    ret = C.pct_change(fill_method=None)
    hist = C.notna().cumsum()
    v30 = V.rolling(30).mean()
    sd30 = ret.rolling(30).std()
    btc = ret["BTCUSD"]
    beta = ret.rolling(60, min_periods=30).cov(btc).div(btc.rolling(60, min_periods=30).var(), axis=0)
    r1, f1 = ret.shift(-1), F.shift(-1)                               # tomorrow's return and funding

    def run(sig, frac=5, floor=2.5e5, thresh=None, invvol=False, hedge=False, skip_move=None, side="both", every=1):
        liquid = (v30 >= floor) & (hist >= 45)
        rows, w_prev = [], pd.Series(dtype=float)
        days = list(C.index[60:-1])
        w = pd.Series(dtype=float)
        for n_, t in enumerate(days):
            if n_ % every == 0:
                u = liquid.loc[t]
                a = sig.loc[t][u].dropna()
                if skip_move is not None:
                    a = a[(ret.loc[t][a.index].abs() < skip_move)]
                if len(a) < 20:
                    w = pd.Series(dtype=float)
                else:
                    if thresh is None:
                        k = max(len(a) // frac, 3)
                        o = a.sort_values()
                        S, Lg = o.index[-k:], o.index[:k]             # short HIGH funding, long LOW funding
                    else:
                        S, Lg = a.index[a > thresh], a.index[a < -thresh]
                    ws = pd.Series(1.0, index=S) if not invvol else 1 / sd30.loc[t][S]
                    wl = pd.Series(1.0, index=Lg) if not invvol else 1 / sd30.loc[t][Lg]
                    ws, wl = ws.replace([np.inf], np.nan).dropna(), wl.replace([np.inf], np.nan).dropna()
                    parts = []
                    if side in ("both", "short") and len(ws) >= 3:
                        parts.append(-ws / ws.sum())
                    if side in ("both", "long") and len(wl) >= 3:
                        parts.append(wl / wl.sum())
                    w = pd.concat(parts) if parts else pd.Series(dtype=float)
                    if hedge and len(w):
                        b = float((w * beta.loc[t][w.index]).sum())
                        if np.isfinite(b):
                            w = pd.concat([w, pd.Series({"__BTC__": -b})])
            if not len(w):
                continue
            core = w.drop("__BTC__", errors="ignore")
            px = float((core * r1.loc[t][core.index]).sum(skipna=True))
            fu = float((-core * f1.loc[t][core.index]).sum(skipna=True))       # a short (negative w) receives positive funding
            if "__BTC__" in w:
                px += w["__BTC__"] * r1.at[t, "BTCUSD"]
                fu += -w["__BTC__"] * f1.at[t, "BTCUSD"]
            trn = float(w.sub(w_prev, fill_value=0).abs().sum()) / 2
            gross_exposure = float(core.abs().sum())
            rows.append((t, px, fu, trn * COST_RT, gross_exposure))
            w_prev = w
        return pd.DataFrame(rows, columns=["t", "price", "fund", "cost", "gross"]).set_index("t")

    def rep(nm, r):
        s = r.price + r.fund - r.cost
        t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
        eq = (1 + s).cumprod()
        print(f"  {nm:52s} n {len(s):3d}  net {100 * s.mean():+6.3f}%/d  t {t:+5.2f}  Sharpe {s.mean() / s.std() * np.sqrt(365):+5.2f}  "
              f"maxDD {100 * (1 - eq / eq.cummax()).max():3.0f}%  worst day {100 * s.min():+6.1f}% | price {100 * r.price.mean():+.3f} "
              f"funding {100 * r.fund.mean():+.3f} cost {100 * r.cost.mean():.3f} | 2025 {100 * s[s.index.year == 2025].mean():+.3f} 2026 {100 * s[s.index.year == 2026].mean():+.3f}")

    F3, F7 = F.rolling(3, min_periods=2).mean(), F.rolling(7, min_periods=5).mean()
    print("FUNDING COLLECTION ON DELTA (net % per day on the gross of one side; both sides equal unless stated)")
    rep("base: fifths on yesterday's funding", run(F))
    rep("thirds", run(F, frac=3))
    rep("3-day mean funding, fifths", run(F3))
    rep("7-day mean funding, fifths", run(F7))
    rep("only |funding| > 10 bp/day", run(F, thresh=0.0010))
    rep("only |funding| > 20 bp/day", run(F, thresh=0.0020))
    rep("only |funding| > 40 bp/day", run(F, thresh=0.0040))
    rep("sized by 1/volatility", run(F, invvol=True))
    rep("BTC-hedged to zero beta", run(F, hedge=True))
    rep("1/volatility + BTC hedge", run(F, invvol=True, hedge=True))
    rep("skip coins that moved > 15% today", run(F, skip_move=0.15))
    rep("skip > 15% + 1/vol + hedge", run(F, skip_move=0.15, invvol=True, hedge=True))
    rep("thirds + skip > 15% + 1/vol + hedge", run(F, frac=3, skip_move=0.15, invvol=True, hedge=True))
    rep("SHORT high-funding only, BTC-hedged", run(F, side="short", hedge=True))
    rep("LONG negative-funding only, BTC-hedged", run(F, side="long", hedge=True))
    rep("floor $1M/day, fifths", run(F, floor=1e6))
    rep("rebalance every 3 days (3-day funding)", run(F3, every=3))
    rep("rebalance weekly (7-day funding)", run(F7, every=7))


if __name__ == "__main__":
    main()
