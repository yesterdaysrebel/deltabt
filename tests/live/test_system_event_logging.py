"""A WARNING+ system event must reach the log stream, not only the database.

FOUND 2026-10-02 while planning the prod pilot: TradingBot._event wrote to
`system_events` and nothing else. POSITION_FLATTENED_UNSAFE, FLATTEN_FAILED and
PROTECTION_UNVERIFIABLE are severity CRITICAL, and the CloudWatch alarm that
exists for them reads `{ $.level = "CRITICAL" }` from the LOG -- so the alarm
could never fire for the events it was written for. The infra tests passed
throughout, because they read the filter's text, not whether anything emits it.

These drive the real _event and read what the JSON formatter would ship.
"""
from __future__ import annotations

import json
import logging

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.strategy import FROZEN
from app.monitoring.logging import JsonFormatter
from app.persistence.repository import InMemoryRepository
from app.runtime.bot import TradingBot
from tests.live.test_recovery import COSTS, DeadFeed


def _bot():
    settings = Settings(symbols=("BTCUSD",),
                        risk=RiskConfig(starting_equity=10_000.0))
    return TradingBot(settings, InMemoryRepository(), COSTS,
                      strategy=FROZEN, feed=DeadFeed())


@pytest.mark.asyncio
@pytest.mark.parametrize("severity, level", [
    ("CRITICAL", "CRITICAL"), ("ERROR", "ERROR"), ("WARNING", "WARNING")])
async def test_a_severe_event_is_logged_at_its_own_level(caplog, severity, level):
    bot = _bot()
    with caplog.at_level(logging.INFO, logger="app.runtime.bot"):
        await bot._event("execution", "POSITION_FLATTENED_UNSAFE",
                         symbol="BEATUSD", severity=severity,
                         payload={"reason": "unprotected"})
    hits = [r for r in caplog.records if r.levelname == level
            and getattr(r, "event_type", None) == "POSITION_FLATTENED_UNSAFE"]
    assert hits, f"a {severity} system event produced no {level} log record"
    shipped = json.loads(JsonFormatter().format(hits[0]))
    # The exact field the metric filter matches, as it reaches CloudWatch.
    assert shipped["level"] == level
    assert shipped["symbol"] == "BEATUSD"
    assert shipped["event_payload"] == {"reason": "unprotected"}


@pytest.mark.asyncio
async def test_an_info_event_stays_in_the_journal_only(caplog):
    bot = _bot()
    with caplog.at_level(logging.DEBUG, logger="app.runtime.bot"):
        await bot._event("broker", "POSITION_OPENED", symbol="BEATUSD")
    assert not [r for r in caplog.records
                if getattr(r, "event_type", None) == "POSITION_OPENED"], (
        "INFO events are the journal; logging them too doubles every row")


@pytest.mark.asyncio
async def test_the_event_is_still_written_to_the_database():
    bot = _bot()
    await bot._event("execution", "POSITION_LIQUIDATED", symbol="BEATUSD",
                     severity="CRITICAL")
    rows = [e for e in await bot.repo.recent_system_events()
            if e["event_type"] == "POSITION_LIQUIDATED"]
    assert rows and rows[0]["severity"] == "CRITICAL"
