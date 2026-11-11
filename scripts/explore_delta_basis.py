"""The basis / funding lead on Delta itself, with Delta's REAL funding (hourly FUNDING series, settled every
funding_interval_seconds), Delta's prices, and only coins Delta had listed for 30+ days. Open exploration.

    PYTHONPATH=. python scripts/explore_delta_basis.py

Trade: LONG the fifth with the highest signal, SHORT the fifth with the lowest; a short RECEIVES a positive rate.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import gainer_fade_2025_lab as G  # noqa: E402
ProductCatalog = G.ProductCatalog

DAY, HOUR = 86400, 3600
FEE_RT = 0.00158


def funding_daily(sym, iv, lo, hi) -> pd.Series:
    st = G._store()
    try:
        f = st.load(sym, "funding", "1h", lo, hi)
    except Exception as exc:                                         # noqa: BLE001
        print(f"  {sym}: {exc}", flush=True)
        return pd.Series(dtype=float, name=sym)
    if f.empty:
        return pd.Series(dtype=float, name=sym)
    t, v = f.time.to_numpy("int64"), f.close.to_numpy(float)
    stamps = np.arange((lo // iv + 1) * iv, hi, iv)
    i = np.searchsorted(t, stamps, side="right") - 1
    ok = (i >= 0) & (stamps - t[np.clip(i, 0, None)] <= 2 * HOUR)
    s = pd.Series(v[i[ok]] / 100.0, index=stamps[ok] // DAY * DAY)
    return s.groupby(level=0).sum().rename(sym)


def main() -> None:
    d = G.daily()
    d["date"] = pd.to_datetime(d.time, unit="s")
    C = d.pivot(index="date", columns="symbol", values="close").ffill(limit=7)
    V = d.pivot(index="date", columns="symbol", values="turnover")
    cat = ProductCatalog()
    lo, hi = int(d.time.min()), int(d.time.max()) + DAY
    jobs = []
    for s in C.columns:
        try:
            iv = int(cat.get(s)["funding_interval_seconds"])
        except Exception:                                            # noqa: BLE001
            iv = 8 * HOUR
        jobs.append((s, iv))
    with ThreadPoolExecutor(3) as ex:
        cols = list(ex.map(lambda j: funding_daily(j[0], j[1], lo, hi), jobs))
    F = pd.concat(cols, axis=1)
    F.index = pd.to_datetime(F.index, unit="s")
    F = F.reindex(index=C.index, columns=C.columns)
    F.to_parquet("out/binance_paths/delta_funding_daily.parquet")
    print(f"Delta funding: {int(F.notna().any().sum())} coins; median {1e4 * F.stack().median():+.2f} bp/day; "
          f"5th pct {1e4 * F.stack().quantile(.05):+.1f}, 95th {1e4 * F.stack().quantile(.95):+.1f} bp/day")
    hist = C.notna().cumsum()
    v30 = V.rolling(30).mean()

    def run(sig, h, floor=2.5e5, frac=5):
        liquid = (v30 >= floor) & (hist >= 45)
        fwd = C.shift(-h) / C - 1
        ffwd = F.shift(-1).rolling(h, min_periods=h).sum().shift(-(h - 1))
        rows, turn, prev = [], [], None
        for t in C.index[60::h]:
            u = liquid.loc[t]
            a, f, fu = sig.loc[t][u], fwd.loc[t][u], ffwd.loc[t][u]
            ok = a.notna() & f.notna() & fu.notna()
            if ok.sum() < 20:
                continue
            a, f, fu = a[ok], f[ok], fu[ok]
            k = max(len(a) // frac, 3)
            o = a.sort_values()
            Ls, Ss = set(o.index[-k:]), set(o.index[:k])
            tn = 1.0 if prev is None else (2 - len(Ls & prev[0]) / k - len(Ss & prev[1]) / k) / 2
            prev = (Ls, Ss)
            rows.append((t, f[list(Ls)].mean() - f[list(Ss)].mean(), -fu[list(Ls)].mean() + fu[list(Ss)].mean(),
                         2 * tn * FEE_RT, len(a), 1e4 * a[list(Ls)].mean(), 1e4 * a[list(Ss)].mean()))
        return pd.DataFrame(rows, columns=["t", "price", "fund", "fee", "n", "sL", "sS"]).set_index("t")

    def rep(nm, s):
        t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
        eq = (1 + s).cumprod()
        per = " ".join(f"{y} {100 * s[s.index.year == y].mean():+5.2f}" for y in (2025, 2026))
        print(f"    {nm:34s} {100 * s.mean():+6.3f}%  t {t:+5.2f}  win {100 * (s > 0).mean():3.0f}%  maxDD {100 * (1 - eq / eq.cummax()).max():3.0f}% | {per}")

    for title, sig in (("30-day mean funding", F.rolling(30, min_periods=20).mean()), ("7-day mean funding", F.rolling(7, min_periods=5).mean()),
                       ("yesterday's funding", F)):
        for h in (7, 1):
            for floor in (2.5e5, 1e6):
                r = run(sig, h, floor)
                if len(r) < 20:
                    continue
                print(f"\nLONG high / SHORT low {title}; hold {h}d; floor ${floor:,.0f}/day; n {len(r)}, ~{int(r.n.median())} coins; "
                      f"signal long {r.sL.mean():+.1f} bp/day, short {r.sS.mean():+.1f}")
                rep("price move", r.price)
                rep("REAL Delta funding", r.fund)
                rep("fees at the real turnover", -r.fee)
                rep("NET (price + funding - fees)", r.price + r.fund - r.fee)
                rep("the CARRY side (reverse), net", -r.price - r.fund - r.fee)


if __name__ == "__main__":
    main()
