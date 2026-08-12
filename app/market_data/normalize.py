"""Delta WebSocket payloads -> internal records.

Every quirk encoded here was observed on a live connection to
``wss://socket.india.delta.exchange`` rather than read from documentation:

* Timestamps on the socket are **microseconds**. The REST candle API uses
  **seconds**. Mixing them silently produces bar timestamps ~50,000 years in
  the future, so conversion happens exactly once, here, at the boundary.
* ``candlestick_1m`` streams the *forming* bar repeatedly, keyed by
  ``candle_start_time``. A bar is closed by observing a LATER
  ``candle_start_time``, never by trusting any single message.
* ``v2/ticker`` carries both ``close`` (last traded) and ``mark_price``. Stops
  trigger on mark; fills price off last traded. They are kept distinct all the
  way through.
* ``all_trades`` carries individual prints with microsecond timestamps. This is
  what makes live stop-vs-target ordering observable, which 1m OHLC never was.
"""

from __future__ import annotations

from dataclasses import dataclass

US = 1_000_000


def us_to_s(micros: int | float) -> int:
    """Microsecond exchange timestamp -> integer unix seconds."""
    return int(int(micros) // US)


@dataclass(frozen=True)
class Tick:
    """A price observation. ``ltp`` prices fills, ``mark`` triggers stops."""

    symbol: str
    ts_us: int
    ltp: float
    mark: float
    best_bid: float | None = None
    best_ask: float | None = None
    funding_rate: float | None = None

    @property
    def ts(self) -> int:
        return us_to_s(self.ts_us)


@dataclass(frozen=True)
class Trade:
    symbol: str
    ts_us: int
    price: float
    size: float


@dataclass(frozen=True)
class CandleUpdate:
    """A snapshot of a (possibly still forming) 1m bar."""

    symbol: str
    start: int            # bar-open, unix SECONDS, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    updated_us: int


@dataclass(frozen=True)
class Candle:
    """A CLOSED bar. The only thing the strategy is ever allowed to see."""

    symbol: str
    start: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    #: Where it came from: "ws" (assembled live) or "rest" (backfilled).
    source: str = "ws"

    def as_row(self) -> dict:
        return {
            "time": self.start, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "volume": self.volume,
        }


class NormalizeError(ValueError):
    pass


def _f(v, name: str) -> float:
    if v is None:
        raise NormalizeError(f"missing numeric field {name!r}")
    try:
        return float(v)
    except (TypeError, ValueError) as exc:
        raise NormalizeError(f"field {name!r} is not numeric: {v!r}") from exc


def normalize_ticker(msg: dict) -> Tick:
    """``v2/ticker`` -> :class:`Tick`."""
    sym = msg.get("symbol")
    if not sym:
        raise NormalizeError("ticker message has no symbol")
    quotes = msg.get("quotes") or {}

    def _opt(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return Tick(
        symbol=sym,
        ts_us=int(msg.get("timestamp") or 0),
        ltp=_f(msg.get("close"), "close"),
        mark=_f(msg.get("mark_price"), "mark_price"),
        best_bid=_opt(quotes.get("best_bid")),
        best_ask=_opt(quotes.get("best_ask")),
        funding_rate=_opt(msg.get("funding_rate")),
    )


def normalize_candle(msg: dict) -> CandleUpdate:
    """``candlestick_1m`` -> :class:`CandleUpdate`."""
    sym = msg.get("symbol")
    if not sym:
        raise NormalizeError("candle message has no symbol")
    start_us = msg.get("candle_start_time")
    if start_us is None:
        raise NormalizeError("candle message has no candle_start_time")
    start = us_to_s(start_us)
    if start % 60 != 0:
        raise NormalizeError(f"candle_start_time {start} is not minute-aligned")
    return CandleUpdate(
        symbol=sym,
        start=start,
        open=_f(msg.get("open"), "open"),
        high=_f(msg.get("high"), "high"),
        low=_f(msg.get("low"), "low"),
        close=_f(msg.get("close"), "close"),
        volume=_f(msg.get("volume", 0.0), "volume"),
        updated_us=int(msg.get("last_updated") or msg.get("timestamp") or 0),
    )


def normalize_trade(msg: dict) -> Trade:
    """``all_trades`` -> :class:`Trade`."""
    sym = msg.get("symbol")
    if not sym:
        raise NormalizeError("trade message has no symbol")
    return Trade(
        symbol=sym,
        ts_us=int(msg.get("timestamp") or 0),
        price=_f(msg.get("price"), "price"),
        size=_f(msg.get("size", 0.0), "size"),
    )
