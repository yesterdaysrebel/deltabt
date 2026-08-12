"""THE SAFETY TEST. The build fails if V1 gains any live-trading capability.

Section 1 of the brief: the absence of an order-placement method IS the safety
boundary. This test enforces that against the shipped source.

It parses the AST rather than grepping. Grep cannot tell a docstring that
*mentions* ``place_order`` from a function that *is* ``place_order``, so a
grep-based check either fails on its own documentation or is loosened until it
proves nothing. The AST distinguishes them exactly.

Scoping is deliberate. ``cancel_order`` and ``modify_order`` are legitimate
PAPER broker methods -- section 9 requires them -- and forbidden on the
EXCHANGE adapter. So the exchange-facing modules get the strict rule and the
simulator gets the rule that applies to a simulator: it may not reach the
network at all.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Everything that talks to Delta. Nothing here may place, cancel or amend an
#: order, authenticate, sign, or issue a non-GET request.
EXCHANGE_MODULES = [
    ROOT / "app" / "market_data",
    ROOT / "deltabt" / "data",
]

#: The whole shipped application. Credentials and signing are banned outright.
SHIPPED = [ROOT / "app", ROOT / "deltabt"]

ORDER_PLACEMENT_NAMES = {
    "place_order", "place_real_order", "submit_live_order", "send_signed_order",
    "submit_order_to_exchange", "create_exchange_order", "amend_order",
    "cancel_exchange_order", "place_bracket_order", "close_position_live",
}

#: Names that only exist to authenticate. `hashlib` is NOT here: it is used for
#: the strategy config hash and the advisory-lock key, neither of which is a
#: credential.
CREDENTIAL_NAMES = {
    "api_key", "api_secret", "apikey", "apisecret", "secret_key",
    "private_key", "signature", "signing_key", "auth_token", "bearer_token",
}

FORBIDDEN_IMPORTS = {"hmac", "ecdsa", "nacl"}

NON_GET_VERBS = {"post", "put", "patch", "delete"}

#: A live-trading feature flag is explicitly forbidden by section 1.
FORBIDDEN_FLAGS = {
    "ENABLE_LIVE_TRADING", "LIVE_TRADING", "REAL_TRADING_ENABLED",
    "ALLOW_REAL_ORDERS", "TRADING_ENABLED", "PAPER_MODE",
}


def py_files(paths) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for p in paths:
        out.extend(sorted(p.rglob("*.py")))
    return [f for f in out if "__pycache__" not in f.parts]


def parse(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def rel(path: pathlib.Path) -> str:
    return str(path.relative_to(ROOT))


ALL_SHIPPED = py_files(SHIPPED)
ALL_EXCHANGE = py_files(EXCHANGE_MODULES)


def test_there_is_shipped_source_to_scan():
    """Guards against the scan silently passing because it found nothing."""
    assert len(ALL_SHIPPED) > 30
    assert len(ALL_EXCHANGE) >= 5


# =====================================================================
# NO ORDER-PLACEMENT CAPABILITY
# =====================================================================


@pytest.mark.parametrize("path", ALL_SHIPPED, ids=rel)
def test_no_order_placement_function_is_defined(path):
    tree = parse(path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in ORDER_PLACEMENT_NAMES, (
                f"{rel(path)}:{node.lineno} defines {node.name}() -- V1 must "
                f"have no order-placement capability at all")


@pytest.mark.parametrize("path", ALL_SHIPPED, ids=rel)
def test_no_order_placement_function_is_called(path):
    tree = parse(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        assert name not in ORDER_PLACEMENT_NAMES, (
            f"{rel(path)}:{node.lineno} calls {name}()")


@pytest.mark.parametrize("path", ALL_EXCHANGE, ids=rel)
def test_exchange_adapter_defines_no_order_methods_at_all(path):
    """Stricter for the exchange layer: not even cancel or amend."""
    banned = ORDER_PLACEMENT_NAMES | {"cancel_order", "modify_order",
                                      "submit_order", "create_order"}
    tree = parse(path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in banned, (
                f"{rel(path)}:{node.lineno} defines {node.name}() on the "
                f"exchange adapter, which must be read-only")


# =====================================================================
# NO CREDENTIALS, NO SIGNING
# =====================================================================


@pytest.mark.parametrize("path", ALL_SHIPPED, ids=rel)
def test_no_credential_identifiers(path):
    tree = parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id.lower() not in CREDENTIAL_NAMES, (
                f"{rel(path)}:{node.lineno} references {node.id}")
        elif isinstance(node, ast.Attribute):
            assert node.attr.lower() not in CREDENTIAL_NAMES, (
                f"{rel(path)}:{node.lineno} references .{node.attr}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for a in list(node.args.args) + list(node.args.kwonlyargs):
                assert a.arg.lower() not in CREDENTIAL_NAMES, (
                    f"{rel(path)}:{node.lineno} takes a {a.arg} argument")


@pytest.mark.parametrize("path", ALL_SHIPPED, ids=rel)
def test_no_signing_libraries_are_imported(path):
    tree = parse(path)
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module.split(".")[0]]
        for m in mods:
            assert m not in FORBIDDEN_IMPORTS, (
                f"{rel(path)}:{node.lineno} imports {m} -- V1 signs nothing")


@pytest.mark.parametrize("path", ALL_SHIPPED, ids=rel)
def test_no_credential_environment_variables_are_read(path):
    """`os.environ["API_SECRET"]` and friends."""
    src = path.read_text()
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.upper()
            for bad in ("API_KEY", "API_SECRET", "DELTA_SECRET", "PRIVATE_KEY"):
                assert bad not in v or "DELTABOT_TEST" in v, (
                    f"{rel(path)}:{node.lineno} mentions {bad} as a string")
    assert "getenv('API" not in src and 'getenv("API' not in src


@pytest.mark.parametrize("path", ALL_SHIPPED, ids=rel)
def test_no_live_trading_feature_flag(path):
    """Section 1 forbids a flag-gated live mode outright."""
    for node in ast.walk(parse(path)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value.strip().upper() not in FORBIDDEN_FLAGS, (
                f"{rel(path)}:{node.lineno} defines a live-trading flag")
        if isinstance(node, ast.Name):
            assert node.id.upper() not in FORBIDDEN_FLAGS, (
                f"{rel(path)}:{node.lineno} references {node.id}")


# =====================================================================
# READ-ONLY NETWORK ACCESS
# =====================================================================


@pytest.mark.parametrize("path", ALL_SHIPPED, ids=rel)
def test_no_non_get_http_requests_anywhere(path):
    tree = parse(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr.lower()
        if attr not in NON_GET_VERBS:
            continue
        # `dict.pop`, `list.append` etc. are not HTTP. Only flag calls whose
        # receiver looks like an HTTP client or session.
        recv = node.func.value
        recv_name = (recv.attr if isinstance(recv, ast.Attribute)
                     else getattr(recv, "id", "")).lower()
        assert not any(t in recv_name for t in
                       ("session", "client", "http", "requests", "aiohttp",
                        "httpx", "conn")), (
            f"{rel(path)}:{node.lineno} issues a {attr.upper()} via "
            f"{recv_name} -- V1 is read-only")


@pytest.mark.parametrize("path", ALL_EXCHANGE, ids=rel)
def test_exchange_modules_reference_only_public_endpoints(path):
    """Authenticated Delta paths must not appear anywhere."""
    private = ("/v2/orders", "/v2/positions/", "/v2/wallet", "/v2/fills",
               "/v2/orders/batch", "/v2/positions/change_margin")
    src = path.read_text()
    for p in private:
        assert p not in src, f"{rel(path)} references the private endpoint {p}"


def test_rest_client_only_has_a_get_helper():
    """The one HTTP client in the project is structurally GET-only."""
    from deltabt.data import client
    tree = parse(pathlib.Path(client.__file__))
    verbs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr.lower() in NON_GET_VERBS | {"get", "request"}:
                recv = node.func.value
                if getattr(recv, "attr", "") == "_session" or \
                        getattr(recv, "id", "") == "session":
                    verbs.add(node.func.attr.lower())
    assert verbs <= {"get"}, f"the REST client issues {verbs}"


# =====================================================================
# LAYER SEPARATION
# =====================================================================


def test_strategy_cannot_reach_execution():
    """A strategy must never be able to create an order.

    Enforced by import graph, not by convention: the strategy package does not
    import the broker or the intent type, so there is no expression a strategy
    could write that produces one.
    """
    for path in py_files([ROOT / "app" / "strategy"]):
        for node in ast.walk(parse(path)):
            mod = None
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            if not mod:
                continue
            assert "execution" not in mod, (
                f"{rel(path)} imports {mod} -- the strategy layer must not "
                f"reach execution")
            assert "paper_broker" not in mod


def test_market_data_never_imports_execution():
    for path in ALL_EXCHANGE:
        src = path.read_text()
        assert "paper_broker" not in src
        assert "ApprovedOrderIntent" not in src


def test_paper_broker_makes_no_network_calls():
    """The simulator must not be able to reach anything, correct URL or not."""
    from app.execution import paper_broker
    tree = parse(pathlib.Path(paper_broker.__file__))
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module.split(".")[0]]
        for m in mods:
            assert m not in {"requests", "httpx", "aiohttp", "websockets",
                             "socket", "urllib", "http"}, (
                f"the paper broker imports {m}")


def test_no_module_defines_an_exchange_order_url():
    for path in ALL_SHIPPED:
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value.lower()
                if "delta.exchange" in v:
                    assert "/orders" not in v and "/wallet" not in v, (
                        f"{rel(path)} embeds a trading endpoint: {node.value}")


# =====================================================================
# SETTINGS
# =====================================================================


def test_settings_have_no_credential_fields():
    from app.config.settings import RiskConfig, Settings
    for cls in (Settings, RiskConfig):
        for name in cls.__dataclass_fields__:
            assert not any(t in name.lower() for t in
                           ("key", "secret", "token", "credential", "auth")), (
                f"{cls.__name__} has a {name} field")


def test_settings_have_no_live_trading_toggle():
    from app.config.settings import Settings
    for name in Settings.__dataclass_fields__:
        assert "live" not in name.lower() or name == "live_data_only", name
