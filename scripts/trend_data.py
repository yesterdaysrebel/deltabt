"""1h perpetual candles of the large coins, Binance public archive 2021-01..2026-10, for the trend research
(owner 2026-11-12, "option 2": a strategy built for reward:risk 1:4-1:5 on bigger coins).

    PYTHONPATH=. python scripts/trend_data.py

Writes out/trend/<SYMBOL>_1h.parquet (not committed). Public data, no key.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_binance_test as B  # noqa: E402

OUT = Path("out/trend")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT"]
#: Held-out coins for the confirmation (2026-11-12): never used while the rule was found.
HOLDOUT = ["DOTUSDT", "TRXUSDT", "ATOMUSDT", "NEARUSDT", "UNIUSDT", "FILUSDT", "BCHUSDT", "ETCUSDT", "AAVEUSDT", "XLMUSDT"]
MONTHS = [f"{y}-{m:02d}" for y in range(2021, 2027) for m in range(1, 13) if (y, m) <= (2026, 10)]


def month(sym: str, ym: str) -> pd.DataFrame | None:
    blob = B.get(f"{B.ARCHIVE}/monthly/klines/{sym}/1h/{sym}-1h-{ym}.zip")
    if not blob:
        return None
    df = B.read_zip(blob)
    return df[["open_time", "open", "high", "low", "close", "quote_volume"]] if "quote_volume" in df else df.iloc[:, :6]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for sym in COINS + (HOLDOUT if "--holdout" in sys.argv else []):
        f = OUT / f"{sym}_1h.parquet"
        if f.exists():
            continue
        with ThreadPoolExecutor(6) as ex:
            parts = [x for x in ex.map(lambda ym: month(sym, ym), MONTHS) if x is not None]
        df = pd.concat(parts, ignore_index=True)
        t = df.open_time.astype("int64")
        df["time"] = (t // 1000 if t.max() > 1e11 else t).astype("int64")
        df["time"] = df.time // 1000 if df.time.max() > 1e11 else df.time   # some files use microseconds
        df = df.drop(columns="open_time").drop_duplicates("time").sort_values("time").reset_index(drop=True)
        df.to_parquet(f)
        print(f"{sym}: {len(df)} hours, {pd.to_datetime(df.time.min(), unit='s')} .. {pd.to_datetime(df.time.max(), unit='s')}", flush=True)


if __name__ == "__main__":
    main()
