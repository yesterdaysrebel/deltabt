"""Read-only WebSocket feed for Delta Exchange India.

SAFETY: this module is the entire exchange integration. It contains no
authentication, no signing, no credentials and no send path other than
subscribe/unsubscribe/ping. There is no order-placement method here or anywhere
else in the process, and that absence -- not a configuration flag -- is the
safety boundary. ``tests/live/test_no_live_trading.py`` enforces it against the
shipped source.

Protocol facts, all observed live rather than taken from documentation:

* subscribe with ``{"type": "subscribe", "payload": {"channels": [...]}}``;
  the server acknowledges with a ``subscriptions`` message.
* ``candlestick_1m`` streams the forming bar; ``candle_start_time`` and every
  other timestamp are in **microseconds**.
* ``v2/ticker`` carries ``close`` (last traded) and ``mark_price`` together.
* ``all_trades`` sends an ``all_trades_snapshot`` of recent prints on
  subscribe, then individual ``all_trades`` messages.

A connection that stops delivering while its socket stays open is the failure
mode that matters, and it is invisible to every process-level check. This
client therefore imposes its own receive deadline and treats expiry as a fatal
connection error rather than waiting on TCP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Awaitable, Callable

import websockets

from app.config.settings import MAX_WS_SILENCE, WS_URL

log = logging.getLogger(__name__)

CHANNELS = ("v2/ticker", "candlestick_1m", "all_trades")


class FeedStats:
    def __init__(self) -> None:
        self.messages = 0
        self.reconnects = 0
        self.stale_events = 0
        self.errors = 0
        self.last_message_at: float = 0.0
        self.connected: bool = False
        self.connected_since: float | None = None

    def as_dict(self) -> dict:
        return {
            "websocket_messages": self.messages,
            "websocket_reconnects": self.reconnects,
            "stale_feed_events": self.stale_events,
            "websocket_errors": self.errors,
            "last_message_at": self.last_message_at,
            "connected": self.connected,
            "connected_since": self.connected_since,
        }

    @property
    def seconds_since_last_message(self) -> float:
        if not self.last_message_at:
            return float("inf")
        return time.time() - self.last_message_at


class StaleFeedError(RuntimeError):
    """No message arrived within the receive deadline."""


class DeltaMarketFeed:
    """Maintains one subscribed connection, reconnecting forever."""

    def __init__(
        self,
        symbols,
        on_message: Callable[[dict], Awaitable[None] | None],
        *,
        url: str = WS_URL,
        channels=CHANNELS,
        recv_timeout: float = MAX_WS_SILENCE,
        max_backoff: float = 60.0,
        connect=None,
    ) -> None:
        self.symbols = list(symbols)
        self.on_message = on_message
        self.url = url
        self.channels = list(channels)
        self.recv_timeout = recv_timeout
        self.max_backoff = max_backoff
        self.stats = FeedStats()
        self._stop = asyncio.Event()
        #: Injection point for tests -- a fake connect() returning an async
        #: context manager with .send()/.recv(). Keeps reconnect logic testable
        #: without a network.
        self._connect = connect or self._default_connect

    def _default_connect(self):
        return websockets.connect(
            self.url, ping_interval=20, ping_timeout=20, close_timeout=5,
            max_queue=4096,
        )

    def subscribe_payload(self) -> dict:
        return {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {"name": ch, "symbols": list(self.symbols)}
                    for ch in self.channels
                ]
            },
        }

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Connect, subscribe, pump messages. Reconnects until stopped."""
        attempt = 0
        while not self._stop.is_set():
            try:
                async with self._connect() as ws:
                    self.stats.connected = True
                    self.stats.connected_since = time.time()
                    attempt = 0
                    await ws.send(json.dumps(self.subscribe_payload()))
                    log.info("subscribed", extra={"symbols": self.symbols,
                                                  "channels": self.channels})
                    await self._pump(ws)
            except asyncio.CancelledError:
                raise
            except StaleFeedError:
                self.stats.stale_events += 1
                log.error("feed went silent with the socket still open; "
                          "forcing a reconnect")
            except Exception as exc:                      # noqa: BLE001
                self.stats.errors += 1
                log.error("feed error: %s", exc)
            finally:
                self.stats.connected = False
                self.stats.connected_since = None

            if self._stop.is_set():
                break
            self.stats.reconnects += 1
            attempt += 1
            delay = min(self.max_backoff, 2.0 ** min(attempt, 6))
            delay *= 0.5 + random.random()      # jitter; thundering herds
            log.warning("reconnecting in %.1fs (attempt %d)", delay, attempt)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _pump(self, ws) -> None:
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.recv_timeout)
            except asyncio.TimeoutError as exc:
                raise StaleFeedError(
                    f"no message for {self.recv_timeout}s"
                ) from exc

            self.stats.messages += 1
            self.stats.last_message_at = time.time()
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                self.stats.errors += 1
                log.warning("undecodable frame dropped")
                continue

            result = self.on_message(msg)
            if asyncio.iscoroutine(result):
                await result
