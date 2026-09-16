"""The only things that differ between testnet and prod.

THE REQUIREMENT THIS EXISTS TO MEET

    "when testnet run is done, we just change creds and endpoints and go live
     with core changes"

So the switch must be exactly two environment variables and nothing else. That
is only true if everything else RESOLVES rather than being configured, and one
thing in particular does not survive being configured:

    PRODUCT IDS ARE NOT THE SAME ON BOTH VENUES. BTCUSD is product 84 on
    testnet. Hard-coding that, or putting it in a config file, means flipping
    the endpoint aims every order at whatever product 84 happens to be on
    prod -- an order that places successfully, fills, and is for the wrong
    instrument. Nothing downstream would notice: the id is valid, the fill is
    real, and reconciliation would agree with itself.

    So ids are looked up by SYMBOL at start-up, against whichever venue the
    credentials point at. There is no configured id anywhere.

WHAT ELSE IS DELIBERATELY NOT AN ENVIRONMENT VARIABLE

Not the symbols, the risk limits, the strategy or the stop cap. Those are the
experiment, and an experiment that changes when you change venue is two
experiments. They come from the same place the paper bot gets them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal

from live.auth import Credentials
from live.client import PROD, TESTNET, LiveClient, VenueError

log = logging.getLogger(__name__)

KEY_ENV = "DELTA_API_KEY"
SECRET_ENV = "DELTA_API_SECRET"
#: testnet | prod. A NAME, not a URL: a typo in a URL can silently point at
#: something reachable, while a typo in a name is refused outright.
ENV_ENV = "DELTA_ENV"

BASE_URLS = {"testnet": TESTNET, "prod": PROD}

#: THE UNIVERSE DIFFERS BY VENUE, AND NOT BECAUSE ANYONE WANTED IT TO.
#:
#: Testnet lists 15 perpetuals and the arm's three are not among them --
#: BEATUSD, AKEUSD and BANKUSD simply do not exist there. So the testnet run
#: CANNOT be the experiment; it is a rehearsal of the machinery on whatever
#: instruments the venue has.
#:
#: Be clear about what that does and does not buy. It exercises signing,
#: ordering, brackets, the poll loop, reconciliation and persistence. It does
#: NOT exercise the arm, and it does not exercise the conditions the arm
#: actually trades in: the thin three are illiquid (89.7% empty minutes) and
#: produced a stop that filled 6.83R past its trigger in a two-minute crash.
#: BTCUSD will do none of that, and the stop cap will never bind on testnet.
#:
#: The first time these three symbols trade through this code will therefore
#: be on prod. That is a real limit of the plan, not a detail.
VENUE_SYMBOLS = {
    "testnet": ("BTCUSD", "ETHUSD", "SOLUSD"),
    "prod": ("BEATUSD", "AKEUSD", "BANKUSD"),
}


#: THE THREE CIRCUIT BREAKERS, FOR PROD. Chosen by the operator 2026-09-15.
#:
#: They are HERE and not in infra/terraform/variables.tf on purpose. That file
#: feeds risk_hash AND the instance's user_data, which carries
#: `user_data_replace_on_change = true` -- so editing these values there would
#: replace the PAPER bot's host and then fail to bind its running experiment on
#: ConfigurationDrift. The live bot gets its own configuration; the paper run
#: is not collateral.
#:
#: WHAT EACH ONE DOES WHEN IT FIRES -- they are not the same kind of thing:
#:
#:   max_daily_loss_pct       resets at the UTC day roll (RiskState.roll_day)
#:   max_consecutive_losses   resets at the UTC day roll, and on any win
#:   max_drawdown_pct         LATCHES. TERMINAL. Only `forward-test resume
#:                            --yes` clears it, and it rebases the peak so the
#:                            run does not immediately re-halt.
#:
#: The drawdown halt is deliberately terminal, and the engine explains why: a
#: breach reached while FLAT is self-sustaining, because equity only moves when
#: a position closes. Every entry is refused, nothing can close, equity never
#: changes, and the drawdown never recovers. It is a stop-and-reassess event,
#: "not something that should quietly clear at midnight".
#:
#: SIZING NOTE, WHICH THE OPERATOR SHOULD WEIGH: the arm's own backtest had a
#: 9.1% peak drawdown UNGATED, before live slippage. A 10% halt therefore sits
#: barely above the range the strategy has already visited, and is likely to
#: fire and end the run rather than merely bound it.
PROD_RISK = {
    "max_drawdown_pct": 0.10,
    "max_daily_loss_pct": 0.03,
    "max_consecutive_losses": 4,
}


def symbols_for(env_name: str) -> tuple[str, ...]:
    name = (env_name or "testnet").strip().lower()
    if name not in VENUE_SYMBOLS:
        raise ConfigError(f"no universe defined for venue {name!r}")
    return VENUE_SYMBOLS[name]


def venue_name(env: dict[str, str] | None = None) -> str:
    src = os.environ if env is None else env
    name = (src.get(ENV_ENV) or "testnet").strip().lower()
    if name not in BASE_URLS:
        raise ConfigError(f"{ENV_ENV}={name!r} is not one of {sorted(BASE_URLS)}")
    return name


class ConfigError(RuntimeError):
    """Never contains a secret."""


@dataclass(frozen=True)
class Product:
    symbol: str
    product_id: int
    tick_size: Decimal
    contract_value: Decimal


def client_from_env(env: dict[str, str] | None = None) -> LiveClient:
    """Build the client from the two credentials and the venue name.

    Defaults to TESTNET. Reaching prod requires saying so, because the
    difference between the two is real money and a default should never be the
    reason it was reached.
    """
    src = os.environ if env is None else env
    key = src.get(KEY_ENV, "").strip()
    secret = src.get(SECRET_ENV, "").strip()
    if not key or not secret:
        raise ConfigError(f"{KEY_ENV} and {SECRET_ENV} must both be set")

    name = (src.get(ENV_ENV) or "testnet").strip().lower()
    if name not in BASE_URLS:
        raise ConfigError(
            f"{ENV_ENV}={name!r} is not one of {sorted(BASE_URLS)}")

    if name == "prod":
        log.warning("PROD: orders from this process spend real money")
    return LiveClient(Credentials(key=key, secret=secret, label=name),
                      base_url=BASE_URLS[name])


def resolve_products(client: LiveClient, symbols) -> dict[str, Product]:
    """Look every symbol up on the venue the client points at.

    Raises if any symbol is missing rather than trading the ones it found. A
    universe that is quietly smaller than the experiment claims is a different
    experiment, and the failure should happen at start-up where somebody is
    watching -- not as an absence nobody notices for a week.

    The products endpoint names the key `id`; positions, orders and tickers
    name the same thing `product_id`. And unfiltered, the first page is all
    options -- BTCUSD is not among them -- so the contract type is filtered
    here rather than paged through.
    """
    wanted = {s.upper() for s in symbols}
    if not wanted:
        raise ConfigError("no symbols to resolve")

    try:
        rows = client._read("/v2/products",
                            {"contract_types": "perpetual_futures",
                             "page_size": 500}) or []
    except VenueError as exc:
        raise ConfigError(f"could not list products: {exc}") from exc

    found: dict[str, Product] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        if sym not in wanted:
            continue
        if str(row.get("state") or "").lower() not in ("live", ""):
            log.warning("%s is not live on this venue (state=%s)",
                        sym, row.get("state"))
            continue
        # `.get`, not `["id"]`. A row missing the key must fall through to the
        # "missing symbol" error below, which names the problem -- a KeyError
        # escaping from here says only that a dict lacked a key, at start-up,
        # with nothing about which venue or which symbol.
        raw_id = row.get("id")
        if raw_id is None:
            log.warning("%s has no `id` field; ignoring the row", sym)
            continue
        found[sym] = Product(
            symbol=sym,
            product_id=int(raw_id),
            tick_size=Decimal(str(row.get("tick_size") or "0")),
            contract_value=Decimal(str(row.get("contract_value") or "1")))

    missing = sorted(wanted - set(found))
    if missing:
        raise ConfigError(
            f"these symbols are not live perpetuals on this venue: {missing}. "
            f"Refusing to start on a smaller universe than the experiment "
            f"claims -- available: {sorted(r.get('symbol') for r in rows)[:20]}")

    for p in found.values():
        log.info("resolved %s -> product_id=%d tick=%s",
                 p.symbol, p.product_id, p.tick_size)
    return found


def product_ids(products: dict[str, Product]) -> dict[str, int]:
    return {s: p.product_id for s, p in products.items()}


def tick_sizes(products: dict[str, Product]) -> dict[str, Decimal]:
    return {s: p.tick_size for s, p in products.items()}
