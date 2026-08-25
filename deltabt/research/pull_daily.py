"""Pull daily bars for every crypto perpetual, for H-XSec-1.

    PYTHONPATH=. python3 -u -m deltabt.research.pull_daily

The cross-sectional test needs the WHOLE universe, not the eight symbols in
data/candles. Daily resolution keeps that affordable: ~600 bars a symbol fits
in one request against the endpoint's 4000-bar cap.

VOLUME IS THE POINT, not just price. H-XSec-1's eligibility rule is causal --
trailing 30-day median USD volume computed strictly before each rebalance --
because filtering on TODAY's turnover would select the symbols that survived
and are liquid now, which is survivorship bias inside a 2025 backtest. That
filter can only be built from per-bar volume, so it is stored here.

Written to out/xsec/daily.parquet as a long frame: symbol, time, ohlcv.
Re-running overwrites it; the API is the source of truth, not this file.
"""

from __future__ import annotations

import json
import time

import pandas as pd

from deltabt.config import OUT_DIR
from deltabt.data.client import DeltaClient

OUT = OUT_DIR / "xsec"
START = int(pd.Timestamp("2024-06-01", tz="UTC").timestamp())   # 6m of warm-up
#: The 90-bar history requirement and the 30-day volume window both need bars
#: BEFORE 2025-01-01, or the study window would open with an empty universe.


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    products = json.load(open("data/meta/products.json"))
    # Crypto perps only: the 0.05% taker tier. Tokenised equity and metals
    # track instruments with different trading hours, so their daily bars are
    # not comparable to a 24/7 crypto bar.
    syms = sorted(s for s, p in products.items()
                  if float(p["taker_fee"]) >= 0.0005)
    print(f"{len(syms)} crypto perpetuals")

    client = DeltaClient()
    now = int(time.time())
    rows, failed, empty = [], [], []
    for i, s in enumerate(syms, 1):
        try:
            bars = client.candles(s, "1d", START, now)
        except Exception as exc:                       # noqa: BLE001
            failed.append((s, str(exc)[:60]))
            print(f"  [{i:>3}/{len(syms)}] {s:14} FAILED {str(exc)[:50]}")
            continue
        if not bars:
            empty.append(s)
            print(f"  [{i:>3}/{len(syms)}] {s:14} no bars")
            continue
        for b in bars:
            rows.append((s, int(b["time"]), float(b["open"]), float(b["high"]),
                         float(b["low"]), float(b["close"]),
                         float(b.get("volume") or 0.0)))
        if i % 20 == 0 or i == len(syms):
            print(f"  [{i:>3}/{len(syms)}] {s:14} {len(bars):>4} bars   "
                  f"running total {len(rows):,}")

    df = pd.DataFrame(rows, columns=["symbol", "time", "open", "high", "low",
                                     "close", "volume"])
    df = df.sort_values(["symbol", "time"]).reset_index(drop=True)
    path = OUT / "daily.parquet"
    df.to_parquet(path, index=False)

    print(f"\n{len(df):,} bars across {df.symbol.nunique()} symbols")
    print(f"  span {pd.Timestamp(int(df.time.min()), unit='s').date()} -> "
          f"{pd.Timestamp(int(df.time.max()), unit='s').date()}")
    print(f"  {len(failed)} failed, {len(empty)} returned nothing")
    for s, e in failed[:10]:
        print(f"    FAILED {s}: {e}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
