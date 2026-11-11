"""WIDE open exploration: any family, unpopular or invented (owner 2026-11-11: "look for more ideas, not just
fade ones, try any family, unpopular ones, invent some").

    PYTHONPATH=. python scripts/explore_wide.py

NOT a test. Binance USDT-M perpetuals, daily candles 2021-2026 (out/binance + out/binance_control daily.parquet,
delisted coins included) and BTC 1m closes (out/binance_paths/btc.npz). Everything is looked at at once; anything
here is a LEAD. Flags: '<<' = same sign every year and |t| >= 3; '<' = all but one year and |t| >= 2.5.

Cross-sectional ideas: each rebalance, rank the liquid coins by the signal; LONG the top fifth, SHORT the bottom
fifth, equal weight, hold `h` days (1 or 7, non-overlapping). `net` takes 0.30% per rebalance off the long-short
return (both legs fully turned over at ~0.08% a side -- Delta's fee). A NEGATIVE gross means the reverse trade.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

YEARS = ("2021", "2022", "2023", "2024", "2025", "2026")
COST_LS = 0.0030                                                     # per rebalance, long-short, both legs
MIN_VOL, MIN_HIST, MIN_COINS = 5e6, 60, 25


def panel():
    d = pd.concat([pd.read_parquet(f"{x}/daily.parquet") for x in ("out/binance", "out/binance_control")])
    d = d.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"])
    d["date"] = pd.to_datetime(d.time, unit="s")
    piv = lambda c: d.pivot(index="date", columns="symbol", values=c)   # noqa: E731
    return piv("open"), piv("high"), piv("low"), piv("close"), piv("quote_volume")


def flag(per, t):
    v = [m for m in per if np.isfinite(m)]
    if len(v) < 5 or not np.isfinite(t):
        return ""
    same = max(sum(m > 0 for m in v), sum(m < 0 for m in v))
    return "  <<" if same == len(v) and abs(t) >= 3 else ("  <" if same >= len(v) - 1 and abs(t) >= 2.5 else "")


def report(name, s: pd.Series, per_year_scale=100.0, extra=""):
    """s: per-period returns indexed by date."""
    s = s.dropna()
    if len(s) < 30:
        print(f"  {name:46s} (too few)")
        return
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    per = [s[s.index.year == int(y)].mean() if (s.index.year == int(y)).sum() >= 8 else np.nan for y in YEARS]
    cells = " ".join(f"{per_year_scale * m:+6.2f}" if np.isfinite(m) else "   n/a" for m in per)
    print(f"  {name:46s} n {len(s):4d}  mean {per_year_scale * s.mean():+6.3f}%  t {t:+5.2f}{extra}  | {cells}{flag(per, t)}")


def main() -> None:
    O, H, L, C, V = panel()
    C = C.ffill(limit=7)                                             # a coin that stops trading keeps its last price a week
    ret = C.pct_change(fill_method=None)
    hist = C.notna().cumsum()
    liquid = (V.rolling(30).mean() >= MIN_VOL) & (hist >= MIN_HIST)
    btc = ret["BTCUSDT"]
    idx = ret.where(liquid.shift(1, fill_value=False)).mean(axis=1)   # equal-weight liquid-alt index, tradable at t-1
    print(f"liquid universe: median {int(liquid.sum(axis=1).median())} coins a day; days {len(C)}")

    # ------------------------------------------------------------------ cross-sectional signals (known at close t)
    r30 = ret.rolling(30)
    cov = ret.rolling(60).cov(btc)
    beta = cov.div(btc.rolling(60).var(), axis=0)
    resid = ret.sub(beta.mul(btc, axis=0))
    rng = (H - L).replace(0, np.nan)
    age = hist.where(C.notna())
    first_seen = C.notna().idxmax()
    censored = first_seen <= C.index[0] + pd.Timedelta(days=1)
    age = age.mask(pd.DataFrame(np.tile(censored.to_numpy(), (len(C), 1)), index=C.index, columns=C.columns), 9999)
    sig = {
        "low volatility (30d) minus high": -r30.std(),
        "NOT lottery: low 30d max day minus high": -r30.max(),
        "low skew (30d) minus high": -r30.skew(),
        "low beta to BTC minus high": -beta,
        "1-day losers minus winners (reversal)": -ret,
        "1-day residual losers minus winners": -resid,
        "7-day losers minus winners": -C.pct_change(7, fill_method=None),
        "30-day winners minus losers (momentum)": C.shift(1).pct_change(30, fill_method=None),
        "residual 30-day winners minus losers": resid.rolling(30).sum(),
        "near the 90-day high minus far": C / H.rolling(90).max(),
        "illiquid minus liquid (30d volume)": -np.log(V.rolling(30).mean()),
        "volume surge (1d / 30d) minus quiet": V / V.rolling(30).mean(),
        "rising attention (7d / 30d volume)": V.rolling(7).mean() / V.rolling(30).mean(),
        "closed near the day's low minus near high": -((C - L) / rng),
        "high nominal price minus penny": np.log(C),
        "old coins minus newly listed": age,
        "wide daily range (ATR%) minus narrow": (rng / C).rolling(14).mean(),
        "up on falling volume minus up on rising": -(C.pct_change(7, fill_method=None).rank(axis=1, pct=True)
                                                    * (V.rolling(7).mean() / V.rolling(30).mean()).rank(axis=1, pct=True)),
        "small body, long upper wick minus opposite": (H - np.maximum(O, C)) / rng - (np.minimum(O, C) - L) / rng,
        "streak: most up-days in 10 minus fewest": (ret > 0).rolling(10).sum().where(ret.notna()),
    }
    for h in (1, 7):
        print(f"\nCROSS-SECTION, hold {h} day(s): long top fifth minus short bottom fifth, % per rebalance "
              f"(net = after {100 * COST_LS:.2f}%)")
        fwd = C.shift(-h) / C - 1
        days = C.index[90::h]
        for name, s in sig.items():
            rows = {}
            for t in days:
                u = liquid.loc[t]
                x, f = s.loc[t][u], fwd.loc[t][u]
                ok = x.notna() & f.notna() & np.isfinite(x)
                if ok.sum() < MIN_COINS:
                    continue
                x, f = x[ok], f[ok]
                k = max(len(x) // 5, 3)
                o = x.sort_values()
                rows[t] = f[o.index[-k:]].mean() - f[o.index[:k]].mean()
            g = pd.Series(rows)
            best = abs(g.mean()) - COST_LS
            report(name, g, extra=f"  net {100 * best:+6.3f}%")

    # ------------------------------------------------------------------ calendar / market-wide (index = liquid alts)
    print("\nMARKET-WIDE (equal-weight liquid-alt index, % per day unless stated)")
    for k, nm in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        report(f"index on {nm}", idx[idx.index.dayofweek == k])
    report("index, first 3 days of the month", idx[idx.index.day <= 3])
    report("index, last 3 days of the month", idx[idx.index.day >= 27])
    report("index, the day AFTER it fell > 8%", idx[idx.shift(1) < -0.08])
    report("index, the day AFTER it rose > 8%", idx[idx.shift(1) > 0.08])
    report("index minus BTC, the day after BTC rose > 3%", (idx - btc)[btc.shift(1) > 0.03])
    report("index minus BTC, the day after BTC fell > 3%", (idx - btc)[btc.shift(1) < -0.03])
    trend = np.sign((1 + idx).rolling(20).apply(np.prod, raw=True) - 1).shift(1)
    report("index x its own 20-day trend (trend-following)", idx * trend)
    btrend = np.sign(C["BTCUSDT"].pct_change(20, fill_method=None)).shift(1)
    report("BTC x its own 20-day trend", btc * btrend)
    breadth = ((ret > 0.20) & liquid).sum(axis=1)
    report("index, the day after >= 5 coins rose > 20%", idx[breadth.shift(1) >= 5])
    disp = ret.where(liquid).std(axis=1)
    report("index, the day after top-decile dispersion", idx[disp.shift(1) > disp.quantile(0.9)])

    print("\nNEW LISTINGS: a coin's return minus the index, by days since its first candle (% per day)")
    ex = ret.sub(idx, axis=0)
    listed = hist.where(~pd.DataFrame(np.tile(censored.to_numpy(), (len(C), 1)), index=C.index, columns=C.columns))
    for a, b in ((2, 7), (8, 30), (31, 90), (91, 180)):
        m = (listed >= a) & (listed <= b)
        report(f"days {a}-{b} after listing", ex.where(m).mean(axis=1))

    print("\nBTC HOUR OF DAY (UTC): mean return per hour, % (a round trip costs ~0.10-0.16%)")
    z = np.load(Path("out/binance_paths") / "btc.npz")
    days = pd.to_datetime(z["days"], unit="s")
    cl = pd.DataFrame(z["close"], index=days).ffill(axis=1)
    for h in range(24):
        a = cl[60 * h - 1] if h else cl[0]
        report(f"BTC {h:02d}:00-{h + 1:02d}:00", cl[60 * (h + 1) - 1] / a - 1)


if __name__ == "__main__":
    main()
