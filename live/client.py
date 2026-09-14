"""Signed REST client for Delta Exchange India private endpoints.

READ THIS BEFORE CHANGING THE RETRY LOGIC.

A GET may be retried freely. A POST /v2/orders may NOT. The dangerous case is
not an error -- it is an AMBIGUOUS one:

    we sent the order
    the venue may or may not have accepted it
    we never saw the response (socket reset, timeout, our container died)

Retrying blindly risks a second position. Giving up risks an UNMANAGED one --
a live position nothing in our system knows about, with no stop attached in
our records. Both are worse than the outage that caused them.

So the rule here is: an ambiguous write is resolved by ASKING, never by
assuming. `place_order` catches the ambiguous case, looks the order up by its
`client_order_id`, and returns the truth. That is the whole reason orders
carry a deterministic id -- see live/orders.py.

The venue's five-second signature window shapes the rest: every attempt is
signed immediately before it is sent, inside the loop, and a signature that
went stale while waiting on a backoff is abandoned locally rather than sent
to be rejected.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping

import requests

from live.auth import (Credentials, encode_body, encode_query, redact, sign,
                       signature_is_still_valid)
from live.orders import OrderRequest

log = logging.getLogger(__name__)

MAINNET = "https://api.india.delta.exchange"
TESTNET = "https://cdn-ind.testnet.deltaex.org"

#: Conservative. The venue publishes higher, but an execution client has no
#: reason to run near a limit -- being throttled mid-exit is the one time it
#: really costs something.
REQUESTS_PER_SECOND = 4.0


class VenueError(RuntimeError):
    """Base for anything the venue told us, or failed to tell us."""


class VenueRejected(VenueError):
    """The venue refused the request and we know it did not act on it.

    A 4xx that is not a rate limit. NEVER retried: the request was understood
    and declined -- bad signature, insufficient margin, reduce-only violation,
    size below the lot floor. Retrying sends the same rejection again.
    """

    def __init__(self, status: int, path: str, payload: Any) -> None:
        self.status, self.path, self.payload = status, path, payload
        code = ""
        if isinstance(payload, dict):
            err = payload.get("error")
            code = f" {err.get('code')}" if isinstance(err, dict) else f" {err}"
        super().__init__(f"HTTP {status}{code} from {path}: {str(payload)[:400]}")


class VenueUnavailable(VenueError):
    """A 5xx, a timeout, or a connection failure. Safe to retry a READ."""


class AmbiguousWrite(VenueError):
    """A write whose outcome we did not observe, and could not resolve.

    Raised only when the follow-up lookup ALSO failed. The caller must not
    assume either outcome: the correct response is to stop trading and
    reconcile against the venue before doing anything else.
    """


@dataclass
class _RateLimiter:
    per_second: float
    _last: float = 0.0

    def acquire(self) -> None:
        if self.per_second <= 0:
            return
        gap = 1.0 / self.per_second
        wait = self._last + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


class LiveClient:
    """Authenticated client. Every method here can move real money."""

    def __init__(self, credentials: Credentials, *, base_url: str = TESTNET,
                 timeout: float = 10.0, max_retries: int = 4,
                 per_second: float = REQUESTS_PER_SECOND,
                 session: requests.Session | None = None) -> None:
        # Default is TESTNET on purpose. Reaching mainnet is an explicit act.
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._limiter = _RateLimiter(per_second)
        self._session = session or requests.Session()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        net = "MAINNET" if self.base_url == MAINNET else self.base_url
        return f"LiveClient({net}, key={redact(self.credentials.key)})"

    @property
    def is_mainnet(self) -> bool:
        return self.base_url == MAINNET

    # -- transport -----------------------------------------------------------

    def _send_once(self, method: str, path: str, query: str, body: str):
        """One signed attempt. Signs LAST, so the 5s window starts here."""
        headers = sign(self.credentials, method, path, query=query, body=body)
        self._limiter.acquire()
        if not signature_is_still_valid(headers):
            # The limiter or the scheduler ate the window. Re-sign rather than
            # send something the venue will certainly reject.
            headers = sign(self.credentials, method, path, query=query, body=body)
        return self._session.request(
            method, f"{self.base_url}{path}{query}",
            data=body.encode("utf-8") if body else None,
            headers=headers, timeout=self.timeout)

    def _parse(self, resp, path: str) -> Any:
        if resp.status_code == 429:
            raise VenueUnavailable(f"rate limited on {path}")
        if 500 <= resp.status_code < 600:
            raise VenueUnavailable(f"HTTP {resp.status_code} from {path}")
        try:
            payload = resp.json()
        except ValueError:
            payload = {"raw": resp.text[:400]}
        if resp.status_code != 200:
            raise VenueRejected(resp.status_code, path, payload)
        if isinstance(payload, dict) and payload.get("success") is False:
            raise VenueRejected(resp.status_code, path, payload)
        if isinstance(payload, dict) and "result" in payload:
            return payload["result"]
        return payload

    def _read(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """A GET. Idempotent, so retried on any transient failure."""
        query = encode_query(params)
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._parse(self._send_once("GET", path, query, ""), path)
            except VenueUnavailable as exc:
                last = exc
            except requests.RequestException as exc:
                last = VenueUnavailable(f"{type(exc).__name__} on {path}")
            time.sleep(min(2.0 ** attempt, 8.0))
        raise VenueUnavailable(f"giving up on GET {path}: {last}")

    # -- reads ---------------------------------------------------------------

    def get_positions(self) -> list[dict]:
        """Open positions AS THE VENUE SEES THEM. This is the source of truth."""
        result = self._read("/v2/positions")
        return result if isinstance(result, list) else [result]

    def get_open_orders(self, product_id: int | None = None) -> list[dict]:
        params = {"states": "open"}
        if product_id is not None:
            params["product_id"] = product_id
        return self._read("/v2/orders", params) or []

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        """Did this order land? The question that resolves an ambiguous write.

        Looks across live AND historical orders, because an order that filled
        and closed between our send and our lookup is no longer 'open' -- and
        reading that as 'never landed' is exactly the double-fill we are
        avoiding.
        """
        for path in ("/v2/orders", "/v2/orders/history"):
            try:
                rows = self._read(path, {"client_order_id": client_order_id})
            except VenueRejected:
                continue
            for row in (rows or []):
                if isinstance(row, dict) and row.get("client_order_id") == client_order_id:
                    return row
        return None

    def get_balance(self) -> list[dict]:
        result = self._read("/v2/wallet/balances")
        return result if isinstance(result, list) else [result]

    # -- the write -----------------------------------------------------------

    def place_order(self, order: OrderRequest) -> dict:
        """Submit one order, exactly once.

        Sent at most once optimistically. If the outcome is not observed, the
        order is LOOKED UP by its client_order_id rather than resent. There is
        no path through this method that sends the same order twice.
        """
        body = encode_body(order.to_payload())
        path = "/v2/orders"
        log.info("placing %s %s x%d on product %s (cid=%s)%s",
                 order.order_type.value, order.side.value, order.size,
                 order.product_id, order.client_order_id,
                 " [MAINNET]" if self.is_mainnet else "")
        try:
            resp = self._send_once("POST", path, "", body)
        except requests.RequestException as exc:
            # We do not know whether it landed. Ask.
            return self._resolve_ambiguous(order, f"{type(exc).__name__}: {exc}")

        try:
            return self._parse(resp, path)
        except VenueUnavailable as exc:
            # 5xx/429 on a WRITE is ambiguous too: a 502 from a proxy can sit
            # in front of an order the matching engine already accepted.
            return self._resolve_ambiguous(order, str(exc))

    def _resolve_ambiguous(self, order: OrderRequest, why: str) -> dict:
        log.warning("order %s outcome unobserved (%s); looking it up rather "
                    "than resending", order.client_order_id, why)
        try:
            found = self.get_order_by_client_id(order.client_order_id)
        except VenueError as exc:
            raise AmbiguousWrite(
                f"order {order.client_order_id} was sent, its outcome was not "
                f"observed ({why}), and the lookup also failed ({exc}). Do not "
                f"retry: reconcile against the venue first.") from exc
        if found is not None:
            log.warning("order %s DID land: venue id %s, state %s",
                        order.client_order_id, found.get("id"), found.get("state"))
            return found
        raise AmbiguousWrite(
            f"order {order.client_order_id} was sent ({why}) and no matching "
            f"order exists at the venue. It most likely never arrived, but "
            f"this client will not resend it -- the caller must decide.")

    def cancel_order(self, order_id: int, product_id: int) -> dict:
        """Cancel is idempotent at the venue: cancelling twice is harmless."""
        body = encode_body({"id": order_id, "product_id": product_id})
        return self._parse(self._send_once("DELETE", "/v2/orders", "", body),
                           "/v2/orders")
