"""Closed-bar assembly, validation, and the authoritative bar history.

Design decisions worth stating, because each is a place live bots go wrong:

**A bar is closed by observing a later bar, never by a single message.**
``candlestick_1m`` restreams the forming bar continuously. Rolling on "the
minute has elapsed by my local clock" makes correctness depend on NTP; rolling
on "I saw ``candle_start_time`` advance" depends only on the exchange. The
local clock is used solely as a fallback for symbols that print nothing for a
whole minute, and even then only after a grace period.

**A closed bar is immutable.** A late or out-of-order update for an
already-closed bar is dropped and counted, never applied. By the time it
arrives a signal may already have been emitted from that bar; retro-editing it
would make the audit trail a lie.

**5m bars are derived, not accumulated.** They are produced by handing the
closed 1m history to ``deltabt.strategy.resample_ohlcv`` -- the same function
the backtester uses, verified to reproduce the exchange's own 5m candles
exactly. An independently accumulated 5m bar is a second implementation of the
same thing, and two implementations are one more than can be kept in agreement.

**Nothing is silently repaired.** Every rejection, gap and drop is counted and
logged. A 5m bucket missing any of its five minutes is emitted as incomplete
and the strategy declines to evaluate on it.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

import pandas as pd

from app.market_data.normalize import Candle, CandleUpdate

log = logging.getLogger(__name__)

MINUTE = 60
FIVE_MIN = 300


@dataclass
class BuilderStats:
    updates: int = 0
    closed_1m: int = 0
    closed_5m: int = 0
    gaps: int = 0
    missing_minutes: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    invalid_ohlc: int = 0
    non_monotonic: int = 0
    incomplete_5m: int = 0
    backfilled: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class GapEvent:
    symbol: str
    expected_start: int
    actual_start: int

    @property
    def missing(self) -> int:
        return (self.actual_start - self.expected_start) // MINUTE


def validate_ohlc(c: CandleUpdate | Candle) -> str | None:
    """Return a reason string if the bar is impossible, else None."""
    o, h, l, cl, v = c.open, c.high, c.low, c.close, c.volume
    for name, val in (("open", o), ("high", h), ("low", l), ("close", cl)):
        if val is None or val != val:            # NaN
            return f"{name} is not a number"
        if val <= 0:
            return f"{name} <= 0"
    if h < l:
        return f"high {h} < low {l}"
    if not (l <= o <= h):
        return f"open {o} outside [{l}, {h}]"
    if not (l <= cl <= h):
        return f"close {cl} outside [{l}, {h}]"
    if v < 0:
        return f"volume {v} < 0"
    return None


class SymbolCandleBuilder:
    """Closed 1m and 5m bars for one symbol."""

    def __init__(self, symbol: str, *, max_bars: int = 20_000) -> None:
        self.symbol = symbol
        self.max_bars = max_bars
        self.stats = BuilderStats()
        #: Closed 1m bars, oldest first. The authoritative history.
        self.bars: deque[Candle] = deque(maxlen=max_bars)
        self._forming: CandleUpdate | None = None
        self._last_closed_start: int | None = None
        self._last_5m_start: int | None = None
        self.gaps: list[GapEvent] = []
        self._seen_gaps: set[tuple[int, int]] = set()

    # -- accessors ---------------------------------------------------------

    @property
    def last_closed_1m(self) -> Candle | None:
        return self.bars[-1] if self.bars else None

    @property
    def last_closed_1m_start(self) -> int | None:
        return self.bars[-1].start if self.bars else None

    @property
    def forming_start(self) -> int | None:
        return self._forming.start if self._forming else None

    def frame(self, limit: int | None = None) -> pd.DataFrame:
        """Closed 1m bars as the DataFrame shape the research code expects."""
        src = list(self.bars)
        if limit is not None:
            src = src[-limit:]
        if not src:
            return pd.DataFrame(
                columns=["time", "open", "high", "low", "close", "volume"]
            )
        return pd.DataFrame([b.as_row() for b in src])

    def recent_gap_count(self, *, within_seconds: float, now: int) -> int:
        cutoff = now - within_seconds
        return sum(1 for g in self.gaps if g.actual_start >= cutoff)

    # -- ingestion ---------------------------------------------------------

    def ingest(self, upd: CandleUpdate) -> list[Candle]:
        """Apply one candle update. Returns any 1m bars that just closed."""
        if upd.symbol != self.symbol:
            raise ValueError(f"{upd.symbol} update given to {self.symbol} builder")
        self.stats.updates += 1

        reason = validate_ohlc(upd)
        if reason is not None:
            self.stats.invalid_ohlc += 1
            log.warning(
                "rejecting impossible candle", extra={"symbol": self.symbol,
                "start": upd.start, "reason": reason})
            return []

        # An update for a bar we already closed. Never applied.
        if self._last_closed_start is not None and upd.start <= self._last_closed_start:
            self.stats.out_of_order += 1
            log.warning(
                "dropping update for an already-closed bar",
                extra={"symbol": self.symbol, "start": upd.start,
                       "last_closed": self._last_closed_start})
            return []

        if self._forming is None:
            self._forming = upd
            return []

        if upd.start == self._forming.start:
            if upd.updated_us < self._forming.updated_us:
                self.stats.out_of_order += 1
                return []
            if upd.updated_us == self._forming.updated_us:
                self.stats.duplicates += 1
                return []
            self._forming = upd
            return []

        if upd.start < self._forming.start:
            self.stats.out_of_order += 1
            return []

        # A later bar has begun: the forming one is now closed.
        closed = self._close_forming()
        # Record the discontinuity NOW rather than when the post-gap bar
        # eventually closes. Health checks read the gap counter, and a gap
        # reported a minute late is a minute of trading on a feed already
        # known to be holed.
        if closed:
            self._note_gap(closed[-1].start + MINUTE, upd.start)
        self._forming = upd
        return closed

    def roll_on_clock(self, now: int, *, grace: float) -> list[Candle]:
        """Close a forming bar whose minute ended and that has gone silent.

        Needed because a symbol that prints no trades for a whole minute
        produces no update to roll the previous bar. Deliberately a fallback:
        it depends on the local clock, which the message-driven path does not.
        """
        if self._forming is None:
            return []
        if now < self._forming.start + MINUTE + grace:
            return []
        return self._close_forming()

    def ingest_backfill(self, bars: list[Candle]) -> int:
        """Splice REST history in. Only bars older than what we have are used.

        Live-assembled bars are never overwritten by REST ones: the socket saw
        the market directly, and a REST bar for the same minute may reflect the
        exchange's own forward-filling.
        """
        if not bars:
            return 0
        existing = {b.start for b in self.bars}
        merged = [b for b in bars if b.start not in existing]
        if not merged:
            return 0
        allbars = sorted(list(self.bars) + merged, key=lambda b: b.start)
        self.bars = deque(allbars[-self.max_bars:], maxlen=self.max_bars)
        self.stats.backfilled += len(merged)
        if self.bars:
            self._last_closed_start = max(
                self._last_closed_start or 0, self.bars[-1].start
            )
        return len(merged)

    # -- internals ---------------------------------------------------------

    def _note_gap(self, expected: int, actual: int) -> None:
        """Record a hole once.

        Deduplicated by (expected, actual) because the same discontinuity is
        observable twice -- when the post-gap bar arrives, and again when it
        closes -- and counting it twice would overstate feed damage.
        """
        if actual <= expected:
            return
        key = (expected, actual)
        if key in self._seen_gaps:
            return
        self._seen_gaps.add(key)
        ev = GapEvent(self.symbol, expected, actual)
        self.gaps.append(ev)
        self.stats.gaps += 1
        self.stats.missing_minutes += ev.missing
        log.warning("1m gap detected",
                    extra={"symbol": self.symbol, "expected": expected,
                           "actual": actual, "missing": ev.missing})

    def _close_forming(self) -> list[Candle]:
        f = self._forming
        self._forming = None
        if f is None:
            return []

        bar = Candle(symbol=f.symbol, start=f.start, open=f.open, high=f.high,
                     low=f.low, close=f.close, volume=f.volume, source="ws")

        if self.bars and bar.start <= self.bars[-1].start:
            self.stats.non_monotonic += 1
            log.error("refusing non-monotonic closed bar",
                      extra={"symbol": self.symbol, "start": bar.start,
                             "previous": self.bars[-1].start})
            return []

        if self.bars:
            self._note_gap(self.bars[-1].start + MINUTE, bar.start)

        self.bars.append(bar)
        self._last_closed_start = bar.start
        self.stats.closed_1m += 1
        return [bar]

    # -- 5m derivation -----------------------------------------------------

    def closed_tf_for(self, last_1m_start: int, minutes: int) -> tuple[Candle | None, int]:
        """The ``minutes`` bar that closes with ``last_1m_start``, if one does.

        Generalisation of :meth:`closed_5m_for`. Returns ``(bar, missing)``;
        ``bar`` is None when ``last_1m_start`` is not the final minute of a
        bucket, and ``missing > 0`` means the strategy must not act on it.

        NOTE the buffer requirement. A bucket is assembled from the retained 1m
        bars, so ``max_bars`` has to exceed ``minutes`` by a wide margin AND
        cover the strategy's whole warm-up: a 240m rule with a 145-bar warm-up
        needs 34,800 minutes of history before its first legitimate signal.
        """
        step = minutes * MINUTE
        if (last_1m_start + MINUTE) % step != 0:
            return None, 0
        bucket = (last_1m_start // step) * step
        wanted = {bucket + i * MINUTE for i in range(minutes)}
        members = [b for b in self.bars if b.start in wanted]
        missing = minutes - len(members)
        if not members:
            return None, minutes
        members.sort(key=lambda b: b.start)
        bar = Candle(
            symbol=self.symbol, start=bucket,
            open=members[0].open,
            high=max(b.high for b in members),
            low=min(b.low for b in members),
            close=members[-1].close,
            volume=sum(b.volume for b in members),
            source="derived",
        )
        if missing:
            self.stats.incomplete_5m += 1
        else:
            if minutes == 5:
                self.stats.closed_5m += 1
                self._last_5m_start = bucket
        return bar, missing

    def closed_5m_for(self, last_1m_start: int) -> tuple[Candle | None, int]:
        """The 5m bar that closes with ``last_1m_start``, if one does.

        Returns ``(bar, missing_minutes)``. ``bar`` is None when
        ``last_1m_start`` is not the final minute of a 5m bucket. A bar with
        ``missing_minutes > 0`` is incomplete and the strategy must not act
        on it.
        """
        if (last_1m_start + MINUTE) % FIVE_MIN != 0:
            return None, 0
        bucket = (last_1m_start // FIVE_MIN) * FIVE_MIN
        wanted = {bucket + i * MINUTE for i in range(5)}
        members = [b for b in self.bars if b.start in wanted]
        missing = 5 - len(members)
        if not members:
            return None, 5
        members.sort(key=lambda b: b.start)
        bar = Candle(
            symbol=self.symbol, start=bucket,
            open=members[0].open,
            high=max(b.high for b in members),
            low=min(b.low for b in members),
            close=members[-1].close,
            volume=sum(b.volume for b in members),
            source="derived",
        )
        if missing:
            self.stats.incomplete_5m += 1
        else:
            self.stats.closed_5m += 1
            self._last_5m_start = bucket
        return bar, missing

    def frame_tf(self, minutes: int, limit: int | None = None) -> pd.DataFrame:
        """Closed, COMPLETE bars of ``minutes`` derived from the 1m history.

        Uses the backtester's own resampler so the live grid and the research
        grid are the same code path. A bucket missing ANY of its minutes is
        dropped rather than emitted short: a truncated high/low silently moves
        both the signal and the stop, and at 240m one absent minute would
        otherwise corrupt four hours of bar.
        """
        from deltabt.strategy import resample_ohlcv

        df = self.frame()
        if df.empty:
            return df
        step = minutes * MINUTE
        counts = (
            df.assign(_b=(df["time"] // step) * step)
            .groupby("_b")["time"].size()
        )
        out = resample_ohlcv(df, minutes)
        complete = counts[counts == minutes].index
        out = out[out["time"].isin(complete)].reset_index(drop=True)
        if limit is not None:
            out = out.tail(limit).reset_index(drop=True)
        return out

    def frame_5m(self, limit: int | None = None) -> pd.DataFrame:
        """Closed, COMPLETE 5m bars. Thin wrapper over :meth:`frame_tf`."""
        return self.frame_tf(5, limit)

    @property
    def last_closed_5m_start(self) -> int | None:
        return self._last_5m_start


class CandleBuilder:
    """Per-symbol builders behind one interface."""

    def __init__(self, symbols, *, max_bars: int = 20_000) -> None:
        self.builders = {s: SymbolCandleBuilder(s, max_bars=max_bars) for s in symbols}

    def __getitem__(self, symbol: str) -> SymbolCandleBuilder:
        return self.builders[symbol]

    def __contains__(self, symbol: str) -> bool:
        return symbol in self.builders

    def ingest(self, upd: CandleUpdate) -> list[Candle]:
        b = self.builders.get(upd.symbol)
        return b.ingest(upd) if b else []

    def roll_on_clock(self, now: int, *, grace: float) -> list[Candle]:
        out: list[Candle] = []
        for b in self.builders.values():
            out.extend(b.roll_on_clock(now, grace=grace))
        return out

    @property
    def last_closed_1m_start(self) -> int | None:
        """The STALEST symbol's last closed bar.

        Kept as min() because callers that report "how far behind is the
        slowest thing" want exactly this. Health must NOT use it -- see
        last_closed_1m_by_symbol and the note in evaluate_health.
        """
        vals = [b.last_closed_1m_start for b in self.builders.values()
                if b.last_closed_1m_start is not None]
        return min(vals) if vals else None

    def last_closed_1m_by_symbol(self) -> dict[str, int | None]:
        """Per symbol, so a thin instrument's silence is distinguishable from
        a dead feed. One aggregate number cannot express both."""
        return {sym: b.last_closed_1m_start for sym, b in self.builders.items()}

    def recent_gap_count(self, *, within_seconds: float, now: int) -> int:
        return sum(b.recent_gap_count(within_seconds=within_seconds, now=now)
                   for b in self.builders.values())

    def stats(self) -> dict:
        agg = BuilderStats()
        for b in self.builders.values():
            for k, v in b.stats.as_dict().items():
                setattr(agg, k, getattr(agg, k) + v)
        return agg.as_dict()
