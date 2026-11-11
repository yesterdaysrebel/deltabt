"""A composite of the characteristics that came through the weekly regression (scripts/explore_lit.py), tested
two ways: equal weights (nothing fitted) and walk-forward weights (each week uses only earlier weeks).

    PYTHONPATH=. python scripts/explore_composite.py

Open exploration, Binance perps 2021-2026; long the top fifth, short the bottom fifth, weekly. NOT a result.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import explore_wide as W  # noqa: E402

YEARS = W.YEARS


def rep(name, s, extra=""):
    s = pd.Series(s).dropna()
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    per = [s[s.index.year == int(y)].mean() if (s.index.year == int(y)).sum() >= 8 else np.nan for y in YEARS]
    eq = (1 + s).cumprod()
    dd = (1 - eq / eq.cummax()).max()
    cells = " ".join(f"{100 * m:+5.2f}" if np.isfinite(m) else "  n/a" for m in per)
    print(f"  {name:50s} n {len(s):3d}  {100 * s.mean():+6.3f}%/wk  t {t:+5.2f}  Sharpe {s.mean() / s.std() * np.sqrt(52):+5.2f}  "
          f"maxDD {100 * dd:3.0f}%  worst {100 * s.min():+6.1f}%{extra} | {cells}{W.flag(per, t)}")


def main() -> None:
    O, H, L, C, V = W.panel()
    C = C.ffill(limit=7)
    ret = C.pct_change(fill_method=None)
    hist = C.notna().cumsum()
    v30 = V.rolling(30).mean()
    chars = {"vol": -np.log(v30), "sd": -ret.rolling(30).std(), "surge": -(V / v30),
             "mom30": C.pct_change(30, fill_method=None), "rev7": -C.pct_change(7, fill_method=None)}
    fwd = C.shift(-7) / C - 1
    try:
        import gainer_fade_2025_lab as G
        dl = set(G.daily().symbol.unique())
        on_delta = [s for s in C.columns if any(x in dl for x in (s[:-4] + "USD", s[:-4].replace("1000", "", 1) + "USD"))]
    except Exception:                                                # noqa: BLE001
        on_delta = []

    def run(cols, floor, which, wf=False, frac=5, cap=None, lag=0):
        liquid = ((v30 >= floor) & (hist >= 60))[cols]
        f_all = (C.shift(-7 - lag) / C.shift(-lag) - 1)[cols] if lag else fwd[cols]
        rows, turn, prev, hist_coef = {}, [], None, []
        for t in C.index[90::7]:
            u = liquid.loc[t]
            Z = pd.DataFrame({k: chars[k].loc[t][cols][u] for k in which})
            f = f_all.loc[t][u]
            ok = Z.notna().all(axis=1) & np.isfinite(Z).all(axis=1) & f.notna()
            if ok.sum() < 30:
                continue
            Z, f = Z[ok], f[ok]
            Z = (Z - Z.mean()) / Z.std(ddof=0)
            if wf:
                w = np.mean(hist_coef, axis=0) if len(hist_coef) >= 26 else None   # needs half a year of history
                A = np.column_stack([np.ones(len(Z)), Z.to_numpy()])
                coef_now = np.linalg.lstsq(A, f.clip(-0.5, 0.5).to_numpy(), rcond=None)[0][1:]
                if w is None:
                    hist_coef.append(coef_now)
                    continue
                score = pd.Series(Z.to_numpy() @ w, index=Z.index)
                hist_coef.append(coef_now)                           # appended AFTER use: this week's return is not known
            else:
                score = Z.sum(axis=1)
            k = max(len(score) // frac, 3)
            o = score.sort_values()
            Ls, Ss = list(o.index[-k:]), list(o.index[:k])
            ff = f.clip(-cap, cap) if cap else f
            rows[t] = ff[Ls].mean() - ff[Ss].mean()
            if prev is not None:
                turn.append((1 - len(set(Ls) & prev[0]) / k + 1 - len(set(Ss) & prev[1]) / k) / 2)
            prev = (set(Ls), set(Ss))
        return pd.Series(rows), float(np.mean(turn)) if turn else np.nan

    allc = list(C.columns)
    print("EQUAL-WEIGHT SCORE (nothing fitted): long the top fifth, short the bottom fifth, weekly, gross")
    for nm, which in (("low volume", ["vol"]), ("low volatility", ["sd"]), ("no volume spike", ["surge"]),
                      ("low volume + low volatility", ["vol", "sd"]), ("low volume + no spike", ["vol", "surge"]),
                      ("volume + volatility + spike", ["vol", "sd", "surge"]),
                      ("all five (+ 30-day momentum, 7-day reversal)", ["vol", "sd", "surge", "mom30", "rev7"])):
        s, tn = run(allc, 5e6, which)
        rep(nm, s, f"  turn {100 * tn:3.0f}%")
    five = ["vol", "sd", "surge", "mom30", "rev7"]
    print("\nALL FIVE, DIFFERENT WAYS")
    for nm, kw in (("coin moves capped at +-25%", dict(cap=0.25)), ("entered one day late", dict(lag=1)),
                   ("thirds", dict(frac=3)), ("tenths", dict(frac=10)), ("floor $1M", dict(floor=1e6)),
                   ("floor $20M", dict(floor=2e7)), ("floor $50M", dict(floor=5e7))):
        kw = dict(dict(floor=5e6), **kw)
        s, tn = run(allc, which=five, **kw)
        rep(nm, s, f"  turn {100 * tn:3.0f}%")
    s, tn = run(allc, 5e6, five)
    cost = 2 * tn * 0.0016                                           # both legs, 0.16% a round trip
    rep(f"net of fees at {100 * tn:.0f}% weekly turnover", s - cost)
    rep("net of fees AND a 1% round-trip spread", s - 2 * tn * 0.0116)
    print("\nWALK-FORWARD WEIGHTS (each week's weights = the average regression coefficients of EARLIER weeks only)")
    s, tn = run(allc, 5e6, five, wf=True)
    rep("all five, walk-forward", s, f"  turn {100 * tn:3.0f}%")
    s, tn = run(allc, 5e6, five, wf=True, cap=0.25)
    rep("all five, walk-forward, capped +-25%", s, f"  turn {100 * tn:3.0f}%")
    if on_delta:
        print(f"\nONLY COINS DELTA LISTS TODAY ({len(on_delta)}; ranked on Binance data)")
        for nm, kw in (("all five, equal weight", dict()), ("all five, equal weight, capped +-25%", dict(cap=0.25)),
                       ("all five, thirds", dict(frac=3)), ("all five, walk-forward", dict(wf=True))):
            s, tn = run(on_delta, 5e6, five, **kw)
            rep(nm, s, f"  turn {100 * tn:3.0f}%")


if __name__ == "__main__":
    main()
