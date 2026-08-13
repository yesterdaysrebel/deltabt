"""Data-quality screening and halt detection.

Delta does not omit illiquid minutes -- it forward-fills them as synthetic
``o=h=l=c`` bars with ``volume=0``. A backtester that trusts those bars will
"trade" thousands of minutes in which no trade was possible. Measured over 24h,
DOGEUSD was 43% synthetic and LINKUSD 35%, despite both being top-30 by
turnover, while BTCUSD and ETHUSD were 0%.

Exchange maintenance shows up as the same construct at larger scale: a long
flat zero-volume run followed by a gap-open auction. On 2026-04-12 that was 148
flat bars and then a +0.32% one-minute gap. Stops do not trigger during
maintenance, so those windows and their reopen bar must be excluded rather than
traded through.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from deltabt.config import (
    HALT_MIN_RUN_BARS,
    MAX_SYNTHETIC_RATIO,
    MIN_USABLE_DAYS,
)

log = logging.getLogger(__name__)


@dataclass
class SymbolQuality:
    symbol: str
    bars: int
    synthetic_ratio: float
    usable_start: int | None
    usable_end: int | None
    usable_days: float
    halt_bars: int
    passes: bool
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def synthetic_mask(df: pd.DataFrame) -> np.ndarray:
    """Bars the exchange forward-filled rather than traded.

    Requires both zero volume and a degenerate range: a genuinely traded bar
    can print o=h=l=c at a single price, and a zero-volume bar with a real
    range would indicate something else entirely.
    """
    volume = df["volume"].to_numpy(dtype="float64", copy=False)
    o = df["open"].to_numpy(dtype="float64", copy=False)
    h = df["high"].to_numpy(dtype="float64", copy=False)
    lo = df["low"].to_numpy(dtype="float64", copy=False)
    c = df["close"].to_numpy(dtype="float64", copy=False)
    flat = (h == lo) & (o == c) & (o == h)
    no_volume = ~(volume > 0)
    return flat & no_volume


def halt_mask(
    df: pd.DataFrame,
    *,
    min_run: int = HALT_MIN_RUN_BARS,
    mask_reopen: bool = True,
) -> np.ndarray:
    """Bars inside an exchange halt, plus the reopen bar.

    A halt is a synthetic run of at least ``min_run`` bars. The first bar after
    the run carries the auction gap -- it is a real traded bar, but no stop or
    limit could have filled inside the jump, so it is excluded from trading
    too.
    """
    synth = synthetic_mask(df)
    out = np.zeros(len(df), dtype=bool)
    if len(df) == 0:
        return out

    # Find maximal runs of True in `synth`.
    padded = np.concatenate(([False], synth, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[0::2], edges[1::2]

    for s, e in zip(starts, ends):
        if (e - s) >= min_run:
            out[s:e] = True
            if mask_reopen and e < len(out):
                out[e] = True
    return out


def tradable_mask(df: pd.DataFrame, *, min_run: int = HALT_MIN_RUN_BARS) -> np.ndarray:
    """Bars on which an order could realistically have been filled."""
    return ~synthetic_mask(df) & ~halt_mask(df, min_run=min_run)


def first_dense_bar(
    df: pd.DataFrame,
    *,
    window_bars: int = 1440,
    min_density: float = 0.95,
) -> int | None:
    """Timestamp where the series becomes continuously liquid.

    Delta serves sparse candles from a symbol's launch long before it trades
    every minute -- BTCUSD returns 1 bar for 2023-12-29 but a full 1440 from
    2024-02-05. Backtesting the sparse head produces meaningless indicator
    values, so callers should start here instead.
    """
    if df.empty:
        return None

    real = (~synthetic_mask(df)).astype("float64")
    if len(real) < window_bars:
        return int(df["time"].iloc[0]) if real.mean() >= min_density else None

    density = pd.Series(real).rolling(window_bars).mean().to_numpy()
    hits = np.flatnonzero(density >= min_density)
    if hits.size == 0:
        return None
    # `hits[0]` indexes the END of the first qualifying window.
    return int(df["time"].iloc[max(0, hits[0] - window_bars + 1)])


def assess(symbol: str, df: pd.DataFrame) -> SymbolQuality:
    """Score one symbol's 1m series for suitability."""
    if df.empty:
        return SymbolQuality(
            symbol, 0, 1.0, None, None, 0.0, 0, False, "no data"
        )

    start = first_dense_bar(df)
    if start is None:
        ratio = float(synthetic_mask(df).mean())
        return SymbolQuality(
            symbol, len(df), ratio, None, None, 0.0, 0, False,
            f"never reaches continuous liquidity (synthetic {ratio:.1%})",
        )

    usable = df.loc[df["time"] >= start]
    ratio = float(synthetic_mask(usable).mean())
    halts = int(halt_mask(usable).sum())
    end = int(usable["time"].iloc[-1])
    days = (end - start) / 86400.0

    reasons = []
    if ratio > MAX_SYNTHETIC_RATIO:
        reasons.append(f"synthetic {ratio:.1%} > {MAX_SYNTHETIC_RATIO:.0%}")
    if days < MIN_USABLE_DAYS:
        reasons.append(f"only {days:.0f}d usable < {MIN_USABLE_DAYS}d")

    return SymbolQuality(
        symbol=symbol,
        bars=len(usable),
        synthetic_ratio=ratio,
        usable_start=start,
        usable_end=end,
        usable_days=days,
        halt_bars=halts,
        passes=not reasons,
        reason="; ".join(reasons) if reasons else "ok",
    )
