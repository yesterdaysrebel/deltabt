"""Going live must be two environment variables and nothing else."""

from __future__ import annotations

from decimal import Decimal

import pytest

from live.client import PROD, TESTNET
from live.config import (ConfigError, client_from_env, product_ids,
                         resolve_products, tick_sizes)

CREDS = {"DELTA_API_KEY": "k", "DELTA_API_SECRET": "s"}

#: Shaped like the real /v2/products rows: the key is `id`, not `product_id`.
ROWS = [
    {"symbol": "BTCUSD", "id": 84, "tick_size": "0.1",
     "contract_value": "0.001", "state": "live"},
    {"symbol": "ETHUSD", "id": 1699, "tick_size": "0.05",
     "contract_value": "0.01", "state": "live"},
    {"symbol": "OLDUSD", "id": 7, "tick_size": "0.1",
     "contract_value": "1", "state": "expired"},
]


class FakeClient:
    def __init__(self, rows=ROWS, error=None):
        self._rows, self._error = rows, error
        self.asked = None

    def _read(self, path, params=None):
        self.asked = (path, params)
        if self._error:
            raise self._error
        return self._rows


# -- the switch --------------------------------------------------------------

def test_testnet_is_the_default():
    """Reaching prod must never be something a default did."""
    assert client_from_env(dict(CREDS)).base_url == TESTNET
    assert client_from_env(dict(CREDS)).is_prod is False


def test_prod_requires_saying_so():
    c = client_from_env(dict(CREDS, DELTA_ENV="prod"))
    assert c.base_url == PROD and c.is_prod


def test_the_venue_is_a_name_not_a_url():
    """A typo in a URL can silently point at something reachable; a typo in a
    name is refused outright."""
    with pytest.raises(ConfigError, match="not one of"):
        client_from_env(dict(CREDS, DELTA_ENV="https://evil.example"))
    with pytest.raises(ConfigError, match="not one of"):
        client_from_env(dict(CREDS, DELTA_ENV="mainet"))   # a real typo
    # Surrounding whitespace IS forgiven -- it comes from an env file, not from
    # a decision -- and must still reach prod rather than failing obscurely.
    assert client_from_env(dict(CREDS, DELTA_ENV=" prod ")).is_prod


@pytest.mark.parametrize("env", [
    {}, {"DELTA_API_KEY": "k"}, {"DELTA_API_SECRET": "s"},
    {"DELTA_API_KEY": "", "DELTA_API_SECRET": "s"},
])
def test_incomplete_credentials_are_refused(env):
    with pytest.raises(ConfigError, match="must both be set"):
        client_from_env(env)


def test_the_error_never_contains_the_secret():
    try:
        client_from_env(dict(CREDS, DELTA_ENV="nope"))
    except ConfigError as exc:
        assert "s" != str(exc) and "DELTA_API_SECRET" not in str(exc)


# -- product ids are resolved, never configured ------------------------------

def test_ids_come_from_the_venue_the_credentials_point_at():
    """THE reason this module exists. BTCUSD is product 84 on testnet and not
    necessarily on prod; a configured id would silently trade the wrong
    instrument after the endpoint changed."""
    got = resolve_products(FakeClient(), ["BTCUSD"])
    assert got["BTCUSD"].product_id == 84
    assert product_ids(got) == {"BTCUSD": 84}


def test_it_reads_id_not_product_id():
    """The products endpoint names it `id`; everything else names it
    `product_id`. Reading the wrong key finds nothing."""
    rows = [{"symbol": "BTCUSD", "product_id": 999, "tick_size": "0.1",
             "state": "live"}]
    with pytest.raises(ConfigError):
        resolve_products(FakeClient(rows), ["BTCUSD"])


def test_it_filters_to_perpetuals():
    """Unfiltered, the first page on testnet is entirely move_options and
    BTCUSD is not in it."""
    client = FakeClient()
    resolve_products(client, ["BTCUSD"])
    assert client.asked[1]["contract_types"] == "perpetual_futures"


def test_a_missing_symbol_refuses_to_start():
    """A universe quietly smaller than the experiment claims is a different
    experiment, and the failure belongs at start-up where somebody is watching."""
    with pytest.raises(ConfigError, match="not live perpetuals"):
        resolve_products(FakeClient(), ["BTCUSD", "BEATUSD"])


def test_a_symbol_that_is_not_live_counts_as_missing():
    with pytest.raises(ConfigError, match="not live perpetuals"):
        resolve_products(FakeClient(), ["OLDUSD"])


def test_tick_sizes_come_back_as_decimals():
    """Floats at a 1e-7 tick silently move the order."""
    ticks = tick_sizes(resolve_products(FakeClient(), ["BTCUSD", "ETHUSD"]))
    assert ticks["BTCUSD"] == Decimal("0.1")
    assert all(isinstance(t, Decimal) for t in ticks.values())


def test_an_unreadable_venue_is_a_config_error_not_a_crash():
    from live.client import VenueUnavailable
    with pytest.raises(ConfigError, match="could not list products"):
        resolve_products(FakeClient(error=VenueUnavailable("down")), ["BTCUSD"])


def test_no_symbols_is_refused():
    with pytest.raises(ConfigError, match="no symbols"):
        resolve_products(FakeClient(), [])


# -- the universe differs by venue, deliberately -----------------------------

def test_testnet_trades_majors_and_prod_trades_the_arm():
    """The arm's three symbols do not exist on testnet, so the testnet run is
    a rehearsal of the machinery and not the experiment."""
    from live.config import symbols_for, venue_name
    assert symbols_for("testnet") == ("BTCUSD", "ETHUSD", "SOLUSD")
    assert symbols_for("prod") == ("BEATUSD", "AKEUSD", "BANKUSD")


def test_the_two_universes_do_not_overlap():
    """If they did, a result from one could be mistaken for the other."""
    from live.config import symbols_for
    assert not set(symbols_for("testnet")) & set(symbols_for("prod"))


def test_an_unknown_venue_has_no_universe():
    from live.config import symbols_for
    with pytest.raises(ConfigError, match="no universe"):
        symbols_for("staging")


def test_venue_name_defaults_to_testnet_and_validates():
    from live.config import venue_name
    assert venue_name({}) == "testnet"
    assert venue_name({"DELTA_ENV": " PROD "}) == "prod"
    with pytest.raises(ConfigError, match="not one of"):
        venue_name({"DELTA_ENV": "mainet"})


# --- the rename, 2026-09-16: "mainnet" is now "prod" ------------------------

def test_the_old_venue_name_is_refused_rather_than_aliased():
    """A stale DELTA_ENV=mainnet must stop the bot, not quietly keep working.

    An alias would leave two spellings of real money in circulation, and the
    next comparison written against one of them is the next fail-open guard.
    Refusing turns a missed rename into a host that says so and stops.
    """
    from live.config import BASE_URLS, VENUE_SYMBOLS, ConfigError, venue_name
    assert set(BASE_URLS) == {"testnet", "prod"}
    assert set(VENUE_SYMBOLS) == {"testnet", "prod"}
    with pytest.raises(ConfigError):
        venue_name({"DELTA_ENV": "mainnet"})
    assert venue_name({"DELTA_ENV": "prod"}) == "prod"


def test_every_place_that_validates_the_venue_agrees_on_the_names():
    """Four independent statements of the allowed venues. A rename that missed
    one leaves a host that Terraform accepts and run_live.sh refuses -- or the
    reverse, which is worse."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[2]
    run_live = (root / "deploy/aws/run_live.sh").read_text()
    live_tf = (root / "infra/terraform/live.tf").read_text()

    assert re.search(r"^\s*testnet\|prod\)\s*;;", run_live, re.M), (
        "run_live.sh does not accept exactly testnet|prod")
    assert 'contains(["testnet", "prod"], var.live_venue)' in live_tf, (
        "Terraform's live_venue validation does not accept exactly testnet|prod")
    for text, where in ((run_live, "run_live.sh"), (live_tf, "live.tf")):
        code = "\n".join(l for l in text.splitlines()
                         if not l.lstrip().startswith("#"))
        assert "mainnet" not in code, f"{where} still uses mainnet outside a comment"
