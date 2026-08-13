"""F2 -- JSON round-trip through PostgreSQL.

AUDIT FINDING. asyncpg returns jsonb as `str` unless a codec is registered, and
none was. `conditions_passed`, `conditions_failed`, `indicators` and `detail`
-- the whole "why did it do that" payload -- came back as strings, so the
dashboard silently rendered nothing and any analysis would have had to re-parse
by hand.

The existing tests missed it because the in-memory repository returns real
lists and the API tests run against that. The shared PostgreSQL scenarios did
run, but asserted only on plain-text columns. Every assertion in this file that
matters therefore runs against a REAL database.
"""

from __future__ import annotations

import math

import pytest

from app.persistence.jsonb import UnserialisableValue, dumps, loads
from tests.live.conftest import requires_pg
from tests.live.test_persistence import _seed_instance, signal

pytestmark = pytest.mark.asyncio


# =====================================================================
# THE ENCODER (no database needed)
# =====================================================================


class TestEncoder:
    async def test_round_trips_nested_structures(self):
        payload = {"a": [1, 2, {"b": True, "c": None}], "d": {"e": {"f": 1.5}}}
        assert loads(dumps(payload)) == payload

    async def test_preserves_int_and_float_distinctly(self):
        out = loads(dumps({"i": 140, "f": 31.25, "neg": -12.5}))
        assert isinstance(out["i"], int) and out["i"] == 140
        assert isinstance(out["f"], float) and out["f"] == 31.25
        assert out["neg"] == -12.5

    async def test_preserves_booleans_and_null(self):
        out = loads(dumps({"t": True, "f": False, "n": None}))
        assert out == {"t": True, "f": False, "n": None}
        assert out["t"] is True and out["n"] is None

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")],
                             ids=["nan", "inf", "-inf"])
    async def test_non_finite_becomes_null(self, bad):
        """PostgreSQL rejects `NaN` outright -- it is not valid JSON.

        Encoding it as null is what JSON has for "no number here", and a null
        in `indicators` truthfully means the indicator was not finite on that
        bar.
        """
        assert loads(dumps({"adx": bad})) == {"adx": None}

    async def test_non_finite_is_sanitised_recursively(self):
        out = loads(dumps({"p": {"a": float("nan")}, "l": [1.0, float("inf")]}))
        assert out == {"p": {"a": None}, "l": [1.0, None]}

    async def test_finite_neighbours_are_untouched(self):
        out = loads(dumps({"adx": float("nan"), "wpr": -12.5, "n": 140}))
        assert out == {"adx": None, "wpr": -12.5, "n": 140}

    async def test_malformed_input_is_rejected_with_the_type_named(self):
        """A cryptic driver-level TypeError helps nobody."""
        with pytest.raises(UnserialisableValue, match="set"):
            dumps({"bad": {1, 2, 3}})

    async def test_rejection_names_the_offending_value(self):
        class Weird:
            def __repr__(self):
                return "<Weird>"
        with pytest.raises(UnserialisableValue, match="Weird"):
            dumps({"bad": Weird()})


# =====================================================================
# REAL POSTGRESQL -- the only place the original bug was observable
# =====================================================================


@requires_pg
@pytest.mark.postgres
class TestPostgresRoundTrip:
    async def test_json_columns_come_back_as_objects_not_strings(self, pg_repo):
        """The exact audit finding."""
        await _seed_instance(pg_repo)
        s = signal("k1")
        s.conditions_passed = ["primary_supertrend_bullish", "primary_adx_ge_min"]
        s.conditions_failed = ["confirm_1m_agrees_long"]
        s.indicators = {"primary": {"adx": 31.25}, "confirmation": {"adx": 27.0}}
        s.detail = {"risk_per_unit": 500.0, "stop_basis": "leg_low"}
        await pg_repo.record_signal(s)

        row = (await pg_repo.recent_signals(1))[0]
        assert isinstance(row["conditions_passed"], list)
        assert isinstance(row["conditions_failed"], list)
        assert isinstance(row["indicators"], dict)
        assert isinstance(row["detail"], dict)
        assert row["conditions_passed"][0] == "primary_supertrend_bullish"
        assert row["indicators"]["primary"]["adx"] == 31.25

    async def test_a_warm_up_evaluation_can_actually_be_stored(self, pg_repo):
        """This used to THROW and lose the evaluation.

        rules.evaluate() records the indicator snapshot before returning
        SUPPRESSED on the "indicator warm-up produced NaN" branch, so NaN in
        `indicators` is a legitimate live payload. The insert failed inside the
        bar loop, which catches and logs -- so the row simply vanished.
        """
        await _seed_instance(pg_repo)
        s = signal("nan1", outcome="SUPPRESSED")
        s.rejection_reason = "indicator warm-up produced NaN"
        s.indicators = {"primary": {"adx": float("nan"), "wpr": -12.5,
                                    "wpr_prev": float("nan")}}
        assert await pg_repo.record_signal(s) is True

        row = (await pg_repo.recent_signals(1))[0]
        ind = row["indicators"]["primary"]
        assert ind["adx"] is None and ind["wpr_prev"] is None
        assert ind["wpr"] == -12.5, "finite neighbours must survive"

    async def test_the_payload_is_queryable_as_jsonb_not_text(self, pg_repo):
        """If it were stored as a quoted string, none of this would work."""
        await _seed_instance(pg_repo)
        s = signal("q1")
        s.indicators = {"primary": {"adx": 31.25, "direction": -1.0}}
        s.conditions_failed = ["primary_wpr_rising"]
        await pg_repo.record_signal(s)

        async with pg_repo._pool.acquire() as con:
            adx = await con.fetchval(
                "SELECT (indicators->'primary'->>'adx')::float "
                "FROM strategy_signals WHERE idempotency_key='q1'")
            assert adx == 31.25
            n = await con.fetchval(
                "SELECT count(*) FROM strategy_signals "
                "WHERE conditions_failed ? 'primary_wpr_rising'")
            assert n == 1
            typ = await con.fetchval(
                "SELECT jsonb_typeof(indicators) FROM strategy_signals "
                "WHERE idempotency_key='q1'")
            assert typ == "object", f"stored as {typ}, not an object"

    async def test_nested_structures_survive_intact(self, pg_repo):
        await _seed_instance(pg_repo)
        s = signal("n1")
        s.detail = {"risk": {"gates": [{"name": "minimum_rr", "ok": True},
                                       {"name": "max_leverage", "ok": False}]},
                    "counts": {"passed": 2, "failed": 1}}
        await pg_repo.record_signal(s)
        row = (await pg_repo.recent_signals(1))[0]
        assert row["detail"] == s.detail
        assert row["detail"]["risk"]["gates"][1]["ok"] is False

    async def test_numeric_columns_come_back_as_float_not_decimal(self, pg_repo):
        """Mixing Decimal with the engine's floats raises on first arithmetic,
        and plain json.dumps refuses Decimal outright."""
        import json
        await _seed_instance(pg_repo)
        await pg_repo.record_signal(signal("d1"))
        row = (await pg_repo.recent_signals(1))[0]
        for field in ("entry_price", "stop_price", "target_price",
                      "stop_distance_pct", "reward_risk"):
            assert isinstance(row[field], float), f"{field} is {type(row[field])}"
        # both of these used to fail
        assert row["entry_price"] * 2 == 126_000.0
        json.dumps({k: row[k] for k in ("entry_price", "reward_risk")})

    async def test_timestamps_are_deterministic_across_a_round_trip(self, pg_repo):
        from app.clock import EventTime
        await _seed_instance(pg_repo)
        s = signal("t1")
        et = EventTime.at(1_600_000_300)
        s.exchange_ts, s.received_ts = et.exchange_ts, et.received_ts
        await pg_repo.record_signal(s)
        row = (await pg_repo.recent_signals(1))[0]
        assert int(row["exchange_ts"].timestamp()) == 1_600_000_300
        assert row["exchange_ts"].tzinfo is not None, "must be timezone-aware"
        assert row["bar_open"].tzinfo is not None

    async def test_empty_collections_survive(self, pg_repo):
        await _seed_instance(pg_repo)
        s = signal("e1")
        s.conditions_passed, s.conditions_failed = [], []
        s.indicators, s.detail = {}, {}
        await pg_repo.record_signal(s)
        row = (await pg_repo.recent_signals(1))[0]
        assert row["conditions_passed"] == [] and row["indicators"] == {}

    async def test_unicode_and_awkward_strings_survive(self, pg_repo):
        await _seed_instance(pg_repo)
        s = signal("u1", outcome="REJECTED")
        s.rejection_reason = 'reward/risk 1.40 < 2.00 — "too thin" \\ 100%'
        s.detail = {"note": "münchen ₹ 日本語", "quote": 'he said "no"'}
        await pg_repo.record_signal(s)
        row = (await pg_repo.recent_signals(1))[0]
        assert row["detail"]["note"] == "münchen ₹ 日本語"
        assert row["rejection_reason"] == s.rejection_reason

    async def test_quarantine_payload_round_trips(self, pg_repo):
        """The quarantine table's whole value is its payload."""
        from app.persistence.models import QuarantinedFillRecord
        await _seed_instance(pg_repo)
        await pg_repo.quarantine_fill(QuarantinedFillRecord(
            quarantine_uid="q1", instance_uid="inst1", reason="unknown position",
            payload={"position_uid": "ghost", "side": -1, "quantity": 97,
                     "price": 64_487.1},
            symbol="BTCUSD", exchange_ts=1_600_000_000, received_ts=1.0))
        row = (await pg_repo.quarantined_fills())[0]
        assert isinstance(row["payload"], dict)
        assert row["payload"]["side"] == -1 and row["payload"]["quantity"] == 97

    async def test_system_event_payloads_round_trip(self, pg_repo):
        from app.persistence.models import SystemEventRecord
        await _seed_instance(pg_repo)
        await pg_repo.record_system_event(SystemEventRecord(
            event_id="e1", instance_uid="inst1", component="candles",
            event_type="GAP_REPAIRED", payload={"missing": 4, "recovered": 4}))
        row = (await pg_repo.recent_system_events(1))[0]
        assert row["payload"] == {"missing": 4, "recovered": 4}


@requires_pg
@pytest.mark.postgres
async def test_both_backends_return_the_same_python_types(pg_repo, mem_repo):
    """The in-memory twin is only useful if it does not lie about types.

    This is the assertion whose absence let F2 survive: the shared scenarios
    checked values, never the types the two backends hand back.
    """
    s = signal("same1")
    s.indicators = {"primary": {"adx": 31.25}}
    s.detail = {"stop_basis": "leg_low"}
    for repo in (pg_repo, mem_repo):
        await _seed_instance(repo)
        await repo.record_signal(s)

    pg_row = (await pg_repo.recent_signals(1))[0]
    mem_row = (await mem_repo.recent_signals(1))[0]
    for field in ("conditions_passed", "conditions_failed", "indicators", "detail"):
        assert type(pg_row[field]) is type(mem_row[field]), (
            f"{field}: postgres gives {type(pg_row[field]).__name__}, "
            f"in-memory gives {type(mem_row[field]).__name__}")
        assert pg_row[field] == mem_row[field]
