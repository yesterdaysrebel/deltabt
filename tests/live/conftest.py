"""Fixtures for the live-bot test suite."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

#: Postgres-backed tests run only when a DSN is provided. Everything
#: safety-critical also has an in-memory counterpart that always runs, so a
#: machine without a database still gets the constraint semantics checked.
TEST_DSN = os.environ.get("DELTABOT_TEST_DSN")

requires_pg = pytest.mark.skipif(
    not TEST_DSN, reason="set DELTABOT_TEST_DSN to run PostgreSQL tests"
)


@pytest_asyncio.fixture
async def pg_repo():
    from app.persistence.repository import PostgresRepository
    repo = PostgresRepository(TEST_DSN)
    await repo.connect()
    async with repo._pool.acquire() as con:
        await con.execute(
            "TRUNCATE paper_fills, quarantined_fills, funding_events, "
            "paper_orders, positions, "
            "strategy_signals, risk_events, system_events, market_candles, "
            "heartbeat, bot_instance, forward_test, strategy_state "
            "RESTART IDENTITY CASCADE")
    try:
        yield repo
    finally:
        await repo.close()


@pytest_asyncio.fixture
async def mem_repo():
    from app.persistence.repository import InMemoryRepository
    repo = InMemoryRepository()
    await repo.connect()
    try:
        yield repo
    finally:
        await repo.close()
