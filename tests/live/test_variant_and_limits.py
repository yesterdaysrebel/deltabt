"""What this process is configured to BE, before it is anything else.

Two experiments now run concurrently from one image: V1 on one host, V2 on
another. That makes two questions load-bearing that used to have a single
compile-time answer -- which strategy am I, and which risk limits am I under --
and both are answered from the environment. A wrong answer here does not crash
anything; it produces a healthy bot recording the wrong strategy's signals
under the other one's experiment id.

These are pure configuration tests and deliberately live outside
test_experiment_identity.py, which applies `pytestmark = pytest.mark.asyncio`
to every test in the module.
"""

from __future__ import annotations

import os
from dataclasses import replace
from unittest import mock

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.strategy import FROZEN
from app.config.variants import ALL, VARIANT_ENV, resolve_strategy
from app.forwardtest.identity import build_identity


class TestVariantSelection:
    """One image has to be able to be either strategy, and say which.

    Running V1 and V2 concurrently rules out one-strategy-per-image: two images
    from two branches would give up the single git SHA that ties a database row
    to the code that produced it.
    """

    def test_unset_is_v1_which_is_what_frozen_is(self):
        assert resolve_strategy({}).config_hash == FROZEN.config_hash
        assert resolve_strategy({VARIANT_ENV: ""}).config_hash == FROZEN.config_hash

    def test_each_registered_variant_is_reachable_by_name(self):
        for name, cfg in ALL.items():
            got = resolve_strategy({VARIANT_ENV: name})
            assert got.config_hash == cfg.config_hash, name
            assert resolve_strategy({VARIANT_ENV: name.lower()}).config_hash \
                == cfg.config_hash, f"{name} must resolve case-insensitively"

    def test_v1_and_v2_are_actually_different_identities(self):
        """If these ever collide the two runs are indistinguishable in the DB."""
        v1 = resolve_strategy({VARIANT_ENV: "V1"})
        v2 = resolve_strategy({VARIANT_ENV: "V2"})
        assert v1.config_hash != v2.config_hash
        assert v1.confirm_wpr is False and v2.confirm_wpr is True

    def test_an_unknown_variant_refuses_rather_than_defaulting(self):
        """FAILING CLOSED IS THE WHOLE VALUE OF THIS FUNCTION.

        A typo that fell back to V1 would come up healthy, bind to the V2
        experiment, and record V1's signals under V2's identity. The composite
        hash check cannot catch it: the experiment is created by the same
        process from the same wrong config, so the two agree with each other
        and disagree only with the intent.
        """
        for bad in ("V3", "V1_", "VariantA", "NONE", "0", "V2LEVEL"):
            with pytest.raises(ValueError) as e:
                resolve_strategy({VARIANT_ENV: bad})
            assert "not a known variant" in str(e.value)

    def test_surrounding_whitespace_is_tolerated(self):
        """It arrives from an env file, where a trailing space is invisible."""
        assert resolve_strategy({VARIANT_ENV: " V2 "}).config_hash \
            == ALL["V2"].config_hash

    def test_the_error_names_what_is_available(self):
        with pytest.raises(ValueError) as e:
            resolve_strategy({VARIANT_ENV: "V9"})
        for name in ALL:
            assert name in str(e.value)


class TestRelaxedLimitsAreReachableFromTheEnvironment:
    """The forward-test configuration is set by env on the host, so a limit
    with no binding cannot be changed without a code deploy."""

    def test_every_risk_limit_the_run_sets_has_an_override(self):
        env = {"DELTABOT_MAX_OPEN": "6", "DELTABOT_MAX_DRAWDOWN": "1.0",
               "DELTABOT_MAX_CONSEC_LOSSES": "0",
               "DELTABOT_MAX_DAILY_LOSS": "0.02",
               "DELTABOT_MAX_TRADES_PER_DAY": "6"}
        with mock.patch.dict(os.environ, env, clear=False):
            r = Settings.from_env().risk
        assert r.max_open_positions == 6
        assert r.max_drawdown_pct == 1.0
        assert r.max_consecutive_losses == 0
        assert r.max_daily_loss_pct == 0.02
        assert r.max_trades_per_day == 6

    def test_the_defaults_are_untouched_without_the_environment(self):
        """Negative control: the overrides must be opt-in."""
        keys = ["DELTABOT_MAX_OPEN", "DELTABOT_MAX_DRAWDOWN",
                "DELTABOT_MAX_CONSEC_LOSSES"]
        with mock.patch.dict(os.environ, {}, clear=False):
            for k in keys:
                os.environ.pop(k, None)
            r = Settings.from_env().risk
        assert (r.max_open_positions, r.max_drawdown_pct,
                r.max_consecutive_losses) == (1, 0.10, 3)

    def test_relaxing_the_limits_moves_the_risk_hash(self):
        """Otherwise the two configurations would be indistinguishable."""
        strict = RiskConfig()
        loose = replace(strict, max_open_positions=6, max_drawdown_pct=1.0,
                        max_consecutive_losses=0)
        a = build_identity("E", FROZEN, strict, {}, ("BTCUSD",))
        b = build_identity("E", FROZEN, loose, {}, ("BTCUSD",))
        assert a.risk_hash != b.risk_hash
        assert a.config_hash != b.config_hash

    def test_changing_the_symbol_set_moves_the_config_hash(self):
        """Which is why both runs must be new experiments."""
        four = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
        six = ("BTCUSD", "ETHUSD", "SOLUSD", "BEATUSD", "BANKUSD", "AKEUSD")
        a = build_identity("E", FROZEN, RiskConfig(), {}, four)
        b = build_identity("E", FROZEN, RiskConfig(), {}, six)
        assert a.config_hash != b.config_hash


