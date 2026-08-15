"""F5 -- configuration and code-version lock.

AUDIT FINDING. Nothing recorded a commit SHA and nothing prevented
configuration drift. `strategy_config_hash` covers strategy parameters only, so
DELTABOT_RISK_PER_TRADE and its siblings could change between restarts with
nothing in the data reflecting it -- halving the risk fraction mid-run would
have been invisible. And nothing refused to start on a change, so a 30-day
experiment could quietly become two different experiments.

The requirement is explicit: "Do not modify the expected hash to make tests
pass." Every test here asserts the frozen strategy hash is exactly
d7837e445bc74781.

THAT CONSTANT HAS MOVED TWICE, AND BOTH TIMES ON PURPOSE

    5a5412369f3823f3  original V1
    632efcaff62c4d7c  V2: oscillator on both timeframes, one-shot firing
    d7837e445bc74781  back to V1's rules, 2026-08-14, by explicit instruction

(V1's rules hash differently the second time because StrategyConfig gained
confirm_wpr and fire_once in between, so the hashed blob has two more keys.)

The rule the docstring states is about DRIFT: a hash that moves because a
parameter changed underneath a running experiment, with the test edited
afterwards to hide it. A deliberate variant switch is the opposite -- the
whole point of the hash is that it MUST move, loudly, so that signals
recorded before and after are distinguishable in the audit trail and the bot
refuses to continue an experiment across the change. It did refuse, at
2026-08-14 11:03:56, which is the mechanism working.

The test of whether an update here is legitimate: can you name the commit
that changed the configuration and the reason? For this one, see FROZEN in
app/config/strategy.py and V1/V2 in app/config/variants.py, both of which
carry the measured results that motivated it. If you cannot answer that, the
hash moved by accident and the correct fix is the configuration, not this
constant.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.strategy import FROZEN, Adx, StrategyConfig, WilliamsR
from app.forwardtest.identity import (
    APP_VERSION,
    EXECUTION_FIELDS,
    execution_params,
    UNKNOWN_SHA,
    ConfigurationDrift,
    ExperimentIdentity,
    build_identity,
    git_sha,
)
from tests.live.conftest import requires_pg
from tests.live.test_recovery import make_bot

pytestmark = pytest.mark.asyncio

FROZEN_STRATEGY_HASH = "d7837e445bc74781"
EXEC = {"entry_ttl_seconds": 90, "max_entry_deviation": 0.25,
        "min_fill_rr": 1.7, "slippage_bps": 2.0}
SYMS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")


def ident(experiment_id="E1", strategy=None, risk=None, execution=None,
          symbols=SYMS):
    return build_identity(experiment_id, strategy or FROZEN,
                          risk or RiskConfig(), execution or dict(EXEC), symbols)


# =====================================================================
# THE FROZEN STRATEGY HASH IS NOT TOUCHED
# =====================================================================


class TestFrozenHashPreserved:
    async def test_the_strategy_hash_is_exactly_the_frozen_value(self):
        assert FROZEN.config_hash == FROZEN_STRATEGY_HASH

    async def test_the_composite_does_not_alter_the_strategy_hash(self):
        assert ident().strategy_hash == FROZEN_STRATEGY_HASH

    async def test_a_risk_change_leaves_the_strategy_hash_alone(self):
        """They are separate identities on purpose: one means 'the rules',
        the other means 'this experiment'."""
        other = ident(risk=replace(RiskConfig(), risk_per_trade=0.0025))
        assert other.strategy_hash == FROZEN_STRATEGY_HASH

    async def test_the_frozen_parameters_are_still_the_research_values(self):
        from deltabt.research import hwpr
        assert (FROZEN.adx.di_period, FROZEN.adx.period, FROZEN.adx.minimum,
                FROZEN.williams_r.period, FROZEN.supertrend.atr_period,
                FROZEN.supertrend.multiplier) == (
            hwpr.DI_PERIOD, hwpr.ADX_PERIOD, hwpr.ADX_MIN, hwpr.WPR_PERIOD,
            hwpr.ST_PERIOD, hwpr.ST_MULT)


# =====================================================================
# RISK IS PART OF THE IDENTITY -- the audit finding
# =====================================================================


class TestRiskIsInTheIdentity:
    @pytest.mark.parametrize("field,value", [
        ("risk_per_trade", 0.0025),
        ("minimum_rr", 2.5),
        ("max_open_positions", 2),
        ("max_daily_loss_pct", 0.03),
        ("max_trades_per_day", 10),
        ("max_consecutive_losses", 5),
        ("cooldown_after_trade_seconds", 600),
        ("max_leverage", 5.0),
    ], ids=lambda v: str(v))
    async def test_any_risk_parameter_moves_the_composite_hash(self, field, value):
        """This is exactly what was missing: DELTABOT_RISK_PER_TRADE could
        change with nothing in the data reflecting it."""
        base = ident()
        changed = ident(risk=replace(RiskConfig(), **{field: value}))
        assert changed.config_hash != base.config_hash, f"{field} is invisible"
        assert changed.risk_hash != base.risk_hash

    async def test_an_execution_parameter_moves_it_too(self):
        """Not strategy rules, but they change which fills happen."""
        base = ident()
        changed = ident(execution={**EXEC, "min_fill_rr": 1.5})
        assert changed.config_hash != base.config_hash
        assert changed.execution_hash != base.execution_hash

    async def test_the_symbol_universe_moves_it(self):
        assert ident(symbols=("BTCUSD",)).config_hash != ident().config_hash

    async def test_a_strategy_parameter_moves_it(self):
        changed = ident(strategy=replace(FROZEN, adx=Adx(period=14)))
        assert changed.config_hash != ident().config_hash

    async def test_identical_configuration_gives_an_identical_hash(self):
        assert ident().config_hash == ident().config_hash

    async def test_symbol_order_does_not_matter(self):
        a = ident(symbols=("BTCUSD", "ETHUSD"))
        b = ident(symbols=("ETHUSD", "BTCUSD"))
        assert a.config_hash == b.config_hash


# =====================================================================
# CODE VERSION
# =====================================================================


class TestCodeVersion:
    async def test_a_sha_is_recorded(self):
        sha, _ = git_sha()
        assert sha and len(sha) >= 7

    async def test_an_env_override_is_honoured(self, monkeypatch):
        """A container has no git, so the SHA is baked in at build time."""
        monkeypatch.setenv("DELTABOT_GIT_SHA", "abc123def456")
        monkeypatch.setenv("DELTABOT_GIT_DIRTY", "0")
        assert git_sha() == ("abc123def456", False)

    async def test_a_dirty_tree_is_flagged(self, monkeypatch):
        monkeypatch.setenv("DELTABOT_GIT_SHA", "abc123")
        monkeypatch.setenv("DELTABOT_GIT_DIRTY", "1")
        assert git_sha()[1] is True

    async def test_the_identity_carries_version_and_universe(self):
        i = ident()
        assert i.app_version == APP_VERSION
        assert i.strategy_version.startswith("H-WPR-1-VariantA@")
        assert set(i.symbols) == set(SYMS)

    async def test_the_snapshot_holds_the_full_configuration(self):
        snap = ident().snapshot
        assert snap["strategy"]["adx"]["period"] == 28
        assert snap["strategy"]["williams_r"]["period"] == 140
        assert snap["risk"]["risk_per_trade"] == 0.005
        assert snap["execution"]["min_fill_rr"] == 1.7
        assert snap["symbols"] == sorted(SYMS)


class TestDifferences:
    async def test_drift_is_attributable_to_a_component(self):
        """An operator should be told WHAT moved, not just that something did."""
        a = ident()
        b = ident(risk=replace(RiskConfig(), risk_per_trade=0.0025))
        diffs = b.differences(a)
        assert len(diffs) == 1 and diffs[0].startswith("risk_hash:")

    async def test_no_differences_when_identical(self):
        assert ident().differences(ident()) == []

    async def test_a_symbol_change_is_named(self):
        diffs = ident(symbols=("BTCUSD",)).differences(ident())
        assert any("symbols" in d for d in diffs)


# =====================================================================
# FAIL CLOSED
# =====================================================================


async def _register(bot, **risk_over):
    """Put an experiment in the database matching (or not) the bot."""
    risk = replace(RiskConfig(), **risk_over) if risk_over else bot.settings.risk
    # execution_params(), not a hand-built dict: this helper stands in for the
    # CLI creating the experiment, and building it separately here is the same
    # divergence that made every bot refuse to start in production.
    i = build_identity(
        "H-WPR-1-PAPER-TEST", bot.strategy, risk,
        execution_params(
            {"entry_ttl_seconds": bot.broker.entry_ttl_seconds,
             "max_entry_deviation": bot.broker.max_entry_deviation,
             "min_fill_rr": bot.broker.min_fill_rr,
             "slippage_bps": bot.settings.risk.slippage_bps},
            bot.symbols),
        bot.symbols)
    await bot.repo.connect()
    await bot.repo.create_experiment(i)
    return i


class TestFailClosed:
    async def test_a_matching_configuration_binds(self):
        store: dict = {}
        bot = make_bot(store)
        await _register(bot)
        b = make_bot(store)
        assert await b.start() is True
        assert b.experiment_id == "H-WPR-1-PAPER-TEST"
        assert b.identity.config_hash

    async def test_a_changed_risk_parameter_refuses_to_start(self):
        """The whole point of F5."""
        store: dict = {}
        seed = make_bot(store)
        await _register(seed, risk_per_trade=0.0025)      # recorded

        b = make_bot(store)                               # runs at 0.005
        assert await b.start() is False
        assert "configuration drift" in b.recovery_error
        assert "risk_hash" in b.recovery_error
        assert b.ready is False

    async def test_the_refusal_says_to_start_a_new_experiment(self):
        store: dict = {}
        seed = make_bot(store)
        await _register(seed, max_trades_per_day=99)
        b = make_bot(store)
        await b.start()
        assert "Start a NEW experiment" in b.recovery_error

    async def test_the_refusal_is_recorded_as_critical(self):
        store: dict = {}
        seed = make_bot(store)
        await _register(seed, minimum_rr=3.0)
        b = make_bot(store)
        await b.start()
        events = await b.repo.recent_system_events()
        drift = [e for e in events if e["event_type"] == "CONFIG_DRIFT_REFUSED"]
        assert drift and drift[0]["severity"] == "CRITICAL"

    async def test_no_experiment_means_unbound_but_running(self):
        """Development must stay possible; decisions simply carry no id."""
        bot = make_bot({})
        assert await bot.start() is True
        assert bot.experiment_id is None

    async def test_decisions_are_stamped_with_the_experiment(self):
        store: dict = {}
        seed = make_bot(store)
        await _register(seed)
        bot = make_bot(store)
        await bot.start()

        from app.strategy.explanation import Explanation, Outcome
        from app.runtime.bot import idempotency_key
        exp = Explanation(symbol="BTCUSD", bar_open=1_600_000_000,
                          primary_timeframe="5m", confirmation_timeframe="1m",
                          strategy_version=FROZEN.version,
                          strategy_config_hash=FROZEN.config_hash,
                          outcome=Outcome.NO_SETUP)
        await bot._record_signal(exp, idempotency_key(
            "BTCUSD", 1_600_000_000, None, FROZEN.config_hash))
        row = (await bot.repo.recent_signals())[0]
        assert row["experiment_id"] == "H-WPR-1-PAPER-TEST"
        assert row["config_hash"] == bot.identity.config_hash
        assert row["git_sha"]


class TestBothSidesComputeTheSameExecutionHash:
    """The CLI creates the experiment; the bot verifies itself against it.

    They used to build the execution dict independently -- a literal in
    app/cli.py and the broker's attributes via EXECUTION_FIELDS -- with nothing
    making them agree. Adding the per-symbol halt thresholds to the CLI side
    alone made every bot refuse to start against an experiment it could not
    reproduce:

        configuration drift in experiment H-WPR-1-PAPER-AWS-V1-20260815:
        execution_hash: 1c8ebc1cac2b63bd -> 2371829d9c618ba1

    The guard was correct and the experiment was unusable, which is the worst
    of both. This is the test that would have caught it.
    """

    SYMS = ("BTCUSD", "ETHUSD", "SOLUSD", "BEATUSD", "BANKUSD", "AKEUSD")

    def _cli_side(self, symbols):
        from app.cli import EXEC_PARAMS
        return execution_params({**EXEC_PARAMS, "slippage_bps": 2.0}, symbols)

    def _bot_side(self, symbols):
        """What TradingBot.current_identity builds, with a stub broker."""
        broker = type("B", (), {"entry_ttl_seconds": 90,
                                "max_entry_deviation": 0.25,
                                "min_fill_rr": 1.7})()
        return execution_params(
            {f: getattr(broker, f, None) if f != "slippage_bps" else 2.0
             for f in EXECUTION_FIELDS}, symbols)

    async def test_the_two_sides_agree(self):
        assert self._cli_side(self.SYMS) == self._bot_side(self.SYMS)

    async def test_they_agree_on_the_resulting_hash(self):
        a = build_identity("E", FROZEN, RiskConfig(), self._cli_side(self.SYMS), self.SYMS)
        b = build_identity("E", FROZEN, RiskConfig(), self._bot_side(self.SYMS), self.SYMS)
        assert a.execution_hash == b.execution_hash
        assert a.config_hash == b.config_hash

    async def test_the_halt_thresholds_are_actually_in_there(self):
        """Otherwise the two could agree by both omitting them."""
        d = self._cli_side(self.SYMS)
        assert d["halt_min_run"]["BANKUSD"] == 5
        assert d["halt_min_run"]["BTCUSD"] == 20

    async def test_symbol_order_does_not_move_the_execution_hash(self):
        a = self._cli_side(self.SYMS)
        b = self._cli_side(tuple(reversed(self.SYMS)))
        assert a == b

    async def test_changing_a_threshold_does_move_it(self):
        """The point of recording them: a change must be visible."""
        import app.market_data.market_state as ms
        base = self._cli_side(self.SYMS)
        original = dict(ms.HALT_MIN_RUN_OVERRIDES)
        try:
            ms.HALT_MIN_RUN_OVERRIDES["BANKUSD"] = 7
            assert self._cli_side(self.SYMS) != base
        finally:
            ms.HALT_MIN_RUN_OVERRIDES.clear()
            ms.HALT_MIN_RUN_OVERRIDES.update(original)


class TestSingleActiveExperiment:
    async def test_two_running_experiments_are_refused(self):
        """Two concurrent runs against one database interleave their data."""
        store: dict = {}
        bot = make_bot(store)
        await bot.repo.connect()
        assert await bot.repo.create_experiment(ident("E1")) is True
        assert await bot.repo.create_experiment(ident("E2")) is False

    async def test_a_new_experiment_may_start_after_the_old_one_stops(self):
        store: dict = {}
        bot = make_bot(store)
        await bot.repo.connect()
        await bot.repo.create_experiment(ident("E1"))
        await bot.repo.stop_experiment("E1", "completed 30 days")
        assert await bot.repo.create_experiment(ident("E2")) is True

    async def test_the_same_id_cannot_be_reused(self):
        store: dict = {}
        bot = make_bot(store)
        await bot.repo.connect()
        await bot.repo.create_experiment(ident("E1"))
        await bot.repo.stop_experiment("E1", "done")
        assert await bot.repo.create_experiment(ident("E1")) is False


@requires_pg
@pytest.mark.postgres
class TestExperimentInPostgres:
    async def test_it_round_trips_with_its_snapshot(self, pg_repo):
        i = ident("H-WPR-1-PAPER-20260813")
        assert await pg_repo.create_experiment(i, planned_days=30) is True
        row = await pg_repo.get_experiment("H-WPR-1-PAPER-20260813")
        assert row["config_hash"] == i.config_hash
        assert row["strategy_hash"] == FROZEN_STRATEGY_HASH
        assert row["risk_hash"] == i.risk_hash
        assert row["planned_days"] == 30
        assert list(row["symbols"]) == list(SYMS)
        assert isinstance(row["snapshot"], dict), "JSONB, not a string"
        assert row["snapshot"]["strategy"]["adx"]["period"] == 28

    async def test_the_database_refuses_a_second_running_experiment(self, pg_repo):
        assert await pg_repo.create_experiment(ident("E1")) is True
        assert await pg_repo.create_experiment(ident("E2")) is False
        assert (await pg_repo.active_experiment())["experiment_id"] == "E1"

    async def test_stopping_frees_the_slot(self, pg_repo):
        await pg_repo.create_experiment(ident("E1"))
        await pg_repo.stop_experiment("E1", "completed")
        assert await pg_repo.active_experiment() is None
        assert await pg_repo.create_experiment(ident("E2")) is True
