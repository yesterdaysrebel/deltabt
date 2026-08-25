"""Fail-closed preflight for the 30-day forward test.

AUDIT FINDING F6. There was no gate at all: ``python -m app`` started the bot
unconditionally. A 30-day experiment that begins with a stale schema, an
unknown commit, or a symbol whose warm-up never completed produces a dataset
nobody can defend, and the damage is only visible weeks later.

Every check returns a verdict rather than raising, so a single run reports
EVERYTHING that is wrong instead of stopping at the first problem. An operator
fixing four things wants to see four things.

FAIL CLOSED: if any required check fails, ``ok`` is False and the caller must
not enter the trading loop. Checks marked advisory report WARN and do not block
-- there are few of them, and each says why it is advisory.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

from app.clock import MarketClock
from app.config.settings import Settings
from app.config.strategy import StrategyConfig
from app.forwardtest.identity import UNKNOWN_SHA, build_identity, git_sha


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


@dataclass
class Check:
    name: str
    verdict: Verdict
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.verdict is Verdict.FAIL


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.blocking for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.blocking]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if c.verdict is Verdict.WARN]

    def render(self) -> str:
        width = max((len(c.name) for c in self.checks), default=10)
        lines = [f"[{c.verdict.value:4s}] {c.name.ljust(width)}"
                 + (f"  {c.detail}" if c.detail else "")
                 for c in self.checks]
        lines.append("")
        if self.ok:
            lines.append(f"PREFLIGHT PASSED  ({len(self.checks)} checks, "
                         f"{len(self.warnings)} warning(s))")
        else:
            lines.append(f"PREFLIGHT FAILED  ({len(self.failures)} blocking "
                         f"of {len(self.checks)} checks)")
            lines.append("")
            lines.append("The forward test must NOT be started. Blocking:")
            lines.extend(f"  - {c.name}: {c.detail}" for c in self.failures)
        return "\n".join(lines)


#: Tables the bot writes to. A missing one means the schema was never migrated
#: or is older than the code.
REQUIRED_TABLES = (
    "forward_test", "bot_instance", "heartbeat", "market_candles",
    "strategy_signals", "paper_orders", "paper_fills", "quarantined_fills",
    "positions", "funding_events", "risk_events", "system_events",
    "strategy_state",
)


async def run_preflight(settings: Settings, strategy: StrategyConfig, *,
                        repo=None, costs: dict | None = None,
                        clock: MarketClock | None = None,
                        backfiller=None, dsn: str | None = None,
                        experiment_id: str = "preflight",
                        check_feed: bool = True) -> PreflightReport:
    """Run every gate. Never raises; a crashed check is itself a FAIL."""
    r = PreflightReport()

    def add(name, ok, detail="", warn_only=False):
        r.checks.append(Check(
            name,
            Verdict.PASS if ok else (Verdict.WARN if warn_only else Verdict.FAIL),
            detail))

    async def guarded(name, coro, warn_only=False):
        try:
            ok, detail = await coro
            add(name, ok, detail, warn_only)
        except Exception as exc:                        # noqa: BLE001
            add(name, False, f"check raised {type(exc).__name__}: {exc}")

    # ---- configuration ------------------------------------------------
    # VALIDATION AND DESCRIPTION ARE SEPARATE, BECAUSE THEY FAILED TOGETHER.
    # Both lived in one `try`, so an AttributeError while FORMATTING the detail
    # string was reported as the configuration being invalid. On v5's first
    # preflight that read:
    #
    #   [FAIL] strategy config valid
    #          'StrategySpec' object has no attribute 'primary_timeframe'
    #
    # The spec was valid. validate() had already returned. A blocking verdict
    # that names a real attribute is about as convincing as a wrong answer
    # gets, and it would have been debugged as a strategy problem.
    try:
        strategy.validate()
    except Exception as exc:                            # noqa: BLE001
        add("strategy config valid", False, str(exc))
    else:
        add("strategy config valid", True, _describe(strategy))

    try:
        settings.risk.validate()
        add("risk config valid", True,
            f"risk {100*settings.risk.risk_per_trade:.2f}% "
            f"RR>={settings.risk.minimum_rr} "
            f"max_open={settings.risk.max_open_positions}")
    except Exception as exc:                            # noqa: BLE001
        add("risk config valid", False, str(exc))

    exec_params = {"entry_ttl_seconds": 90, "max_entry_deviation": 0.25,
                   "min_fill_rr": 1.7,
                   "slippage_bps": settings.risk.slippage_bps}
    ident = build_identity(experiment_id, strategy, settings.risk,
                           exec_params, settings.symbols)
    add("config hash computed", bool(ident.config_hash),
        f"config={ident.config_hash} strategy={ident.strategy_hash} "
        f"risk={ident.risk_hash}")

    sha, dirty = git_sha()
    add("git SHA recorded", sha != UNKNOWN_SHA,
        f"{sha[:12]}" + (" (WORKING TREE DIRTY)" if dirty else "")
        if sha != UNKNOWN_SHA else
        "no commit SHA available; a result that cannot be tied to code is not "
        "reproducible")
    if sha != UNKNOWN_SHA and dirty:
        add("working tree clean", False,
            "uncommitted changes: the recorded SHA does not describe the code "
            "that would run")

    # ---- universe -----------------------------------------------------
    # NOT "exactly four". The count was pinned to the original universe, so
    # widening it to six blocked the start with the symbol list echoed back as
    # the failure -- a check reporting the intended configuration as the fault.
    #
    # What actually has to hold is that the universe is non-empty and that no
    # symbol appears twice: a duplicate would double that symbol's weight in
    # the results while every per-symbol guard still saw one position, and
    # nothing downstream would report it. The specific membership is already
    # recorded in the experiment identity, which is what makes a change to it
    # visible.
    dupes = sorted({s for s in settings.symbols
                    if list(settings.symbols).count(s) > 1})
    add("universe configured", bool(settings.symbols) and not dupes,
        ", ".join(settings.symbols) + (f" -- DUPLICATED: {dupes}" if dupes else "")
        or "no symbols configured")
    if costs is not None:
        missing = [s for s in settings.symbols if s not in costs]
        add("contract specs available", not missing,
            "all present" if not missing else f"missing {missing}")
        bad = [s for s in settings.symbols
               if s in costs and costs[s].funding_interval_seconds <= 0]
        add("funding intervals known", not bad,
            "; ".join(f"{s}={costs[s].funding_interval_seconds}s"
                      for s in settings.symbols if s in costs)
            or "none")
    else:
        add("contract specs available", False, "no cost specifications supplied")

    # ---- clock --------------------------------------------------------
    if clock is not None:
        add("market clock initialised", clock.is_set,
            f"market time {clock.now()}" if clock.is_set else
            "no market data observed yet, so exchange time is unknown")
    else:
        add("market clock initialised", False, "no clock supplied")

    # ---- persistence --------------------------------------------------
    if repo is None:
        add("database reachable", False, "no repository supplied")
    else:
        async def _writable():
            ok = await repo.is_writable()
            return ok, "write probe succeeded" if ok else "write probe FAILED"
        await guarded("database writable", _writable())

        async def _schema():
            missing = await _missing_tables(repo)
            if missing is None:
                return True, "in-memory repository; schema check not applicable"
            return not missing, ("all required tables present"
                                 if not missing else f"missing {missing}")
        await guarded("schema current", _schema())

        async def _persist():
            probe = {"at": time.time()}
            await repo.set_state("_preflight_probe", probe)
            back = await repo.get_state("_preflight_probe")
            return back == probe, ("round trip verified" if back == probe
                                   else f"wrote {probe}, read {back}")
        await guarded("event persistence working", _persist())

        async def _experiment():
            active = await repo.active_experiment()
            if active is None:
                return True, "no experiment running; ready to start one"
            return True, (f"{active['experiment_id']} is RUNNING "
                          f"(config {active['config_hash']})")
        await guarded("experiment slot", _experiment())

    # ---- single instance ----------------------------------------------
    if dsn:
        await guarded("advisory lock available", _lock_free(dsn))
    else:
        add("advisory lock available", False,
            "no DSN supplied; single-instance safety cannot be verified")

    # ---- market data ---------------------------------------------------
    if check_feed and backfiller is not None:
        await guarded("market data reachable", _feed_reachable(
            backfiller, settings.symbols[0]))
    elif check_feed:
        add("market data reachable", False, "no backfiller supplied")

    # ---- the safety boundary -------------------------------------------
    add_safety(r)
    return r


def _describe(strategy) -> str:
    """Name the arm and its timeframes, in whichever vocabulary it uses.

    A StrategyConfig states them as strings ("5m"/"1m"); a StrategySpec states
    them as integer minutes and may have no confirmation timeframe at all.
    Asked for an attribute it does not have, either one raises -- so this
    branches on what the object IS rather than on what it is hoped to expose.
    """
    from deltabt.spec import StrategySpec
    if isinstance(strategy, StrategySpec):
        confirm = (f"{strategy.confirm_minutes}m" if strategy.confirm.enabled
                   else "no confirmation")
        return f"{strategy.version} ({strategy.primary_minutes}m/{confirm})"
    return (f"{strategy.version} ({strategy.primary_timeframe}/"
            f"{strategy.confirmation_timeframe})")


async def _missing_tables(repo) -> list[str] | None:
    pool = getattr(repo, "_pool", None)
    if pool is None:
        return None
    async with pool.acquire() as con:
        rows = await con.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'")
    have = {r["table_name"] for r in rows}
    return sorted(t for t in REQUIRED_TABLES if t not in have)


async def _lock_free(dsn: str):
    from app.persistence.lock import SingleInstanceLock
    lock = SingleInstanceLock(dsn)
    got = await lock.acquire()
    if got:
        await lock.release()
        return True, "no other instance holds it"
    return False, "another bot instance holds the advisory lock"


async def _feed_reachable(backfiller, symbol: str):
    now = int(time.time())
    bars = await asyncio.wait_for(
        backfiller.fetch(symbol, now - 3600, now), timeout=30)
    if not bars:
        return False, f"no candles returned for {symbol}"
    age = now - bars[-1].start
    return age < 900, (f"{len(bars)} bars, newest {age}s old"
                       + ("" if age < 900 else " -- stale"))


def add_safety(report: PreflightReport) -> None:
    """Re-assert the paper-only boundary at the gate, not only in CI.

    The build already fails if order-placement code appears, but preflight runs
    against the deployed artifact, which is the thing that will actually trade.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    from app.safety import (
        FORBIDDEN_CREDENTIAL_NAMES,
        FORBIDDEN_IMPORTS,
        FORBIDDEN_ORDER_METHODS,
    )
    banned = FORBIDDEN_ORDER_METHODS
    credentials = FORBIDDEN_CREDENTIAL_NAMES
    hits: list[str] = []
    cred_hits: list[str] = []
    for path in sorted((root / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            hits.append(f"{path.name}: unparseable")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in banned:
                    hits.append(f"{path.name}:{node.lineno} def {node.name}")
            elif isinstance(node, ast.Name) and node.id.lower() in credentials:
                cred_hits.append(f"{path.name}:{node.lineno} {node.id}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = ([a.name for a in node.names]
                        if isinstance(node, ast.Import) else [node.module or ""])
                if any((m or "").split(".")[0] in FORBIDDEN_IMPORTS
                       for m in mods):
                    cred_hits.append(f"{path.name}:{node.lineno} imports hmac")

    report.checks.append(Check(
        "no live order-placement code", Verdict.PASS if not hits else Verdict.FAIL,
        "no order-placement method exists in app/" if not hits
        else "; ".join(hits)))
    report.checks.append(Check(
        "no exchange credentials", Verdict.PASS if not cred_hits else Verdict.FAIL,
        "no credential or signing code in app/" if not cred_hits
        else "; ".join(cred_hits)))
