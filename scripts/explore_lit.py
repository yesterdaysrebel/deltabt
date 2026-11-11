"""Ideas taken from published research, tested on our data (owner 2026-11-11: "read external sources, run tests
in different ways, look for unpopular strategies").

    PYTHONPATH=. python scripts/explore_lit.py

NOT a pre-registered test: open exploration on Binance perps 2021-2026. Sources are in out/explore_lit.txt's header.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import explore_wide as W  # noqa: E402

YEARS = W.YEARS


def rep(name, s, scale=100.0, unit="%"):
    s = pd.Series(s).dropna()
    if len(s) < 30:
        print(f"  {name:54s} (too few)")
        return
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    per = [s[s.index.year == int(y)].mean() if (s.index.year == int(y)).sum() >= 8 else np.nan for y in YEARS]
    cells = " ".join(f"{scale * m:+6.2f}" if np.isfinite(m) else "   n/a" for m in per)
    print(f"  {name:54s} n {len(s):5d}  mean {scale * s.mean():+7.3f}{unit}  t {t:+5.2f}  | {cells}{W.flag(per, t)}")


def main() -> None:
    O, H, L, C, V = W.panel()
    C = C.ffill(limit=7)
    ret = C.pct_change(fill_method=None)
    hist = C.notna().cumsum()
    v30 = V.rolling(30).mean()
    liquid = (v30 >= 5e6) & (hist >= 60)
    btc = ret["BTCUSDT"]

    # ---- A. reversal conditional on volume (Bianchi, Babiak & Dickerson 2022)
    print("A. REVERSAL BY VOLUME GROUP: within a volume third, long the past losers, short the past winners (fifths)")
    for look, h in ((1, 1), (1, 7), (7, 7)):
        past = C.pct_change(look, fill_method=None)
        fwd = C.shift(-h) / C - 1
        out = {"low": {}, "mid": {}, "high": {}}
        for t in C.index[90::h]:
            u = liquid.loc[t]
            x, f, v = past.loc[t][u], fwd.loc[t][u], v30.loc[t][u]
            ok = x.notna() & f.notna() & v.notna()
            if ok.sum() < 45:
                continue
            x, f, v = x[ok], f[ok].clip(-0.5, 0.5), v[ok]
            g = pd.qcut(v.rank(method="first"), 3, labels=["low", "mid", "high"])
            for name in out:
                xi, fi = x[g == name], f[g == name]
                k = max(len(xi) // 5, 3)
                o = xi.sort_values()
                out[name][t] = fi[o.index[:k]].mean() - fi[o.index[-k:]].mean()
        for name in out:
            rep(f"{look}-day reversal, hold {h} d, {name}-volume third", pd.Series(out[name]))

    # ---- B. the volume premium a different way: Fama-MacBeth weekly regressions with controls
    print("\nB. WEEKLY REGRESSION OF NEXT WEEK'S RETURN ON CHARACTERISTICS (each standardised across coins; coefficient")
    print("   = % a week per one standard deviation; all entered together, so each is net of the others)")
    chars = {"log 30-day volume": np.log(v30), "30-day volatility": ret.rolling(30).std(),
             "beta to BTC": ret.rolling(60).cov(btc).div(btc.rolling(60).var(), axis=0),
             "past 7-day return": C.pct_change(7, fill_method=None), "past 30-day return": C.pct_change(30, fill_method=None),
             "log price": np.log(C), "1-day volume / 30-day": V / v30}
    fwd = C.shift(-7) / C - 1
    coefs = {}
    for t in C.index[90::7]:
        u = liquid.loc[t]
        X = pd.DataFrame({k: v.loc[t][u] for k, v in chars.items()})
        y = fwd.loc[t][u].clip(-0.5, 0.5)
        ok = X.notna().all(axis=1) & y.notna() & np.isfinite(X).all(axis=1)
        if ok.sum() < 60:
            continue
        X, y = X[ok], y[ok]
        Z = (X - X.mean()) / X.std(ddof=0)
        A = np.column_stack([np.ones(len(Z)), Z.to_numpy()])
        coefs[t] = np.linalg.lstsq(A, y.to_numpy(), rcond=None)[0][1:]
    cf = pd.DataFrame(coefs, index=list(chars)).T
    for k in chars:
        rep(k, cf[k])

    # ---- C. BTC at its maximum / minimum (Quantpedia; Padysak & Vojtko)
    print("\nC. BTC AND THE ALT INDEX AT A NEW HIGH / LOW: next day's return, % per day")
    idx = ret.where(liquid.shift(1, fill_value=False)).mean(axis=1)
    bc = C["BTCUSDT"]
    ic = (1 + idx.fillna(0)).cumprod()
    for nm, px, r in (("BTC", bc, btc), ("alt index", ic, idx)):
        for n in (10, 20, 50):
            rep(f"{nm} closed at a {n}-day HIGH -> next day", r[(px >= px.rolling(n).max()).shift(1, fill_value=False)])
            rep(f"{nm} closed at a {n}-day LOW  -> next day", r[(px <= px.rolling(n).min()).shift(1, fill_value=False)])
        rep(f"{nm} all days", r)

    # ---- D/E. BTC sessions and the turn of the candle (minute data)
    z = np.load(Path("out/binance_paths") / "btc.npz")
    days = pd.to_datetime(z["days"], unit="s")
    cl = pd.DataFrame(z["close"], index=days).ffill(axis=1)
    print("\nD. BTC SESSIONS, % per session (a taker round trip costs ~0.10-0.16%)")
    for a, b in ((22, 24), (21, 23), (0, 8), (8, 16), (16, 24), (13, 21)):
        rep(f"BTC {a:02d}:00-{b:02d}:00 UTC", cl[60 * b - 1] / (cl[60 * a - 1] if a else cl[0]) - 1)
    wk = days.dayofweek >= 5
    rep("BTC 22:00-24:00 on weekdays", (cl[1439] / cl[1319] - 1)[~wk])
    rep("BTC 22:00-24:00 on weekends", (cl[1439] / cl[1319] - 1)[wk])
    print("\nE. TURN OF THE CANDLE (Shanaev et al. 2023): BTC mean 1-minute return, basis points")
    m = cl.pct_change(axis=1, fill_method=None)
    mins = np.arange(1440)
    for nm, sel in (("minutes :00 :15 :30 :45", (mins % 15 == 0) & (mins > 0)), ("minute :00 of each hour", (mins % 60 == 0) & (mins > 0)),
                    ("minutes :01 :16 :31 :46", mins % 15 == 1), ("all other minutes", (mins % 15 > 1))):
        rep(nm, m.loc[:, sel].mean(axis=1), scale=1e4, unit="bp")


if __name__ == "__main__":
    main()
