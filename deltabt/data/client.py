"""REST client for the Delta Exchange India public API.

Only public endpoints are used -- no API keys, no authenticated calls, no order
placement. The quirks this client works around were each confirmed by live
calls against api.india.delta.exchange:

* ``/v2/history/candles`` caps a response at 4000 bars and truncates on the
  OLD side of the window, so pagination has to walk ``end`` backwards rather
  than ``start`` forwards.
* Results arrive newest-first.
* ``time`` is the bar OPEN in unix seconds, UTC.
* The currently-forming bar is included in the response and must be dropped.
* ``start``/``end`` are unix SECONDS; passing milliseconds fails schema
  validation.
* Resolutions are strings like ``"1m"``; bare numerics are rejected.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Iterable

import requests

from deltabt.config import (
    BASE_URL,
    MAX_CANDLES_PER_REQUEST,
    REQUESTS_PER_SECOND,
)

log = logging.getLogger(__name__)

_RESOLUTION_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "1d": 86400,
    "1w": 604800,
}


def resolution_seconds(resolution: str) -> int:
    try:
        return _RESOLUTION_SECONDS[resolution]
    except KeyError:
        raise ValueError(
            f"unsupported resolution {resolution!r}; "
            f"the API accepts {sorted(_RESOLUTION_SECONDS)}"
        ) from None


class _RateLimiter:
    """Simple thread-safe minimum-interval throttle."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self._interval


class DeltaClient:
    """Read-only client for public Delta India endpoints."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        timeout: float = 30.0,
        max_retries: int = 5,
        per_second: float = REQUESTS_PER_SECOND,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._limiter = _RateLimiter(per_second)
        self._session = session or requests.Session()
        # Delta returns 4xx for requests without a User-Agent.
        self._session.headers.update({"User-Agent": "deltabt/0.1 (backtest research)"})

    # -- low level ----------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            self._limiter.acquire()
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                self._backoff(attempt, reason=str(exc))
                continue

            if resp.status_code == 429:
                # Documented but never observed in testing. Honour the reset
                # header when present, otherwise fall back to exponential.
                reset_ms = resp.headers.get("X-RATE-LIMIT-RESET")
                delay = None
                if reset_ms:
                    try:
                        delay = min(float(reset_ms) / 1000.0, 300.0)
                    except ValueError:
                        delay = None
                if delay is None:
                    delay = min(2.0**attempt, 60.0)
                log.warning("rate limited on %s, sleeping %.1fs", path, delay)
                time.sleep(delay)
                continue

            if 500 <= resp.status_code < 600:
                last_error = RuntimeError(f"HTTP {resp.status_code} from {path}")
                self._backoff(attempt, reason=f"HTTP {resp.status_code}")
                continue

            if resp.status_code != 200:
                raise RuntimeError(
                    f"HTTP {resp.status_code} from {path} "
                    f"params={params}: {resp.text[:400]}"
                )

            payload = resp.json()
            if isinstance(payload, dict) and payload.get("success") is False:
                raise RuntimeError(f"API error from {path}: {payload}")
            return payload

        raise RuntimeError(
            f"giving up on {path} after {self.max_retries} attempts"
        ) from last_error

    @staticmethod
    def _backoff(attempt: int, *, reason: str) -> None:
        delay = min(2.0**attempt, 30.0)
        log.warning("retrying in %.1fs (%s)", delay, reason)
        time.sleep(delay)

    # -- products -----------------------------------------------------------

    def products(
        self,
        *,
        contract_types: str = "perpetual_futures",
        states: str = "live",
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        """All products matching the filters, following pagination."""
        out: list[dict[str, Any]] = []
        after: str | None = None

        while True:
            params: dict[str, Any] = {
                "contract_types": contract_types,
                "states": states,
                "page_size": page_size,
            }
            if after:
                params["after"] = after
            payload = self._get("/v2/products", params)
            batch = payload.get("result") or []
            out.extend(batch)

            meta = payload.get("meta") or {}
            after = meta.get("after")
            if not after or not batch:
                break

        return out

    def tickers(self, contract_types: str = "perpetual_futures") -> list[dict[str, Any]]:
        payload = self._get("/v2/tickers", {"contract_types": contract_types})
        return payload.get("result") or []

    # -- candles ------------------------------------------------------------

    def candles(
        self,
        symbol: str,
        resolution: str,
        start: int,
        end: int,
        *,
        drop_forming: bool = True,
    ) -> list[dict[str, Any]]:
        """Candles for ``[start, end]`` inclusive, ascending by time.

        Pages backwards because the endpoint anchors its 4000-bar cap to
        ``end``. Returns bar-open timestamps in unix seconds, UTC.
        """
        if start > end:
            raise ValueError(f"start {start} is after end {end}")

        step = resolution_seconds(resolution)
        collected: dict[int, dict[str, Any]] = {}
        cursor = end
        # A forming bar can only exist at the current wall clock.
        now = int(time.time())
        forming_open = now - (now % step)

        while cursor >= start:
            payload = self._get(
                "/v2/history/candles",
                {
                    "resolution": resolution,
                    "symbol": symbol,
                    "start": start,
                    "end": cursor,
                },
            )
            batch = payload.get("result") or []
            if not batch:
                break

            oldest = min(int(row["time"]) for row in batch)
            for row in batch:
                ts = int(row["time"])
                if start <= ts <= end:
                    collected[ts] = row

            # Terminate only when we have reached back past `start`. Row count
            # is NOT a valid stop condition: a capped page can return fewer
            # than MAX_CANDLES_PER_REQUEST rows when minutes are missing inside
            # the window (XRPUSD returns 3997 rows spanning 3996 minutes), and
            # treating that as "no more data" silently truncates the history.
            if oldest <= start:
                break
            # Step strictly past the oldest bar we just received, otherwise the
            # next page repeats it forever.
            cursor = oldest - step

        rows = [collected[ts] for ts in sorted(collected)]
        if drop_forming and rows and int(rows[-1]["time"]) >= forming_open:
            rows.pop()
        return rows

    def earliest_candle_time(
        self, symbol: str, resolution: str, *, search_from: int, search_to: int
    ) -> int | None:
        """Binary-search the first timestamp with real data.

        Delta serves sparse candles long before a symbol is continuously
        liquid, so callers should follow this with a density check rather than
        trusting the first timestamp found.
        """
        step = resolution_seconds(resolution)
        probe_window = step * MAX_CANDLES_PER_REQUEST

        lo, hi = search_from, search_to
        best: int | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            rows = self.candles(
                symbol, resolution, mid, min(mid + probe_window, search_to)
            )
            if rows:
                best = int(rows[0]["time"])
                hi = mid - probe_window
            else:
                lo = mid + probe_window
        return best


def chunk_ranges(start: int, end: int, step: int, per_request: int) -> Iterable[tuple[int, int]]:
    """Split ``[start, end]`` into request-sized windows, oldest first."""
    span = step * per_request
    cursor = start
    while cursor <= end:
        yield cursor, min(cursor + span - step, end)
        cursor += span
