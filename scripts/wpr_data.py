"""5m perpetual candles of the large coins, Binance public archive 2021-01..2026-10, for the %R(140) band-cross
research (owner 2026-11-14: "wpr at 140 when it drops from -20 towards -80 (sell), when it rises from -80 to -20
(buy) mostly targets are hit"). 15m and 1h are resampled from these.

    PYTHONPATH=. python scripts/wpr_data.py

Writes out/wpr/<SYMBOL>_5m.parquet (not committed). Public data, no key.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B  # noqa: E402
import trend_data as T  # noqa: E402

OUT = Path("out/wpr")


def month(sym: str, ym: str) -> pd.DataFrame | None:
    blob = B.get(f"{B.ARCHIVE}/monthly/klines/{sym}/5m/{sym}-5m-{ym}.zip")
    if not blob:
        return None
    df = B.read_zip(blob)[["open_time", "open", "high", "low", "close", "quote_volume"]]
    t = df.open_time.astype("int64")
    t = t // 1000 if t.max() > 1e11 else t
    df["time"] = (t // 1000 if t.max() > 1e11 else t).astype("int64")          # some files use microseconds
    return df.drop(columns="open_time")


def one(sym: str) -> str:
    f = OUT / f"{sym}_5m.parquet"
    if f.exists():
        return f"{sym}: cached"
    parts = [m for m in (month(sym, ym) for ym in T.MONTHS) if m is not None]
    if not parts:
        return f"{sym}: no data"
    df = pd.concat(parts, ignore_index=True).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    df.to_parquet(f)
    return f"{sym}: {len(df):,} bars {pd.to_datetime(df.time.min(), unit='s').date()}..{pd.to_datetime(df.time.max(), unit='s').date()}"


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(5) as ex:
        for line in ex.map(one, T.COINS + T.HOLDOUT):
            print(line, flush=True)
