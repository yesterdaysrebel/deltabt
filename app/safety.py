"""The paper-only boundary, defined in exactly one place.

This module is the *definition* of what V1 must not contain. It is imported by
`app/forwardtest/preflight.py`, which re-asserts the boundary against the
deployed artifact at the gate, and mirrored by
`tests/live/test_no_live_trading.py`, which enforces it in CI.

It necessarily NAMES the forbidden things -- a scanner that cannot say what it
is looking for cannot look for it. That makes this the one module the
credential scan must skip, and it is skipped BY NAME with this rationale rather
than by a pattern that could accidentally widen. Nothing here is executable
behaviour: these are string constants and nothing imports a credential.
"""

from __future__ import annotations

#: Order-placement methods. None of these may be DEFINED or CALLED anywhere.
FORBIDDEN_ORDER_METHODS = frozenset({
    "place_order", "place_real_order", "submit_live_order",
    "send_signed_order", "submit_order_to_exchange", "create_exchange_order",
    "amend_order", "cancel_exchange_order", "place_bracket_order",
    "close_position_live",
})

#: Identifiers that only exist to authenticate. `hashlib` is deliberately NOT
#: here: it is used for the strategy config hash and the advisory-lock key,
#: neither of which is a credential.
FORBIDDEN_CREDENTIAL_NAMES = frozenset({
    "api_key", "api_secret", "apikey", "apisecret", "secret_key",
    "private_key", "signature", "signing_key", "auth_token", "bearer_token",
})

#: Signing libraries.
FORBIDDEN_IMPORTS = frozenset({"hmac", "ecdsa", "nacl"})

#: HTTP verbs that would mutate exchange state.
NON_GET_VERBS = frozenset({"post", "put", "patch", "delete"})

#: A flag-gated live mode is explicitly forbidden: the ABSENCE of the
#: capability is the boundary, not a runtime toggle.
FORBIDDEN_FLAGS = frozenset({
    "ENABLE_LIVE_TRADING", "LIVE_TRADING", "REAL_TRADING_ENABLED",
    "ALLOW_REAL_ORDERS", "TRADING_ENABLED", "PAPER_MODE",
})

#: The only module permitted to name the above, because it defines them.
BOUNDARY_DEFINITION_MODULE = "app/safety.py"
