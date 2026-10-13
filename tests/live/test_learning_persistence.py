"""Signal readings, outcomes and grades: both repositories, same behaviour."""
from __future__ import annotations

import math

import pytest

from app.persistence.models import ModelGradeRecord, SignalOutcomeRecord, SignalRecord
from tests.live.conftest import TEST_DSN, requires_pg

pytestmark = pytest.mark.asyncio


def _sig(key, bar_open, direction=1, entry=100.0, stop=98.0, target=106.0, outcome="APPROVED"):
    return SignalRecord(
        idempotency_key=key, instance_uid="i", symbol="AUSD", bar_open=bar_open,
        primary_timeframe="5m", confirmation_timeframe="1m", direction=direction,
        outcome=outcome, strategy_version="v", strategy_config_hash="h",
        conditions_passed=[], conditions_failed=[], indicators={}, entry_price=entry,
        stop_price=stop, target_price=target, rejection_reason=None if outcome == "APPROVED"
        else "already took 2 trades today, limit is 2")


def _out(sid, key, r=None):
    return SignalOutcomeRecord(
        signal_id=sid, idempotency_key=key, symbol="AUSD", side=1, entry_time=1_790_000_000,
        entry_price=100.0, stop_price=98.0, r_net=r, win=None if r is None else r > 0,
        exit_reason="target" if r is not None else "unlabellable: no data",
        exit_time=None if r is None else 1_790_003_000, exit_price=None if r is None else 106.0,
        mfe_r=None if r is None else 3.1, mae_r=None if r is None else -0.4,
        bars_to_mfe=None if r is None else 9, bars_held=None if r is None else 10,
        fav=[0.1, 3.1] if r is not None else [], adv=[-0.4, 2.0] if r is not None else [],
        detail={"fees": 0.05})


async def _scenario(repo):
    assert await repo.record_signal(_sig("a", 1_790_000_000))
    assert await repo.record_signal(_sig("b", 1_790_000_300, outcome="REJECTED"))
    assert await repo.record_signal(_sig("c", 1_790_000_600, direction=None, entry=None,
                                         stop=None, target=None, outcome="NO_SETUP"))
    await repo.record_signal_features("a", {"lab": {"wpr": -12.5, "adx_1h": float("nan")},
                                            "live": {"spread_bps": 3.0}, "skip": "taken"})
    todo = await repo.signals_to_label()
    assert [r["idempotency_key"] for r in todo] == ["a", "b"], "no direction, no label"
    a = todo[0]
    assert a["bar_open"] == 1_790_000_000 and a["direction"] == 1
    assert a["entry_price"] == 100.0 and isinstance(a["entry_price"], float)
    assert a["features"]["lab"]["wpr"] == -12.5 and a["features"]["lab"]["adx_1h"] is None
    assert todo[1]["features"] is None
    assert await repo.record_signal_outcome(_out(a["id"], "a", 2.95))
    assert not await repo.record_signal_outcome(_out(a["id"], "a", -1.0)), "write-once"
    assert await repo.record_signal_outcome(_out(todo[1]["id"], "b", None))
    assert await repo.signals_to_label() == []
    lab = await repo.labelled_signals()
    assert [(r["idempotency_key"], r["r_net"], r["win"]) for r in lab] == [
        ("a", 2.95, True), ("b", None, None)]
    assert lab[0]["features"]["skip"] == "taken" and lab[1]["exit_reason"].startswith("unlabellable")
    g = ModelGradeRecord(week_start=1_789_948_800, week_end=1_790_553_600, n_train=10,
                         n_train_live=2, n_test=3, auc=0.5, top_mean_r=0.1, all_mean_r=0.0,
                         random_p=0.4, top_k=1, summary="first", detail={"x": 1})
    await repo.record_model_grade(g)
    g.summary, g.auc = "again", 0.6
    await repo.record_model_grade(g)
    latest = await repo.latest_model_grade()
    assert latest["summary"] == "again" and math.isclose(latest["auc"], 0.6)


async def test_in_memory(mem_repo):
    await _scenario(mem_repo)


@requires_pg
async def test_postgres(pg_repo):
    await _scenario(pg_repo)
    async with pg_repo._pool.acquire() as con:
        assert await con.fetchval("SELECT count(*) FROM model_grades") == 1


@requires_pg
async def test_label_and_grade_against_postgres(pg_repo):
    """The labeller and grader end to end on Postgres, on the lab's synthetic market."""
    import pandas as pd

    from live import learning
    from tests import signal_lab_fixture as fx
    from tests.live_exec.test_learning import COSTS, FAR, SYM, FakeDelta, _lab_signals
    sigs = _lab_signals()[:8]
    for bo, side, entry, stop, _ in sigs:
        rec = _sig(f"k{bo}", bo, direction=side, entry=entry, stop=stop,
                   target=entry + side * 3 * abs(entry - stop))
        rec.symbol = SYM
        assert await pg_repo.record_signal(rec)
        await pg_repo.record_signal_features(f"k{bo}", {"lab": {"wpr": -10.0}})
    got = await learning.label_signals(pg_repo, FakeDelta(), lambda s: COSTS, now=FAR)
    assert got["labelled"] == 8, got
    gold = pd.read_parquet(fx.GOLDEN).set_index("entry_time")
    rows = await pg_repo.labelled_signals()
    assert len(rows) == 8
    for r in rows:
        # strategy_signals keeps prices as NUMERIC(20,8); the synthetic
        # market's prices have more digits than a venue's, hence 1e-5
        assert r["r_net"] == pytest.approx(float(gold.loc[r["bar_open"]].R), rel=1e-5)
    again = await learning.label_signals(pg_repo, FakeDelta(), lambda s: COSTS, now=FAR)
    assert again["candidates"] == 0
    g = await learning.grade_model(pg_repo, history_path=None, now=FAR)
    assert g.n_test == 0 and (await pg_repo.latest_model_grade())["summary"] == g.summary


@requires_pg
async def test_a_database_from_before_the_features_column_gets_it():
    """migrate() on a database whose strategy_signals predates `features`."""
    import asyncpg

    from app.persistence.repository import PostgresRepository
    repo = PostgresRepository(TEST_DSN)
    await repo.connect()
    con = await asyncpg.connect(TEST_DSN)
    try:
        await con.execute("ALTER TABLE strategy_signals DROP COLUMN IF EXISTS features")
        await repo.migrate()
        assert await con.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE "
            "table_name = 'strategy_signals' AND column_name = 'features'") == 1
    finally:
        await con.close()
        await repo.close()
