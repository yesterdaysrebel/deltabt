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

    #: WHETHER A POSITION COULD STILL RESULT FROM THE ORDER THIS CONCERNS.
    #:
    #: Not "was anything sent": an IOC entry that reached the venue and
    #: died unfilled was sent, and still cannot open anything. The runtime
    #: only needs to know whether exposure can appear later.
    #:
    #: The runtime reserves an exposure slot in the database BEFORE it submits,
    #: and has to decide what to do with that slot when submission raises. It
    #: may release it only when it KNOWS nothing was placed -- releasing on an
    #: order that did land lets a second entry open beside it.
    #:
    #: It is an attribute rather than an isinstance check because the runtime
    #: lives in app/, and app/ must never import live/
    #: (tests/live_exec/test_boundary_preserved.py). So the runtime reads this
    #: with getattr, and anything that does not declare it -- a plain
    #: exception, a bug -- is treated as UNKNOWN. False is the safe default:
    #: a wrongly held slot costs one trade, a wrongly released one can double
    #: a position.
    no_position_can_result = False


class VenueRejected(VenueError):
    """The venue refused the request and we know it did not act on it.

    A 4xx that is not a rate limit. NEVER retried: the request was understood
    and declined -- bad signature, insufficient margin, reduce-only violation,
    size below the lot floor. Retrying sends the same rejection again.
    """

    #: The venue answered and declined, so no position can come of it.
    no_position_can_result = True

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

    #: Stated explicitly rather than inherited, because this is the case the
    #: whole attribute exists for: the order may well be live.
    no_position_can_result = False


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
        """EVERY open position, as the venue sees them. The source of truth.

        `/v2/positions` is the SINGLE-product endpoint and rejects a call with
        no product_id:

            HTTP 400 bad_schema -- one out of product_id or
            underlying_asset_symbol is required

        `/v2/positions/margined` is the one that enumerates. The distinction
        matters more than it reads: reconciliation exists to notice a position
        we have NO record of, and a per-product query can only ask about
        products we already know to ask about. Querying symbol by symbol would
        report "agreed" while an unknown position sat open on a product not in
        our universe -- the exact blind spot this is supposed to close.

        So this endpoint is not interchangeable with the other one, and
        get_position() below is deliberately a separate method rather than an
        optional argument here.
        """
        result = self._read("/v2/positions/margined")
        if result is None:
            return []
        return result if isinstance(result, list) else [result]

    def get_position(self, product_id: int) -> dict | None:
        """One product's position. NOT a substitute for get_positions()."""
        result = self._read("/v2/positions", {"product_id": product_id})
        if isinstance(result, list):
            return result[0] if result else None
        return result or None

    def get_open_orders(self, product_id: int | None = None) -> list[dict]:
        params = {"states": "open"}
        if product_id is not None:
            params["product_id"] = product_id
        return self._read("/v2/orders", params) or []

    def get_order_by_client_id(self, client_order_id: str) -> dict | None:
        """Did this order land? The question that resolves an ambiguous write.

        USE THE DEDICATED ENDPOINT, NOT A FILTER ON THE LIST. `GET /v2/orders`
        does NOT support `client_order_id` as a query parameter -- it ignores
        it and returns the first page of open orders. Filtering that page
        client-side happens to work for an order still resting, and FAILS for
        the case that matters: an order that filled and closed is in paginated
        history, so a one-page scan reports "never arrived" for an order that
        did land. That is the worst answer this function can give, because the
        caller's next move is to decide whether a position exists.

        The fallback scan is kept for the same reason it is not the primary:
        if the dedicated endpoint is unavailable, a partial answer beats none,
        but it is only trusted when it finds something. Not finding something
        in the fallback returns None and the caller raises rather than assumes.
        """
        # THE ID GOES IN THE PATH. `/v2/orders/client-oid` -- the name in the
        # docs navigation -- is not a route: it matches `/v2/orders/{order_id}`
        # and the venue rejects it with "Should be an integer, param order_id".
        # Confirmed on testnet 2026-09-15, then found by probing unauthenticated:
        # this path answers 401 (exists, needs auth) and every other candidate
        # spelling 404s.
        #
        # The path is part of the signed message, so the id must be in it
        # before signing -- which sign() handles, since it signs the path it is
        # given. client_order_id() emits 16 hex characters, so there is nothing
        # here that needs URL-encoding.
        try:
            row = self._read(f"/v2/orders/client_order_id/{client_order_id}")
        except VenueRejected as exc:
            log.warning("client-oid lookup rejected (%s); falling back to a "
                        "page scan, which can MISS a filled order", exc)
        else:
            if isinstance(row, dict) and row:
                return row
            if isinstance(row, list) and row:
                return row[0]
            return None

        for path in ("/v2/orders", "/v2/orders/history"):
            try:
                rows = self._read(path)
            except VenueRejected:
                continue
            for r in (rows or []):
                if isinstance(r, dict) and r.get("client_order_id") == client_order_id:
                    return r
        return None

    def order_history(self, product_id: int | None = None,
                      page_size: int = 20) -> list[dict]:
        """Recent CLOSED and cancelled orders -- where a filled order goes.

        `get_open_orders` cannot see the order that closed a position, because
        a filled order is no longer open. This is how an exit is found after
        the fact, which is the only way to learn WHY a position closed when the
        venue held the brackets.
        """
        params: dict[str, Any] = {"page_size": page_size}
        if product_id is not None:
            params["product_ids"] = product_id
        return self._read("/v2/orders/history", params) or []

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
