"""The quiet-coins lead on coins Delta lists, costed with Delta's fee and each coin's LIVE bid-ask spread
(out/delta_spreads_2026-11-11.json, one snapshot at 11:48 UTC), and cross-checked on Delta's own prices.

    PYTHONPATH=. python scripts/explore_delta_costed.py

Open exploration, not a result. Ranking uses Binance 30-day volume (the global measure); weekly rebalance.
Cost per coin traded = Delta taker 0.079% a side (fee + GST) + half its spread a side.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import explore_wide as W  # noqa: E402
import gainer_fade_2025_lab as G  # noqa: E402

YEARS = W.YEARS
FEE_SIDE = 0.00079


def rep(name, s):
    s = pd.Series(s).dropna()
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    per = [s[s.index.year == int(y)].mean() if (s.index.year == int(y)).sum() >= 8 else np.nan for y in YEARS]
    eq = (1 + s).cumprod()
    cells = " ".join(f"{100 * m:+5.2f}" if np.isfinite(m) else "  n/a" for m in per)
    pos = sum(m > 0 for m in per if np.isfinite(m))
    print(f"  {name:46s} n {len(s):3d}  {100 * s.mean():+6.3f}%/wk  t {t:+5.2f}  Sharpe {s.mean() / s.std() * np.sqrt(52):+5.2f}  "
          f"maxDD {100 * (1 - eq / eq.cummax()).max():3.0f}%  worst {100 * s.min():+6.1f}%  x{eq.iloc[-1]:.1f}  {pos}/{sum(np.isfinite(per))} | {cells}")


def main() -> None:
    snap = json.load(open("out/delta_spreads_2026-11-11.json"))["rows"]
    spread = {r["symbol"]: r["spread"] for r in snap}
    O, H, L, C, V = W.panel()
    C = C.ffill(limit=7)
    ret = C.pct_change(fill_method=None)
    hist = C.notna().cumsum()
    v30 = V.rolling(30).mean()
    dmap = {}
    for s in C.columns:
        for cand in (s[:-4] + "USD", s[:-4].replace("1000", "", 1) + "USD"):
            if cand in spread:
                dmap[s] = cand
                break
    cols = list(dmap)
    sp = pd.Series({s: spread[dmap[s]] for s in cols})
    print(f"{len(cols)} Binance perps Delta lists with a live quote; their spread: median {100 * sp.median():.3f}%, "
          f"90th pct {100 * sp.quantile(.9):.3f}%, worst {100 * sp.max():.2f}%")
    dd = G.daily()
    DC = dd.assign(date=pd.to_datetime(dd.time, unit="s")).pivot(index="date", columns="symbol", values="close").ffill(limit=7)
    chars = {"vol": -np.log(v30), "sd": -ret.rolling(30).std(), "surge": -(V / v30),
             "mom30": C.pct_change(30, fill_method=None), "rev7": -C.pct_change(7, fill_method=None)}

    def run(which, k_side=None, frac=5, floor=5e6, delta_px=False, stress=1.0):
        liquid = ((v30 >= floor) & (hist >= 60))[cols]
        gross, net, prev, held, costs = {}, {}, None, [], []
        for t in C.index[90::7]:
            u = liquid.loc[t]
            Z = pd.DataFrame({k: chars[k].loc[t][cols][u] for k in which})
            if delta_px:
                t2 = t + pd.Timedelta(days=7)
                if t not in DC.index or t2 not in DC.index:
                    continue
                f = pd.Series({s: DC.at[t2, dmap[s]] / DC.at[t, dmap[s]] - 1 for s in Z.index if dmap[s] in DC.columns})
            else:
                f = (C.shift(-7) / C - 1).loc[t][cols][u]
            f = f.reindex(Z.index)
            ok = Z.notna().all(axis=1) & np.isfinite(Z).all(axis=1) & f.notna()
            if ok.sum() < 20:
                continue
            Z, f = Z[ok], f[ok]
            score = ((Z - Z.mean()) / Z.std(ddof=0)).sum(axis=1)
            k = k_side or max(len(score) // frac, 3)
            o = score.sort_values()
            Ls, Ss = list(o.index[-k:]), list(o.index[:k])
            g = f[Ls].mean() - f[Ss].mean()
            new = (set(Ls) | set(Ss)) if prev is None else (set(Ls) - prev[0]) | (set(Ss) - prev[1])
            out = set() if prev is None else (prev[0] - set(Ls)) | (prev[1] - set(Ss))
            c = sum(FEE_SIDE + stress * sp[s] / 2 for s in new) / k + sum(FEE_SIDE + stress * sp[s] / 2 for s in out) / k
            gross[t], net[t] = g, g - c
            costs.append(c)
            held.append(len(score))
            prev = (set(Ls), set(Ss))
        return pd.Series(gross), pd.Series(net), np.mean(costs), np.median(held)

    for title, which in (("QUIET COINS (low 30-day volume only)", ["vol"]),
                         ("FIVE-PART SCORE (volume, volatility, spike, 30-day momentum, 7-day reversal)", ["vol", "sd", "surge", "mom30", "rev7"])):
        g, n, c, m = run(which)
        print(f"\n{title}: Delta-listed coins, ~{int(m)} liquid a week, a fifth each side; mean cost {100 * c:.3f}%/wk")
        rep("gross", g)
        rep("NET of Delta fee + each coin's live spread", n)
        rep("net with spreads 3x wider", run(which, stress=3.0)[1])
        rep("net with spreads 10x wider", run(which, stress=10.0)[1])
        for k in (5, 8):
            rep(f"net, only {k} coins a side", run(which, k_side=k)[1])
        rep("net, thirds", run(which, frac=3)[1])
        rep("net, floor $1M", run(which, floor=1e6)[1])
        gd, nd, _, _ = run(which, delta_px=True)
        gb = g.reindex(gd.index)
        print(f"  on DELTA'S OWN PRICES (2025-26): gross {100 * gd.mean():+.3f}%/wk vs Binance prices same weeks {100 * gb.mean():+.3f}%/wk; "
              f"correlation {gd.corr(gb):.2f}")
        rep("NET on Delta's own prices", nd)


if __name__ == "__main__":
    main()
