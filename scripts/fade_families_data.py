"""Data for the second idea search (docs/fade_families_prereg.md): crowding readings, BTC paths, loser picks.

    PYTHONPATH=. python scripts/fade_families_data.py

Writes into out/binance_paths/ (not committed), from the public Binance archive (no key):
  crowding.parquet  per rule A pick: funding before D, D-1 taker-buy share, D-1 open-interest change and
                    long/short ratios (metrics exist from 2021-12 only)
  btc.npz           BTCUSDT 1m closes for every pick day
  losers.parquet + losers.npz   the mirror picks (top 2 previous-day LOSERS) and their 1m path for D
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


def csv_zip(url: str) -> pd.DataFrame | None:
    try:
        b = B.get(url)
    except Exception:                                                # noqa: BLE001
        return None
    if not b:
        return None
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        raw = z.read(z.namelist()[0]).decode()
    first = raw.split("\n", 1)[0]
    return pd.read_csv(io.StringIO(raw), header=0 if not first[:1].isdigit() else None)


def ymd(t: int, fmt: str) -> str:
    return pd.to_datetime(t, unit="s").strftime(fmt)


# ---------------------------------------------------------------- crowding
def month_funding(key):
    sym, m = key
    f = csv_zip(f"{B.ARCHIVE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip")
    if f is None or f.empty:
        return key, None
    f = f.iloc[:, [0, 2]]
    f.columns = ["t", "rate"]
    return key, (f.t.astype("int64").to_numpy() // 1000, f.rate.astype(float).to_numpy())


def month_daily(key):
    sym, m = key
    b = None
    try:
        b = B.get(f"{B.ARCHIVE}/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip")
    except Exception:                                                # noqa: BLE001
        pass
    if not b:
        return key, None
    k = B.read_zip(b)
    return key, dict(zip(k.open_time.astype("int64") // 1000,
                         k.taker_buy_quote_volume.astype(float) / k.quote_volume.astype(float).replace(0, np.nan)))


def day_metrics(x):
    d = csv_zip(f"{B.ARCHIVE}/daily/metrics/{x.symbol}/{x.symbol}-metrics-{ymd(int(x.time) - DAY, '%Y-%m-%d')}.zip")
    if d is None or len(d) < 100:
        return {}
    oi = d["sum_open_interest"].astype(float)
    return dict(oi_chg=oi.iloc[-1] / oi.iloc[0] - 1 if oi.iloc[0] > 0 else np.nan,
                ls_top=float(d["sum_toptrader_long_short_ratio"].iloc[-1]),
                ls_acct=float(d["count_long_short_ratio"].iloc[-1]),
                taker_ls=float(d["sum_taker_long_short_vol_ratio"].astype(float).mean()))


def crowding(p: pd.DataFrame) -> None:
    prev = p.time - DAY
    fkeys = sorted({(s, ymd(t, "%Y-%m")) for s, t in zip(p.symbol, prev)}
                   | {(s, ymd(t - DAY, "%Y-%m")) for s, t in zip(p.symbol, prev)})
    with ThreadPoolExecutor(16) as ex:
        fund = dict(ex.map(month_funding, fkeys))
        dkeys = sorted({(s, ymd(t, "%Y-%m")) for s, t in zip(p.symbol, prev)})
        tak = dict(ex.map(month_daily, dkeys))
        met = list(ex.map(day_metrics, p.itertuples()))
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
        share = (tak.get((x.symbol, ymd(t0 - DAY, "%Y-%m"))) or {}).get(t0 - DAY, np.nan)
        rows.append(dict(symbol=x.symbol, time=t0, fund_last=f_last, fund_day=f_day, taker_share=share, **m))
    pd.DataFrame(rows).to_parquet(OUT / "crowding.parquet", index=False)
    c = pd.DataFrame(rows)
    print("crowding: " + ", ".join(f"{k} {int(c[k].notna().sum())}" for k in c.columns[2:]), flush=True)


# ---------------------------------------------------------------- 1m paths
def path_day(sym: str, t0: int) -> np.ndarray:
    out = np.full((3, 1440), np.nan, dtype=np.float32)
    try:
        b = B.get(f"{B.ARCHIVE}/daily/klines/{sym}/1m/{sym}-1m-{ymd(t0, '%Y-%m-%d')}.zip")
    except Exception:                                                # noqa: BLE001
        b = None
    if b:
        m = B.read_zip(b)
        i = (m.open_time.astype("int64").to_numpy() // 1000 - t0) // 60
        ok = (i >= 0) & (i < 1440)
        for row, col in enumerate(("high", "low", "close")):
            out[row, i[ok]] = m[col].to_numpy(float)[ok]
    return out


def btc(p: pd.DataFrame) -> None:
    days = np.array(sorted(p.time.unique()), dtype="int64")
    with ThreadPoolExecutor(16) as ex:
        arr = np.stack(list(ex.map(lambda t: path_day("BTCUSDT", int(t))[2], days)))
    np.savez_compressed(OUT / "btc.npz", days=days, close=arr)
    print(f"btc: {len(days)} days, full {int((np.isfinite(arr).sum(axis=1) >= 1430).sum())}", flush=True)


def losers() -> None:
    frames = []
    for d, (a, b) in RANGES.items():
        x = pd.read_parquet(Path(d) / "daily.parquet")
        x = x.sort_values(["symbol", "time"]).drop_duplicates(["symbol", "time"]).reset_index(drop=True)
        x["change"] = x.close / x.open - 1
        pc = x.groupby("symbol").close.shift(1)
        tr = np.maximum.reduce([x.high - x.low, (x.high - pc).abs(), (x.low - pc).abs()])
        x["atrp"] = pd.Series(tr, index=x.index).groupby(x.symbol).transform(lambda s: s.rolling(14).mean()) / x.close
        prev = x[["symbol", "time", "change", "quote_volume", "atrp"]].copy()
        prev["time"] += DAY
        prev.columns = ["symbol", "time", "prev_change", "prev_turnover", "atrp"]
        q = x[["symbol", "time", "open"]].merge(prev, on=["symbol", "time"])
        q = q[(q.time >= a) & (q.time < b)]
        q = q[(q.prev_change < 0) & (q.prev_turnover >= B.MIN_TURNOVER) & np.isfinite(q.atrp) & (q.atrp > 0)]
        q = q.sort_values(["time", "prev_change"]).groupby("time").head(2).copy()   # most negative first
        q["rank"] = q.groupby("time").cumcount() + 1
        frames.append(q)
    L = pd.concat(frames, ignore_index=True)
    L["dist"] = B.K_STOP * L.atrp
    L["year"] = pd.to_datetime(L.time, unit="s").dt.year.astype(str)
    L = L[L.dist <= B.MAX_DIST].reset_index(drop=True)
    print(f"losers: {len(L)} picks; 1m ...", flush=True)
    with ThreadPoolExecutor(16) as ex:
        arr = np.stack(list(ex.map(lambda x: path_day(x.symbol, int(x.time)), L.itertuples())))
    np.savez_compressed(OUT / "losers.npz", paths=arr)
    L.to_parquet(OUT / "losers.parquet", index=False)
    print(f"losers: saved, full first day {int((np.isfinite(arr[:, 2]).sum(axis=1) >= 1430).sum())}", flush=True)


def main() -> None:
    p = pd.read_parquet(OUT / "picks.parquet")
    crowding(p)
    btc(p)
    losers()


if __name__ == "__main__":
    main()
