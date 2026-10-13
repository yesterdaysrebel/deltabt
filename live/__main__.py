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
    announces PROD loudly                   a log line nobody can miss in a
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
import os
import signal
import sys
import time
from dataclasses import replace

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
from live.config import (ENV_ENV, ConfigError, client_from_env, product_ids,
                         resolve_products, symbols_for, tick_sizes, venue_name)
from live.runtime import LiveTradingBot
from live import universe

log = logging.getLogger("live")


def load_costs(symbols, slippage_bps: float) -> dict[str, SymbolCosts]:
    cat = ProductCatalog()
    return {s: SymbolCosts.from_spec(cat.get(s), slippage_bps=slippage_bps)
            for s in symbols}


def dry_run_requested(env=None) -> bool:
    """True when the host asked for a dry run (see main)."""
    src = os.environ if env is None else env
    return (src.get("DELTABOT_DRY_RUN") or "0").strip().lower() not in ("0", "", "false", "no", "off")


async def main() -> int:
    # The bot reads execution params off its own broker, but bind_experiment()
    # compares against what the CLI wrote, so the profile has to be set here
    # too -- anything that reconstructs it in this process must reconstruct the
    # LIVE one. See app/forwardtest/identity.execution_profile.
    os.environ["DELTABOT_EXECUTION_PROFILE"] = "live"
    settings = Settings.from_env()
    configure(settings.log_level)

    # A HOST CONFIGURED FOR A DRY RUN NEVER TRADES (2026-10-02). It runs the
    # paper bot with the live venue checks in front of every entry and no
    # order path at all (live/dry_run.py); its positions are simulated, so its
    # experiment registers the PAPER execution profile -- set here and in
    # cli_entry so the two agree.
    dry_run = dry_run_requested()
    if dry_run:
        os.environ["DELTABOT_EXECUTION_PROFILE"] = "paper"

    venue = venue_name()
    client = client_from_env()
    if dry_run:
        log.warning("=" * 62)
        log.warning("DRY RUN on %s. No order is sent from this process.", venue)
        log.warning("=" * 62)
    elif client.is_prod:
        log.warning("=" * 62)
        log.warning("PROD. Orders from this process spend real money.")
        log.warning("=" * 62)

    # THE UNIVERSE COMES FROM THE VENUE, NOT FROM Settings. The arm's three
    # symbols do not exist on testnet, so a single configured list cannot be
    # right for both. resolve_products refuses to start on a partial match
    # rather than trading a smaller universe than the run claims.
    symbols, picks = await _universe(client, venue, settings.database_url)
    products = resolve_products(client, symbols)

    # AND IT HAS TO REACH THE BOT. TradingBot reads settings.symbols, so
    # resolving the venue universe here and threading it only into costs,
    # product ids and tick sizes left the two halves disagreeing: the tnet
    # host inherits DELTABOT_SYMBOLS=BEATUSD,... from the shared paper
    # user_data, so it warmed and subscribed to the PAPER universe while
    # holding testnet ids for BTC/ETH/SOL. Nothing caught it -- every health
    # check was green -- and the first entry would have died in the broker
    # with "no product_id known for BEATUSD". The venue wins, always.
    settings = replace(settings, symbols=tuple(symbols))

    strategy = resolve_strategy()
    log.info("starting", extra={"venue": venue, "symbols": list(symbols),
                                "strategy": strategy.version,
                                "config_hash": strategy.config_hash})

    costs = load_costs(symbols, settings.risk.slippage_bps)
    recorder = signal_recorder(strategy, symbols, picks, costs)
    if dry_run:
        from live.dry_run import build as build_dry_run
        from live.guards import KILL_SWITCH_PATH
        bot = build_dry_run(
            settings, client=client, venue=venue, symbols=symbols,
            products=products, strategy=strategy, costs=costs,
            repo=PostgresRepository(settings.database_url),
            lock=SingleInstanceLock(settings.database_url),
            notifier=LogNotifier(), backfiller=Backfiller(),
            kill_switch_path=KILL_SWITCH_PATH, signal_recorder=recorder)
    else:
        bot = _live_bot(settings, client, venue, symbols, products, strategy,
                        signal_recorder=recorder)
    repick_at = _next_repick() if universe.mode() == "top_gainers" else None
    return await _serve(bot, settings, stop_at=repick_at)


def _next_repick(now: float | None = None) -> float:
    """30 s past the next UTC midnight: the daily trade count and loss cap
    reset then, so the coin list is re-picked with them. The bot exits 0 and
    systemd (Restart=always, RestartSec=15) starts it again with a new list."""
    now = time.time() if now is None else now
    return (int(now) // 86400 + 1) * 86400 + 30


#: "0" turns the signal readings off (default on). They never affect a trade.
ENV_SIGNAL_FEATURES = "DELTABOT_SIGNAL_FEATURES"


def signal_recorder(strategy, symbols, picks: dict, costs: dict):
    """The recorder of readings at every signal (app/learning/recorder.py).

    Fed PLAIN DATA from here -- today's pick (rank, 24h change, turnover),
    each coin's listing time and contract value -- so app/ never imports
    live/. Returns None when switched off or when anything here fails: a
    missing measurement must never stop the bot from starting."""
    if (os.environ.get(ENV_SIGNAL_FEATURES) or "1").strip().lower() in ("0", "false", "no", "off"):
        log.info("signal readings are switched off (%s)", ENV_SIGNAL_FEATURES)
        return None
    try:
        import pandas as pd

        from app.learning.recorder import SignalRecorder
        from deltabt import signal_features as SF
        cat = ProductCatalog()
        launches = {}
        for s in symbols:
            lt = (cat.get(s) or {}).get("launch_time")
            launches[s] = int(pd.Timestamp(lt).value // 10**9) if lt else None
        return SignalRecorder(
            strategy, picks={s: {k: v for k, v in i.items() if k != "tags"}
                             for s, i in (picks or {}).items()},
            pick_day=int(time.time()) // 86400 * 86400, launches=launches,
            tags={s: SF.tag_of(i.get("tags")) for s, i in (picks or {}).items()},
            contract_values={s: c.contract_value for s, c in costs.items()})
    except Exception:                                   # noqa: BLE001
        log.exception("signal readings disabled: the recorder could not be built")
        return None


async def _universe(client, venue, database_url) -> tuple[tuple[str, ...], dict]:
    """(symbols, today's pick info): the fixed venue list (no pick info), or
    today's top gainers plus any coin still held.

    TOP GAINERS (owner, 2026-10-13): see live/universe.py. The held coins come
    from the database because recover() checks the database's open positions
    against the universe and refuses to start on a coin it does not watch.
    """
    if universe.mode() == "fixed":
        return symbols_for(venue), {}
    repo = PostgresRepository(database_url, min_size=1, max_size=1)
    await repo.connect()
    try:
        held = [p.symbol for p in await repo.load_open_positions()]
    finally:
        await repo.close()
    tickers = client._read("/v2/tickers",
                           {"contract_types": "perpetual_futures"}) or []
    return universe.pick_with_info(tickers, held)


def _live_bot(settings, client, venue, symbols, products, strategy, signal_recorder=None):
    return LiveTradingBot(
        settings,
        PostgresRepository(settings.database_url),
        load_costs(symbols, settings.risk.slippage_bps),
        strategy=strategy,
        signal_recorder=signal_recorder,
        notifier=LogNotifier(),
        backfiller=Backfiller(),
        lock=SingleInstanceLock(settings.database_url),
        client=client,
        venue=venue,
        product_ids=product_ids(products),
        tick_size=tick_sizes(products),
    )


async def _serve(bot, settings, stop_at: float | None = None) -> int:
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
    if stop_at is not None:
        loop.call_later(max(0.0, stop_at - time.time()), stopping.set)
        log.info("will exit for the daily coin re-pick at %s UTC",
                 time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(stop_at)))
    await stopping.wait()

    log.info("shutting down (signal or daily re-pick)")
    await bot.stop()
    run_task.cancel()
    api.should_exit = True
    for t in (run_task, api_task):
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(t, timeout=10)
    return 0


def cli_entry(argv: list[str]) -> int:
    """Hand a subcommand to the CLI, with the VENUE's universe in the env.

    THE CLI AND THE BOT MUST AGREE ON WHAT IS BEING TRADED, and they derive it
    from different places: the bot takes the venue universe (main() above),
    while app/cli.py builds an experiment from Settings, which reads
    DELTABOT_SYMBOLS. On a live host that variable is inherited from the shared
    PAPER user_data, so `forward-test start` registered an experiment naming
    BEATUSD/AKEUSD/BANKUSD/WIFUSD and the bot then refused it:

        configuration drift in LIVE-MANUAL_SCALP_BOTH_T3-5-20260916-5583102:
        execution_hash: aa1d4a152189e1c5 -> f251c6e7a9ceea7a;
        symbols: ['AKEUSD','BANKUSD','BEATUSD','WIFUSD']
              -> ['BTCUSD','ETHUSD','SOLUSD']

    The guard was RIGHT -- the experiment was unusable, and the bot crash-looped
    rather than trade under an identity it could not reproduce. Overriding the
    variable here is what makes the two sides reproduce each other, and it is
    done at this boundary so app/cli.py stays venue-agnostic and the paper
    stacks are untouched.

    execution_hash moves with it because execution_params() hashes the
    per-symbol halt thresholds, so fixing the universe fixes both components.

    THE VENUE MUST BE STATED, NOT INFERRED (2026-10-02). venue_name() defaults
    to testnet, which is right for the bot's own convenience paths and wrong
    here: the experiment document ran this with an env file that carried no
    DELTA_ENV, so on a PROD host `forward-test start` would have registered
    testnet's BTC/ETH/SOL while the bot traded BEAT/AKE/BANK -- the same drift
    as above, from the other direction, and invisible on tnet only because
    testnet is the default. Refusing costs nothing; guessing writes an
    experiment the bot can never bind. Help and argparse usage errors act on
    nothing, so they are let through to argparse without a venue.
    """
    usage_only = (any(a in ("-h", "--help") for a in argv)
                  or argv[:1] == ["forward-test"] and len(argv) < 2)
    if not usage_only and not (os.environ.get(ENV_ENV) or "").strip():
        raise ConfigError(
            f"{ENV_ENV} is not set. The live CLI registers experiments against "
            f"the venue's universe and must be told which venue that is; it "
            f"does not default to testnet. On a host, run_live.sh writes it "
            f"into /run/deltabt/env.")
    if universe.mode() == "top_gainers":
        # THE LIST CHANGES EVERY DAY, so an experiment registered today would
        # read as configuration drift tomorrow and the bot would refuse to
        # start. This mode runs unbound (no experiment); refuse to create one.
        if argv[:2] == ["forward-test", "start"]:
            raise ConfigError(
                "DELTABOT_UNIVERSE=top_gainers re-picks its coins daily and runs "
                "without a forward-test experiment; `forward-test start` would "
                "make the bot refuse to start the next day.")
    else:
        os.environ["DELTABOT_SYMBOLS"] = ",".join(symbols_for(venue_name()))
    # AND THE EXECUTION SURFACE, for the same reason. The CLI has no broker, so
    # it reconstructs the execution params; only this entry point knows they
    # should be the LIVE ones -- or, on a dry-run host, the PAPER ones, which
    # simulate its positions. main() sets the same, so the bot that verifies
    # the experiment and the CLI that wrote it agree.
    os.environ["DELTABOT_EXECUTION_PROFILE"] = "paper" if dry_run_requested() else "live"
    from app.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    # SUBCOMMANDS GO THROUGH THE CLI; a bare `python -m live` runs the bot.
    # This block is not decoration -- without it argv is silently DISCARDED
    # and `python -m live forward-test stop ...` starts a full trading bot
    # that never exits. On 2026-09-16 that hung the tnet retire step for 40
    # minutes while the real bot sat stopped, because the experiment document
    # stops the service before calling the CLI. The paper entry point has had
    # this dispatch all along; the live one was written to the same shape and
    # this was the piece that got left out.
    if len(sys.argv) > 1:
        sys.exit(cli_entry(sys.argv[1:]))
    sys.exit(asyncio.run(main()))
