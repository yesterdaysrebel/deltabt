"""Request signing for Delta Exchange India private endpoints.

THE SCHEME, as the venue documents it:

    signature = hex(HMAC_SHA256(api_secret, method + timestamp + path + query + body))

    headers: api-key, signature, timestamp, User-Agent

Four details that are easy to get wrong and expensive to debug against a live
account, each of which has a test:

1.  THE FIVE SECOND WINDOW. The venue rejects a signature that reaches it more
    than five seconds after the timestamp it was signed with. So the signature
    must be built immediately before the send, never cached, never built at
    request-construction time and sent after a retry backoff. `sign()` returns
    headers with a timestamp taken at call time, and `LiveClient` calls it
    inside the retry loop rather than outside it. A retry RE-SIGNS.

2.  THE QUERY STRING IS PART OF THE MESSAGE, INCLUDING ITS `?`, and it must be
    byte-identical to what goes on the wire. If the signer serialises params in
    one order and the HTTP library in another, every request fails with a
    signature error that looks like a credentials problem. We therefore
    serialise ONCE here and hand the finished string to the transport.

3.  THE BODY IS THE EXACT BYTES SENT. Same reasoning. A dict re-serialised by
    the HTTP library with different separators or key order produces a
    different signature. We serialise once, sign that string, and send that
    same string as the body.

4.  NUMBERS GO AS STRINGS. The venue documents this for price fields to
    preserve precision. `json.dumps` on a float would emit `59000.0`, and
    0.1 + 0.2 problems in a price field are not theoretical when the tick size
    is 1e-7 as it is on BEATUSD.

THE SECRET NEVER APPEARS IN A LOG. `Credentials` has no `__repr__` that
exposes it, and `redact()` is what anything diagnostic should print.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

#: The venue's tolerance for a stale signature, in seconds. Requests are signed
#: immediately before sending; this is here so the client can refuse to even
#: attempt a send it knows is already too late (a long GC pause, a suspended
#: container) rather than burn a rate-limit slot on a guaranteed rejection.
SIGNATURE_TTL_SECONDS = 5.0

#: Leave room for flight time. Signing at T and arriving at T+4.9 is a coin
#: flip; this is the budget the client refuses to exceed locally.
SIGNATURE_SAFETY_MARGIN = 1.5


class CredentialError(RuntimeError):
    """Credentials are absent or malformed. Never contains the secret."""


@dataclass(frozen=True)
class Credentials:
    """An API key pair. Deliberately not printable.

    The dataclass is frozen and `repr` is overridden because the default
    dataclass repr would put the secret into any exception traceback that
    happens to hold one of these in a local variable.
    """

    key: str
    secret: str
    #: Human label for logs -- "mainnet", "testnet", "readonly". Not sent.
    label: str = "unlabelled"

    def __post_init__(self) -> None:
        if not self.key or not self.secret:
            raise CredentialError(
                f"credentials '{self.label}' are incomplete; "
                "both key and secret are required")

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Credentials(label={self.label!r}, key={redact(self.key)})"

    __str__ = __repr__


def redact(value: str) -> str:
    """Show enough of an identifier to tell two keys apart, and no more."""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-2:]}"


def encode_query(params: Mapping[str, Any] | None) -> str:
    """Serialise query params exactly once, for both signing and sending.

    Returns "" for no params, otherwise "?a=1&b=2". Booleans are lowercased
    because `urlencode` would emit Python's "True"/"False", which the venue
    does not accept.
    """
    if not params:
        return ""
    items = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        items.append((key, str(value)))
    if not items:
        return ""
    return "?" + urllib.parse.urlencode(items)


def encode_body(payload: Mapping[str, Any] | None) -> str:
    """Serialise a request body exactly once, for both signing and sending.

    Compact separators because the signed string and the sent string must be
    identical, and the default `json.dumps` spacing is a silent way for them to
    diverge if anything re-serialises downstream.
    """
    if payload is None:
        return ""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def sign(credentials: Credentials, method: str, path: str, *,
         query: str = "", body: str = "",
         timestamp: int | None = None) -> dict[str, str]:
    """Build the auth headers for one request, signed AT CALL TIME.

    `method` is upper-cased; `path` must start with "/" and must be the path
    the request is actually sent to. `query` and `body` must be the strings
    produced by `encode_query` / `encode_body` and sent verbatim.

    Do not cache the result. See note 1 in the module docstring.
    """
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    method = method.upper()
    ts = str(int(time.time()) if timestamp is None else timestamp)

    message = f"{method}{ts}{path}{query}{body}"
    signature = hmac.new(
        credentials.secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return {
        "api-key": credentials.key,
        "signature": signature,
        "timestamp": ts,
        # The venue returns 4xx for requests with no User-Agent -- the same
        # quirk deltabt/data/client.py documents for the public endpoints.
        "User-Agent": "deltabt-live/0.1",
        "Content-Type": "application/json",
    }


def signature_is_still_valid(headers: Mapping[str, str], *,
                             now: float | None = None) -> bool:
    """Would this signature still be accepted if it arrived right now?

    Used by the client to abandon a send whose signature went stale while it
    was waiting on a rate limiter or a retry backoff, so the failure is a
    clear local one rather than an opaque 401 from the venue.
    """
    try:
        signed_at = float(headers["timestamp"])
    except (KeyError, TypeError, ValueError):
        return False
    age = (time.time() if now is None else now) - signed_at
    return age < (SIGNATURE_TTL_SECONDS - SIGNATURE_SAFETY_MARGIN)
