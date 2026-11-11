"""Data for the second idea search (docs/fade_families_prereg.md).

    PYTHONPATH=. python scripts/fade_families_data.py

Writes into out/binance_paths/ (not committed), from the public Binance archive (no key):
  features.parquet  per rule A pick, known before D 00:00: funding, D-1 taker-buy share, D-1 open-interest change
                    and long/short ratios (metrics from 2021-12 only), spot/futures volume, coin age, BTC's D-1 change
  btc.npz           BTCUSDT 1m closes for every day in the pick range
  set_<name>.parquet + set_<name>.npz   other pick sets and their 1m path for D (high/low/close):
                    losers (long), weekly (top 7-day gainers), volsurge (largest volume surge among risers)
"""
from __future__ import annotations

import io
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B  # noqa: E402

OUT = Path("out/binance_paths")
DAY = B.DAY
RANGES = {"out/binance": (B.TS("2021-01-15"), B.TS("2025-01-01")),
          "out/binance_control": (B.TS("2025-01-15"), B.TS("2026-10-28"))}
POOL = ThreadPoolExecutor(16)


def secs(t) -> np.ndarray:
    """Archive open times are ms before 2025 and microseconds after (spot)."""
    t = np.asarray(t, dtype="int64")
    return np.where(t > 10**14, t // 10**6, t // 1000)


def csv_zip(url: str, cols: list[str] | None = None) -> pd.DataFrame | None:
    try:
        b = B.get(url)
    except Exception:                                                # noqa: BLE001
        return None
    if not b:
        return None
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        raw = z.read(z.namelist()[0]).decode()
    has_header = not raw[:1].isdigit()
    df = pd.read_csv(io.StringIO(raw), header=0 if has_header else None)
    if cols is not None:
        df = df.iloc[:, :len(cols)]
        df.columns = cols
    return df


def ymd(t: int, fmt: str) -> str:
    return pd.to_datetime(int(t), unit="s").strftime(fmt)


def daily_all() -> pd.DataFrame:
    d = pd.concat([pd.read_parquet(Path(x) / "daily.parquet") for x in RANGES], ignore_index=True)
    return d.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"]).reset_index(drop=True)


# ---------------------------------------------------------------- features of rule A's picks
def month_funding(key):
    sym, m = key
    f = csv_zip(f"{B.ARCHIVE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip", ["t", "iv", "rate"])
    if f is None or f.empty:
        return key, None
    return key, (secs(f.t), f.rate.astype(float).to_numpy())


K12 = B.COLS


def month_kline(key):
    """(market, symbol, month) -> {day: (quote volume, taker-buy share)} from monthly 1d klines."""
    market, sym, m = key
    base = B.ARCHIVE if market == "fut" else "https://data.binance.vision/data/spot"
    k = csv_zip(f"{base}/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip", K12)
    if k is None or k.empty:
        return key, {}
    q = k.quote_volume.astype(float)
    share = k.taker_buy_quote_volume.astype(float) / q.replace(0, np.nan)
    return key, {int(t): (float(a), float(b)) for t, a, b in zip(secs(k.open_time), q, share)}


def day_metrics(x):
    d = csv_zip(f"{B.ARCHIVE}/daily/metrics/{x.symbol}/{x.symbol}-metrics-{ymd(x.time - DAY, '%Y-%m-%d')}.zip")
    try:
        oi = d["sum_open_interest"].astype(float)
        return dict(oi_chg=oi.iloc[-1] / oi.iloc[0] - 1 if oi.iloc[0] > 0 else np.nan,
                    ls_top=float(d["sum_toptrader_long_short_ratio"].iloc[-1]),
                    ls_acct=float(d["count_long_short_ratio"].iloc[-1]),
                    taker_ls=float(d["sum_taker_long_short_vol_ratio"].astype(float).mean()))
    except Exception:                                                # noqa: BLE001
        return {}


def spot_symbol(sym: str) -> str:
    for pre in ("1000000", "1000"):
        if sym.startswith(pre):
            return sym[len(pre):]
    return sym


def features(p: pd.DataFrame, d: pd.DataFrame) -> None:
    prev = (p.time - DAY).to_numpy()
    fkeys = sorted({(s, ymd(t - k * DAY, "%Y-%m")) for s, t in zip(p.symbol, prev) for k in (0, 1)})
    fund = dict(POOL.map(month_funding, fkeys))
    kkeys = sorted({(mk, s if mk == "fut" else spot_symbol(s), ymd(t, "%Y-%m"))
                    for s, t in zip(p.symbol, prev) for mk in ("fut", "spot")})
    kl = dict(POOL.map(month_kline, kkeys))
    met = list(POOL.map(day_metrics, p.itertuples()))
    first = d.groupby("symbol").time.min()
    start = int(d.time.min())
    btc = d[d.symbol == "BTCUSDT"].set_index("time")
    btc_chg = (btc.close / btc.open - 1).to_dict()
    rows = []
    for x, m in zip(p.itertuples(), met):
        t0 = int(x.time)
        ts, rs = [], []
        for key in {(x.symbol, ymd(t0 - DAY, "%Y-%m")), (x.symbol, ymd(t0 - 2 * DAY, "%Y-%m"))}:
            if fund.get(key) is not None:
                ts.append(fund[key][0]); rs.append(fund[key][1])
        f_last = f_day = np.nan
        if ts:
            t, r = np.concatenate(ts), np.concatenate(rs)
            before = t < t0 - 60                                     # settled before D 00:00 (not the 00:00 one)
            if before.any():
                f_last = float(r[before][np.argmax(t[before])])
                d1 = before & (t >= t0 - DAY - 60)
                f_day = float(r[d1].sum()) if d1.any() else np.nan
        mth = ymd(t0 - DAY, "%Y-%m")
        fq, share = kl.get(("fut", x.symbol, mth), {}).get(t0 - DAY, (np.nan, np.nan))
        sq = kl.get(("spot", spot_symbol(x.symbol), mth), {}).get(t0 - DAY, (np.nan, np.nan))[0]
        f0 = int(first.get(x.symbol, start))
        rows.append(dict(symbol=x.symbol, time=t0, fund_last=f_last, fund_day=f_day, taker_share=share,
                         spot_ratio=sq / fq if fq and np.isfinite(fq) and np.isfinite(sq) else np.nan,
                         has_spot=float(np.isfinite(sq)),
                         age_days=np.inf if f0 <= start + DAY else (t0 - f0) / DAY,
                         btc_prev=btc_chg.get(t0 - DAY, np.nan), **m))
    c = pd.DataFrame(rows)
    c.to_parquet(OUT / "features.parquet", index=False)
    print("features: " + ", ".join(f"{k} {int(np.isfinite(c[k]).sum())}" for k in c.columns[2:]), flush=True)


# ---------------------------------------------------------------- 1m paths
def path_day(sym: str, t0: int) -> np.ndarray:
    out = np.full((3, 1440), np.nan, dtype=np.float32)
    try:
        b = B.get(f"{B.ARCHIVE}/daily/klines/{sym}/1m/{sym}-1m-{ymd(t0, '%Y-%m-%d')}.zip")
    except Exception:                                                # noqa: BLE001
        b = None
    if b:
        m = B.read_zip(b)
        i = (secs(m.open_time) - t0) // 60
        ok = (i >= 0) & (i < 1440)
        for row, col in enumerate(("high", "low", "close")):
            out[row, i[ok]] = m[col].to_numpy(float)[ok]
    return out


def btc_paths() -> None:
    days = np.concatenate([np.arange(a, b, DAY) for a, b in RANGES.values()]).astype("int64")
    arr = np.stack(list(POOL.map(lambda t: path_day("BTCUSDT", int(t))[2], days)))
    np.savez_compressed(OUT / "btc.npz", days=days, close=arr)
    print(f"btc: {len(days)} days, full {int((np.isfinite(arr).sum(axis=1) >= 1430).sum())}", flush=True)


def pick_sets() -> None:
    sets: dict[str, list[pd.DataFrame]] = {"losers": [], "weekly": [], "volsurge": []}
    for src, (a, b) in RANGES.items():
        x = pd.read_parquet(Path(src) / "daily.parquet")
        x = x.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"]).reset_index(drop=True)
        g = x.groupby("symbol")
        x["change"] = x.close / x.open - 1
        pc = g.close.shift(1)
        tr = np.maximum.reduce([x.high - x.low, (x.high - pc).abs(), (x.low - pc).abs()])
        x["atrp"] = pd.Series(tr, index=x.index).groupby(x.symbol).transform(lambda s: s.rolling(14).mean()) / x.close
        x["wk"] = x.close / g.close.shift(7) - 1                     # 7-day change to this close
        x["surge"] = x.quote_volume / g.quote_volume.transform(lambda s: s.shift(1).rolling(14).mean())
        prev = x[["symbol", "time", "change", "quote_volume", "atrp", "wk", "surge"]].copy()
        prev["time"] += DAY
        prev.columns = ["symbol", "time", "prev_change", "prev_turnover", "atrp", "wk", "surge"]
        q = x[["symbol", "time", "open"]].merge(prev, on=["symbol", "time"])
        q = q[(q.time >= a) & (q.time < b) & (q.prev_turnover >= B.MIN_TURNOVER) & np.isfinite(q.atrp) & (q.atrp > 0)]
        top = lambda f, col, asc: (f.sort_values(["time", col], ascending=[True, asc]).groupby("time").head(2))  # noqa: E731
        sets["losers"].append(top(q[q.prev_change < 0], "prev_change", True))
        sets["weekly"].append(top(q[np.isfinite(q.wk) & (q.wk > 0)], "wk", False))
        sets["volsurge"].append(top(q[(q.prev_change > 0) & np.isfinite(q.surge)], "surge", False))
    for name, frames in sets.items():
        L = pd.concat(frames, ignore_index=True)
        L["rank"] = L.groupby("time").cumcount() + 1
        L["dist"] = B.K_STOP * L.atrp
        L["year"] = pd.to_datetime(L.time, unit="s").dt.year.astype(str)
        L = L[L.dist <= B.MAX_DIST].reset_index(drop=True)
        print(f"{name}: {len(L)} picks; 1m ...", flush=True)
        arr = np.stack(list(POOL.map(lambda x: path_day(x.symbol, int(x.time)), L.itertuples())))
        np.savez_compressed(OUT / f"set_{name}.npz", paths=arr)
        L.to_parquet(OUT / f"set_{name}.parquet", index=False)
        print(f"{name}: saved, full day {int((np.isfinite(arr[:, 2]).sum(axis=1) >= 1430).sum())}", flush=True)


def main() -> None:
    p = pd.read_parquet(OUT / "picks.parquet")
    features(p, daily_all())
    btc_paths()
    pick_sets()


if __name__ == "__main__":
    main()
