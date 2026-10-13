"""A small synthetic market for the signal-features parity test.

Deterministic (numpy PCG64, seeded), so the golden file written from the lab's
ORIGINAL `_coin` (before deltabt/signal_features.py existed) can be compared
against the refactored one on any machine.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DAY = 86400
START = 1_767_225_600 - 4 * DAY          # 2025-12-28 00:00 UTC
DAYS = 12
SYMBOL = "SYNTHUSD"
GOLDEN = Path(__file__).parent / "fixtures" / "signal_lab_golden.parquet"
GOLDEN_PATHS = Path(__file__).parent / "fixtures" / "signal_lab_golden_paths.parquet"

PRODUCT = {"symbol": SYMBOL, "tick_size": 0.0001, "contract_value": 1.0,
           "maker_fee": 0.0002, "taker_fee": 0.0005, "max_leverage": 50.0,
           "position_size_limit": float("inf"), "funding_interval_seconds": 14400,
           "launch_time": "2025-11-01T00:00:00Z"}


def _walk(seed: int, n: int, p0: float) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # trending legs of random length and sign, plus noise: enough structure
    # for %R + Supertrend to fire both ways
    drift = np.zeros(n)
    k = 0
    while k < n:
        L = int(rng.integers(120, 900))
        drift[k:k + L] = rng.normal(0, 4e-4)
        k += L
    r = drift + rng.normal(0, 1.5e-3, n)
    close = p0 * np.exp(np.cumsum(r))
    open_ = np.r_[p0, close[:-1]]
    wig = np.abs(rng.normal(0, 1e-3, n)) * close
    high = np.maximum(open_, close) + wig
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1e-3, n)) * close
    vol = rng.gamma(2.0, 500.0, n)
    vol[rng.random(n) < 0.02] = 0.0           # a few flat no-trade minutes
    flat = vol == 0
    open_[flat] = high[flat] = low[flat] = close[flat] = np.r_[p0, close[:-1]][flat]
    t = START + 60 * np.arange(n, dtype="int64")
    return pd.DataFrame({"time": t, "open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})


def coin(seed: int = 7, p0: float = 1.25) -> dict:
    n = DAYS * 1440
    ltp = _walk(seed, n, p0)
    rng = np.random.default_rng(seed + 100)
    mark = ltp.copy()
    mark["close"] = ltp.close * (1 + rng.normal(0, 2e-4, n))
    mark["high"] = np.maximum(mark[["open", "close"]].max(axis=1), ltp.high)
    mark["low"] = np.minimum(mark[["open", "close"]].min(axis=1), ltp.low)
    mark["volume"] = 0.0
    hours = START + 3600 * np.arange(DAYS * 24, dtype="int64")
    fund = pd.DataFrame({"time": hours, "open": 0.0, "high": 0.0, "low": 0.0,
                         "close": rng.normal(0.005, 0.01, len(hours)), "volume": 0.0})
    return dict(ltp=ltp, mark=mark, funding=fund)


def oi(seed: int = 9) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    hours = START + 3600 * np.arange(DAYS * 24, dtype="int64")
    return pd.DataFrame({"time": hours, "oi": 1e6 * np.exp(np.cumsum(rng.normal(0, 0.02, len(hours))))})


def picks() -> pd.DataFrame:
    days = START + DAY * np.arange(1, DAYS, dtype="int64")
    return pd.DataFrame({"pick_day": days, "symbol": SYMBOL,
                         "change": np.linspace(5, 30, len(days)),
                         "turnover": np.linspace(3e5, 9e6, len(days)),
                         "rank": (np.arange(len(days)) % 10) + 1})


META = {"launch": int(pd.Timestamp(PRODUCT["launch_time"]).value // 10**9), "tag": "meme"}
