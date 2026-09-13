"""Backfill the 1m archive for the m3 measurement, from the public API.

    PYTHONPATH=. python3 scripts/m3_backfill.py

Fetches exactly what scripts/m3_backtest.py reads -- ltp 1m, mark 1m and
funding 1h for the two universes it compares -- through the same
fetch-through CandleStore every other consumer uses, so repeat runs are
incremental and offline. GET-only public endpoints, like everything else in
deltabt.data; there is nothing to authenticate.
"""
from __future__ import annotations

import time

from deltabt.config import RESOLUTION_FUNDING
from deltabt.data.store import DEFAULT_HISTORY_START, CandleStore

SYMBOLS = ["BEATUSD", "ETHUSD", "SOLUSD", "AKEUSD", "BANKUSD"]

store = CandleStore()
end = int(time.time()) - 120          # never the forming minute

for sym in SYMBOLS:
    for series, res in (("ltp", "1m"), ("mark", "1m"),
                        ("funding", RESOLUTION_FUNDING)):
        df = store.load(sym, series, res, DEFAULT_HISTORY_START, end)
        span = ""
        if len(df):
            days = (df["time"].iloc[-1] - df["time"].iloc[0]) / 86400
            span = f"  ({days:,.0f} days)"
        print(f"{sym:<9} {series:<8} {res:<3} {len(df):>9,} rows{span}",
              flush=True)
