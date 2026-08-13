"""Forward-test control surface.

    python -m app forward-test preflight     # gate; changes nothing
    python -m app forward-test start         # register an experiment (needs preflight)
    python -m app forward-test status
    python -m app forward-test stop  --reason "..."
    python -m app forward-test report --day YYYY-MM-DD | --final
    python -m app run                        # the bot itself

AUDIT FINDING F6. There was no gate at all -- ``python -m app`` started the bot
unconditionally. ``start`` now refuses unless preflight passes, and refuses to
start a second experiment while one is RUNNING.

Nothing here can place an exchange order. It reads and writes PostgreSQL and
public market data only.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from app.config.settings import Settings
from app.config.strategy import FROZEN
from app.forwardtest.identity import build_identity
from app.forwardtest.preflight import run_preflight
from app.market_data.backfill import Backfiller
from app.monitoring.logging import configure
from app.persistence.repository import PostgresRepository

EXEC_PARAMS = {"entry_ttl_seconds": 90, "max_entry_deviation": 0.25,
               "min_fill_rr": 1.7}


def default_experiment_id(now: datetime | None = None) -> str:
    now = now or datetime.now(tz=timezone.utc)
    return f"H-WPR-1-PAPER-{now:%Y%m%d}"


def _load_costs(symbols, slippage_bps: float):
    from deltabt.costs import SymbolCosts
    from deltabt.data.store import ProductCatalog
    cat = ProductCatalog()
    return {s: SymbolCosts.from_spec(cat.get(s), slippage_bps=slippage_bps)
            for s in symbols}


async def _preflight(settings: Settings, *, check_feed: bool = True):
    repo = PostgresRepository(settings.database_url)
    try:
        await repo.connect()
    except Exception as exc:                            # noqa: BLE001
        print(f"[FAIL] database reachable  {exc}")
        print("\nPREFLIGHT FAILED (1 blocking of 1 check)")
        return None, None
    try:
        costs = _load_costs(settings.symbols, settings.risk.slippage_bps)
    except Exception as exc:                            # noqa: BLE001
        costs = None
        print(f"note: contract specs unavailable ({exc})")

    from app.clock import MarketClock
    clock = MarketClock()
    if costs:
        # A clock with no market data is not initialised. Seed it from the
        # newest bar the REST endpoint serves, which is exchange time.
        try:
            bars = await Backfiller().fetch(
                settings.symbols[0],
                int(datetime.now(tz=timezone.utc).timestamp()) - 3600,
                int(datetime.now(tz=timezone.utc).timestamp()))
            if bars:
                clock.observe(bars[-1].start + 60)
        except Exception:                               # noqa: BLE001
            pass

    report = await run_preflight(
        settings, FROZEN, repo=repo, costs=costs, clock=clock,
        backfiller=Backfiller(), dsn=settings.database_url,
        check_feed=check_feed)
    return report, repo


async def cmd_preflight(args) -> int:
    settings = Settings.from_env()
    report, repo = await _preflight(settings, check_feed=not args.offline)
    if report is None:
        return 1
    print(report.render())
    await repo.close()
    return 0 if report.ok else 1


async def cmd_start(args) -> int:
    settings = Settings.from_env()
    report, repo = await _preflight(settings, check_feed=not args.offline)
    if report is None:
        return 1
    print(report.render())
    if not report.ok:
        print("\nRefusing to start the forward test.")
        await repo.close()
        return 1

    exp_id = args.experiment_id or default_experiment_id()
    ident = build_identity(
        exp_id, FROZEN, settings.risk,
        {**EXEC_PARAMS, "slippage_bps": settings.risk.slippage_bps},
        settings.symbols)
    created = await repo.create_experiment(ident, planned_days=args.days)
    if not created:
        active = await repo.active_experiment()
        print(f"\nRefused: " + (
            f"experiment {active['experiment_id']} is already RUNNING. "
            f"Stop it before starting another."
            if active else f"experiment id {exp_id} already exists."))
        await repo.close()
        return 1

    print(f"""
EXPERIMENT STARTED
  experiment_id    {ident.experiment_id}
  planned_days     {args.days}
  strategy         {ident.strategy_version}
  strategy_hash    {ident.strategy_hash}
  risk_hash        {ident.risk_hash}
  execution_hash   {ident.execution_hash}
  config_hash      {ident.config_hash}
  git_sha          {ident.git_sha}{'  (DIRTY)' if ident.git_dirty else ''}
  symbols          {', '.join(ident.symbols)}

PAPER TRADING ONLY. No exchange order can be placed by this process.
Now run the bot:  python -m app run""")
    await repo.close()
    return 0


async def cmd_status(args) -> int:
    settings = Settings.from_env()
    repo = PostgresRepository(settings.database_url)
    await repo.connect()
    active = await repo.active_experiment()
    if not active:
        print("no experiment is RUNNING")
        await repo.close()
        return 1
    from app.reports.builder import build_report
    rep = await build_report(repo, active["experiment_id"])
    print(rep.render_status())
    await repo.close()
    return 0


async def cmd_stop(args) -> int:
    settings = Settings.from_env()
    repo = PostgresRepository(settings.database_url)
    await repo.connect()
    active = await repo.active_experiment()
    if not active:
        print("no experiment is RUNNING")
        await repo.close()
        return 1
    await repo.stop_experiment(active["experiment_id"], args.reason)
    print(f"stopped {active['experiment_id']}: {args.reason}")
    print("Open paper positions are LEFT OPEN -- closing them here would "
          "fabricate exits the strategy never produced.")
    await repo.close()
    return 0


async def cmd_report(args) -> int:
    settings = Settings.from_env()
    repo = PostgresRepository(settings.database_url)
    await repo.connect()
    exp = (await repo.get_experiment(args.experiment_id)
           if args.experiment_id else await repo.active_experiment())
    if not exp:
        print("no such experiment")
        await repo.close()
        return 1
    from app.reports.builder import build_report
    rep = await build_report(repo, exp["experiment_id"], day=args.day)
    print(rep.render_final() if args.final else rep.render_daily())
    await repo.close()
    return 0


async def cmd_run(args) -> int:
    from app.__main__ import main as run_bot
    return await run_bot()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app",
                                description="Delta India PAPER-trading bot")
    sub = p.add_subparsers(dest="command", required=True)

    ft = sub.add_parser("forward-test", help="control the 30-day paper test")
    fts = ft.add_subparsers(dest="action", required=True)

    pre = fts.add_parser("preflight", help="run the gate; change nothing")
    pre.add_argument("--offline", action="store_true",
                     help="skip the live market-data reachability check")
    pre.set_defaults(func=cmd_preflight)

    st = fts.add_parser("start", help="register a new experiment")
    st.add_argument("--experiment-id", default=None)
    st.add_argument("--days", type=int, default=30)
    st.add_argument("--offline", action="store_true")
    st.set_defaults(func=cmd_start)

    stat = fts.add_parser("status", help="how the running experiment is doing")
    stat.set_defaults(func=cmd_status)

    stop = fts.add_parser("stop", help="end the running experiment")
    stop.add_argument("--reason", required=True)
    stop.set_defaults(func=cmd_stop)

    rep = fts.add_parser("report", help="daily or final report")
    rep.add_argument("--experiment-id", default=None)
    rep.add_argument("--day", default=None, help="UTC date, YYYY-MM-DD")
    rep.add_argument("--final", action="store_true")
    rep.set_defaults(func=cmd_report)

    run = sub.add_parser("run", help="run the bot")
    run.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    configure(Settings.from_env().log_level)
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
