"""Entry point:  python -m live

The paper bot's entry point with the venue wired in. Deliberately the same
SHAPE as app/__main__.py -- API up first, signal handlers, refuse-and-exit on a
failed start -- because an operator reading one should recognise the other, and
because the failure modes it encodes were learned the expensive way.

WHAT IT DOES THAT THE PAPER ENTRY POINT DOES NOT

    resolves product ids from the venue        they differ per venue, and a
                                              configured id silently trades the
                                              wrong instrument after a switch
    picks the universe from the venue          the arm's symbols do not exist
                                              on testnet
    announces MAINNET loudly                   a log line nobody can miss in a
                                              scrollback

EXIT 1 ON A REFUSED START is the important behaviour, and it is inherited: the
circuit-breaker guard, reconciliation and the advisory lock all refuse through
the same path. Under systemd or a container supervisor that becomes a restart
loop, which is the correct outcome. A bot that cannot establish what it holds
must not trade, and crash-looping is louder than running.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

import uvicorn

from app.api.app import create_app
from app.config.settings import Settings
from app.config.variants import resolve_strategy
from app.market_data.backfill import Backfiller
from app.monitoring.logging import configure
from app.notifications.base import LogNotifier
from app.persistence.lock import SingleInstanceLock
from app.persistence.repository import PostgresRepository
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog
from live.config import (client_from_env, product_ids, resolve_products,
                         symbols_for, tick_sizes, venue_name)
from live.runtime import LiveTradingBot

log = logging.getLogger("live")


def load_costs(symbols, slippage_bps: float) -> dict[str, SymbolCosts]:
    cat = ProductCatalog()
    return {s: SymbolCosts.from_spec(cat.get(s), slippage_bps=slippage_bps)
            for s in symbols}


async def main() -> int:
    settings = Settings.from_env()
    configure(settings.log_level)

    venue = venue_name()
    client = client_from_env()
    if client.is_mainnet:
        log.warning("=" * 62)
        log.warning("MAINNET. Orders from this process spend real money.")
        log.warning("=" * 62)

    # THE UNIVERSE COMES FROM THE VENUE, NOT FROM Settings. The arm's three
    # symbols do not exist on testnet, so a single configured list cannot be
    # right for both. resolve_products refuses to start on a partial match
    # rather than trading a smaller universe than the run claims.
    symbols = symbols_for(venue)
    products = resolve_products(client, symbols)

    strategy = resolve_strategy()
    log.info("starting", extra={"venue": venue, "symbols": list(symbols),
                                "strategy": strategy.version,
                                "config_hash": strategy.config_hash})

    bot = LiveTradingBot(
        settings,
        PostgresRepository(settings.database_url),
        load_costs(symbols, settings.risk.slippage_bps),
        strategy=strategy,
        notifier=LogNotifier(),
        backfiller=Backfiller(),
        lock=SingleInstanceLock(settings.database_url),
        client=client,
        venue=venue,
        product_ids=product_ids(products),
        tick_size=tick_sizes(products),
    )

    # The API comes up FIRST and stays up even if the bot refuses to start, so
    # /readyz can report WHY rather than the probe simply timing out. On this
    # bot that matters more: "refused because the circuit breakers are off" is
    # a message somebody needs to read.
    api = uvicorn.Server(uvicorn.Config(
        create_app(bot), host=settings.api_host, port=settings.api_port,
        log_config=None, access_log=False))
    api_task = asyncio.create_task(api.serve(), name="api")

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    if not await bot.start():
        log.error("startup refused; exiting")
        await bot.stop()
        api.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(api_task, timeout=5)
        return 1

    run_task = asyncio.create_task(bot.run(), name="bot")
    await stopping.wait()

    log.info("shutdown signal received")
    await bot.stop()
    run_task.cancel()
    api.should_exit = True
    for t in (run_task, api_task):
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(t, timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
