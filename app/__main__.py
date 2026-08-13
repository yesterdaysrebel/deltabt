"""Entry point:  python -m app

Starts the bot and the HTTP surface in one process, one event loop, one
instance. Exits non-zero if another instance holds the advisory lock or if
recovery could not be reconciled -- in Kubernetes that is a CrashLoopBackOff,
which is the correct outcome: a bot that cannot establish what it holds must
not trade.
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
from app.config.strategy import FROZEN
from app.market_data.backfill import Backfiller
from app.monitoring.logging import configure
from app.notifications.base import LogNotifier
from app.persistence.lock import SingleInstanceLock
from app.persistence.repository import PostgresRepository
from app.runtime.bot import TradingBot
from deltabt.costs import SymbolCosts
from deltabt.data.store import ProductCatalog

log = logging.getLogger("app")


def load_costs(symbols, slippage_bps: float) -> dict[str, SymbolCosts]:
    """Per-symbol contract specs from the public products endpoint.

    Fees, tick size, contract value and funding interval all differ per symbol
    on Delta India, so none of them may be hardcoded.
    """
    cat = ProductCatalog()
    return {s: SymbolCosts.from_spec(cat.get(s), slippage_bps=slippage_bps)
            for s in symbols}


async def main() -> int:
    settings = Settings.from_env()
    configure(settings.log_level)
    log.info("starting", extra={"symbols": list(settings.symbols),
                                "strategy": FROZEN.version,
                                "config_hash": FROZEN.config_hash})

    bot = TradingBot(
        settings,
        PostgresRepository(settings.database_url),
        load_costs(settings.symbols, settings.risk.slippage_bps),
        strategy=FROZEN,
        notifier=LogNotifier(),
        backfiller=Backfiller(),
        lock=SingleInstanceLock(settings.database_url),
    )

    # The API comes up FIRST and stays up even if the bot refuses to start, so
    # /readyz can report why rather than the probe simply timing out.
    api = uvicorn.Server(uvicorn.Config(
        create_app(bot), host=settings.api_host, port=settings.api_port,
        log_config=None, access_log=False))
    api_task = asyncio.create_task(api.serve(), name="api")

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    started = await bot.start()
    if not started:
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
    # Subcommands go through the CLI; a bare `python -m app` still runs the bot
    # so existing deployment manifests keep working.
    if len(sys.argv) > 1:
        from app.cli import main as cli_main
        sys.exit(cli_main(sys.argv[1:]))
    sys.exit(asyncio.run(main()))
