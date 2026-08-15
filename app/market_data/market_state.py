"""Per-symbol market state: LIVE, HALTED, or REOPENING.

Delta maintenance is an expected operational event, not a data failure. It
shows up as a long run of forward-filled ``o=h=l=c``, ``volume=0`` bars,
followed by a gap-open auction: on 2026-04-12 that was 148 flat bars and then a
**+0.32% one-minute gap**.

The reopen bar is the dangerous one. Every trend indicator in the stack reads a
0.32% one-minute move as a powerful breakout, so a bot that evaluates normally
across a maintenance window will reliably take a large position into an
artifact. Hence a three-state machine rather than a boolean: the bar after the
halt is explicitly skipped, and evaluation resumes only once a genuine
post-reopen bar has closed.

Detection reuses ``deltabt.data.quality`` so live and backtest agree on what a
halt is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from deltabt.config import HALT_MIN_RUN_BARS
from deltabt.data.quality import synthetic_mask

log = logging.getLogger(__name__)

#: Symbols whose flat runs are THIN LIQUIDITY, NOT MAINTENANCE, and the shorter
#: threshold each needs to be suppressed anyway.
#:
#: The 20-bar default is calibrated on a real maintenance window: 148 flat bars
#: on 2026-04-12. It assumes flat bars arrive in long runs. On a thinly traded
#: symbol they do not -- they arrive constantly, in short bursts, because
#: minutes with no trade are forward-filled as o=h=l=c/volume=0 whatever the
#: reason. Measured over 24h on 2026-08-14, BANKUSD was 39.2% flat bars across
#: 322 runs with a maximum run of 10, so the default NEVER fires and 86% of its
#: 5m bars carry at least one fabricated minute into Supertrend, ADX and %R.
#: Nothing downstream can tell the difference; the bars look like real prices.
#:
#: 5 is below that observed maximum, so the detector suppresses entries during
#: the flat stretches instead of trading indicators computed from them. It also
#: means BANKUSD will spend a lot of time HALTED, which is the honest outcome:
#: the instrument is not continuously priced.
HALT_MIN_RUN_OVERRIDES: dict[str, int] = {"BANKUSD": 5}


def halt_min_run(symbol: str) -> int:
    """Flat-bar run length that counts as a halt for this symbol."""
    return HALT_MIN_RUN_OVERRIDES.get(symbol.upper(), HALT_MIN_RUN_BARS)


class MarketState(str, Enum):
    #: Normal trading. Signals permitted.
    LIVE = "LIVE"
    #: Inside a detected maintenance run. Signals suppressed, positions held.
    HALTED = "HALTED"
    #: The reopen bar and its auction gap. Signals still suppressed.
    REOPENING = "REOPENING"


@dataclass
class HaltEvent:
    symbol: str
    started_at: int
    ended_at: int | None = None
    flat_bars: int = 0
    reopen_bar: int | None = None
    reopen_gap_pct: float | None = None


class HaltDetector:
    """Tracks one symbol's halt state across closed 1m bars."""

    def __init__(self, symbol: str, *, min_run: int = HALT_MIN_RUN_BARS) -> None:
        self.symbol = symbol
        self.min_run = min_run
        self.state = MarketState.LIVE
        self.current: HaltEvent | None = None
        self.history: list[HaltEvent] = []
        self._flat_run = 0
        self._last_close: float | None = None

    @staticmethod
    def _is_flat(bar) -> bool:
        """One-bar equivalent of ``quality.synthetic_mask``."""
        return (
            bar.high == bar.low
            and bar.open == bar.close
            and bar.open == bar.high
            and not (bar.volume > 0)
        )

    def observe(self, bar) -> MarketState:
        """Feed one CLOSED 1m bar. Returns the state that bar was evaluated in."""
        flat = self._is_flat(bar)

        if flat:
            self._flat_run += 1
            if self._flat_run >= self.min_run and self.state is MarketState.LIVE:
                self.state = MarketState.HALTED
                self.current = HaltEvent(
                    symbol=self.symbol,
                    started_at=bar.start - (self._flat_run - 1) * 60,
                    flat_bars=self._flat_run,
                )
                log.warning("market halted", extra={
                    "symbol": self.symbol, "since": self.current.started_at,
                    "flat_bars": self._flat_run})
            elif self.current is not None:
                self.current.flat_bars = self._flat_run
            return self.state

        # A real bar. Where it lands depends on what we were in.
        was = self.state
        gap = None
        if self._last_close and self._last_close > 0:
            gap = 100.0 * (bar.close - self._last_close) / self._last_close
        self._flat_run = 0
        self._last_close = bar.close

        if was is MarketState.HALTED:
            # This is the reopen bar itself: real, but uncrossable.
            self.state = MarketState.REOPENING
            if self.current is not None:
                self.current.ended_at = bar.start
                self.current.reopen_bar = bar.start
                self.current.reopen_gap_pct = gap
            log.warning("reopen bar -- excluded from trading", extra={
                "symbol": self.symbol, "bar": bar.start, "gap_pct": gap})
            return MarketState.REOPENING

        if was is MarketState.REOPENING:
            # First genuine post-reopen bar. Trading may resume from the NEXT
            # evaluation; this bar is the one that re-establishes normality.
            self.state = MarketState.LIVE
            if self.current is not None:
                self.history.append(self.current)
                self.current = None
            log.info("market resumed", extra={"symbol": self.symbol,
                                              "bar": bar.start})
            return MarketState.LIVE

        return MarketState.LIVE

    @property
    def can_trade(self) -> bool:
        return self.state is MarketState.LIVE

    def prime_from_history(self, df: pd.DataFrame) -> None:
        """Establish state from backfilled history at startup.

        Without this a bot restarting *during* a maintenance window would come
        up in LIVE and evaluate the reopen bar as a breakout -- exactly the
        failure the detector exists to prevent.
        """
        if df is None or df.empty:
            return
        synth = synthetic_mask(df)
        closes = df["close"].to_numpy("float64")
        run = 0
        for i in range(len(df)):
            if synth[i]:
                run += 1
            else:
                run = 0
        self._flat_run = run
        self._last_close = float(closes[-1]) if len(closes) else None
        if run >= self.min_run:
            self.state = MarketState.HALTED
            self.current = HaltEvent(
                symbol=self.symbol,
                started_at=int(df["time"].iloc[-run]),
                flat_bars=run,
            )
            log.warning("started up inside a halt", extra={
                "symbol": self.symbol, "flat_bars": run})
        else:
            self.state = MarketState.LIVE
