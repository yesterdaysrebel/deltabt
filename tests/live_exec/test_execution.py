"""Signing, idempotency, and the one behaviour that protects real money:
a lost response must never become a second position.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
import requests

from live.auth import (Credentials, encode_body, encode_query, redact, sign,
                       signature_is_still_valid)
from live.client import (AmbiguousWrite, LiveClient, VenueRejected,
                         VenueUnavailable)
from live.orders import (OrderRequest, OrderType, Side, StopTriggerMethod,
                         TimeInForce, client_order_id)

CREDS = Credentials(key="testkey", secret="testsecret", label="unit")


def an_order(**kw) -> OrderRequest:
    base = dict(product_id=27, size=10, side=Side.BUY,
                order_type=OrderType.LIMIT,
                client_order_id=client_order_id("EXP", "pos-1", "entry"),
                limit_price=Decimal("0.0939812"))
    base.update(kw)
    return OrderRequest(**base)


# -- signing -----------------------------------------------------------------

def test_signature_matches_the_documented_scheme():
    """method + timestamp + path + query + body, HMAC-SHA256, hex."""
    headers = sign(CREDS, "get", "/v2/orders", query="?product_id=1",
                   timestamp=1542110948)
    expected = hmac.new(b"testsecret",
                        b"GET1542110948/v2/orders?product_id=1",
                        hashlib.sha256).hexdigest()
    assert headers["signature"] == expected
    assert headers["timestamp"] == "1542110948"
    assert headers["api-key"] == "testkey"
    assert headers["User-Agent"]  # the venue 4xxs without one


def test_signature_covers_the_body():
    a = sign(CREDS, "POST", "/v2/orders", body='{"size":1}', timestamp=1)
    b = sign(CREDS, "POST", "/v2/orders", body='{"size":2}', timestamp=1)
    assert a["signature"] != b["signature"]


def test_path_must_be_absolute():
    with pytest.raises(ValueError):
        sign(CREDS, "GET", "v2/orders")


def test_stale_signature_is_detected():
    import time
    fresh = sign(CREDS, "GET", "/v2/orders")
    assert signature_is_still_valid(fresh)
    old = sign(CREDS, "GET", "/v2/orders", timestamp=int(time.time()) - 30)
    assert not signature_is_still_valid(old)


def test_encoding_is_stable_so_signed_and_sent_bytes_match():
    assert encode_query(None) == ""
    assert encode_query({"a": 1, "b": None}) == "?a=1"
    assert encode_query({"reduce_only": True}) == "?reduce_only=true"
    # sorted keys + compact separators, deterministic across calls
    assert encode_body({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_secret_never_appears_in_repr_or_logs():
    assert "testsecret" not in repr(CREDS)
    assert "testsecret" not in str(CREDS)
    assert redact("abcdefghijkl") == "abcd...kl"


def test_incomplete_credentials_are_refused():
    with pytest.raises(Exception):
        Credentials(key="", secret="x")


# -- orders ------------------------------------------------------------------

def test_client_order_id_is_deterministic_and_distinct():
    a = client_order_id("EXP-1", "pos-1", "entry")
    assert a == client_order_id("EXP-1", "pos-1", "entry")   # survives restart
    assert a != client_order_id("EXP-1", "pos-1", "exit")
    assert a != client_order_id("EXP-2", "pos-1", "entry")


def test_prices_cross_the_wire_as_strings():
    payload = an_order().to_payload()
    assert payload["limit_price"] == "0.0939812"
    assert isinstance(payload["limit_price"], str)


def test_floats_are_refused_not_rounded():
    with pytest.raises(TypeError, match="never float"):
        an_order(limit_price=0.0939812)


def test_bracket_legs_are_attached_to_the_entry():
    """So the venue holds the stop even if this process dies."""
    payload = an_order(bracket_stop_loss_price=Decimal("0.09"),
                       bracket_take_profit_price=Decimal("0.11")).to_payload()
    assert payload["bracket_stop_loss_price"] == "0.09"
    assert payload["bracket_take_profit_price"] == "0.11"
    assert payload["stop_trigger_method"] == "mark_price"


def test_stop_trigger_defaults_to_mark_like_the_recorded_arm():
    assert StopTriggerMethod.MARK.value == "mark_price"
    assert an_order(bracket_stop_loss_price=Decimal("0.09")
                    ).to_payload()["stop_trigger_method"] == "mark_price"


@pytest.mark.parametrize("kw", [
    dict(size=0),
    dict(order_type=OrderType.LIMIT, limit_price=None),
    dict(order_type=OrderType.MARKET),                      # price + market
    dict(post_only=True, time_in_force=TimeInForce.IOC),
])
def test_incoherent_orders_are_refused_at_construction(kw):
    with pytest.raises(ValueError):
        an_order(**kw)


def test_side_follows_position_direction():
    assert Side.for_position(1) is Side.BUY
    assert Side.for_position(-1) is Side.SELL
    assert Side.BUY.opposite() is Side.SELL


# -- the write path ----------------------------------------------------------

class FakeSession:
    """Counts sends, so 'sent exactly once' is assertable."""

    def __init__(self, *behaviours):
        self.behaviours = list(behaviours)
        self.sent: list[tuple[str, str]] = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.sent.append((method, url))
        behaviour = (self.behaviours.pop(0) if self.behaviours
                     else _resp(200, {"result": []}))
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour

    @property
    def posts(self):
        return [s for s in self.sent if s[0] == "POST"]


class _resp:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def a_client(*behaviours) -> tuple[LiveClient, FakeSession]:
    session = FakeSession(*behaviours)
    return LiveClient(CREDS, session=session, per_second=0, max_retries=2), session


def test_happy_path_sends_exactly_one_post():
    client, session = a_client(_resp(200, {"result": {"id": 5, "state": "open"}}))
    out = client.place_order(an_order())
    assert out["id"] == 5
    assert len(session.posts) == 1


def test_lost_response_is_resolved_by_lookup_not_by_resending():
    """The core protection. The order DID land; we must find it, not resend."""
    order = an_order()
    landed = {"id": 9, "state": "open", "client_order_id": order.client_order_id}
    client, session = a_client(
        requests.ConnectionError("socket reset"),   # POST, outcome unobserved
        _resp(200, {"result": [landed]}),           # lookup finds it
    )
    out = client.place_order(order)
    assert out["id"] == 9
    assert len(session.posts) == 1, "the order must never be sent twice"


def test_server_error_on_a_write_is_also_treated_as_ambiguous():
    """A 502 can sit in front of an order the matching engine accepted."""
    order = an_order()
    landed = {"id": 11, "state": "closed", "client_order_id": order.client_order_id}
    client, session = a_client(
        _resp(502, {"error": "bad gateway"}),
        _resp(200, {"result": landed}),    # found by client-oid, already closed
    )
    assert client.place_order(order)["id"] == 11
    assert len(session.posts) == 1


def test_the_lookup_uses_the_dedicated_endpoint_not_a_list_filter():
    """REGRESSION. `GET /v2/orders` ignores a client_order_id query parameter.

    Filtering the returned page client-side works for an order still resting
    and fails for one that filled and closed -- that lives in PAGINATED
    history, so a one-page scan reports "never arrived" for an order that did
    land. The caller's next move is deciding whether a position exists, so
    that is the worst answer available.
    """
    order = an_order()
    landed = {"id": 12, "state": "closed", "client_order_id": order.client_order_id}
    client, session = a_client(
        requests.ConnectionError("socket reset"),
        _resp(200, {"result": landed}),
    )
    assert client.place_order(order)["id"] == 12
    looked_up = [u for _, u in session.sent if "client_order_id" in u]
    assert looked_up, f"lookup did not use the client_order_id route: {session.sent}"
    # The id goes in the PATH. `/v2/orders/client-oid` is not a route -- it
    # matches /v2/orders/{order_id} and 400s. Found on testnet 2026-09-15.
    assert looked_up[0].endswith(f"/v2/orders/client_order_id/{order.client_order_id}")


def test_the_page_scan_is_only_a_fallback_and_is_never_trusted_when_empty():
    """If the dedicated endpoint is unavailable a partial answer beats none,
    but NOT finding the order in one page must not be read as 'never sent'."""
    order = an_order()
    client, session = a_client(
        requests.ConnectionError("socket reset"),
        _resp(404, {"success": False, "error": "not_found"}),  # client-oid gone
        _resp(200, {"result": []}),                            # open: empty page
        _resp(200, {"result": []}),                            # history: empty
    )
    with pytest.raises(AmbiguousWrite, match="never arrived"):
        client.place_order(order)
    assert len(session.posts) == 1


def test_unresolvable_ambiguity_raises_and_does_not_resend():
    order = an_order()
    client, session = a_client(
        requests.ConnectionError("socket reset"),
        *[requests.ConnectionError("still down")] * 12,
    )
    with pytest.raises(AmbiguousWrite, match="reconcile"):
        client.place_order(order)
    assert len(session.posts) == 1


def test_order_that_never_arrived_raises_rather_than_silently_retrying():
    order = an_order()
    client, session = a_client(
        requests.ConnectionError("socket reset"),
        _resp(200, {"result": []}),
        _resp(200, {"result": []}),
    )
    with pytest.raises(AmbiguousWrite, match="never arrived"):
        client.place_order(order)
    assert len(session.posts) == 1


def test_a_rejection_is_never_retried():
    """Insufficient margin twice is still insufficient margin."""
    client, session = a_client(
        _resp(400, {"success": False, "error": {"code": "insufficient_margin"}}))
    with pytest.raises(VenueRejected, match="insufficient_margin"):
        client.place_order(an_order())
    assert len(session.posts) == 1


def test_listing_positions_uses_the_endpoint_that_enumerates():
    """REGRESSION, found on testnet 2026-09-15.

    `/v2/positions` is the SINGLE-product endpoint and 400s without a
    product_id. Reconciliation exists to notice a position we have no record
    of, so it must enumerate: a per-product query can only ask about products
    we already know to ask about, and would report "agreed" while an unknown
    position sat open on a product outside our universe.
    """
    client, session = a_client(_resp(200, {"result": []}))
    client.get_positions()
    assert "/v2/positions/margined" in session.sent[0][1]


def test_a_single_product_position_is_a_separate_call():
    client, session = a_client(_resp(200, {"result": [{"size": 1}]}))
    assert client.get_position(27) == {"size": 1}
    url = session.sent[0][1]
    assert "/v2/positions?product_id=27" in url
    assert "margined" not in url


def test_reads_are_retried_then_give_up():
    client, session = a_client(_resp(500, {}), _resp(500, {}))
    with pytest.raises(VenueUnavailable):
        client.get_positions()
    assert len(session.sent) == 2


def test_client_defaults_to_testnet_and_says_so():
    client = LiveClient(CREDS)
    assert not client.is_mainnet
    assert "testsecret" not in repr(client)
