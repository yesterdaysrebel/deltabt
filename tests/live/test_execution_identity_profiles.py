"""The CLI writes an experiment; the bot must be able to reproduce it.

They compute the execution surface from different places -- the bot off its own
broker, the CLI by reconstruction -- and that split has now diverged three
times. Twice inside the paper path (app/execution/paper_broker.broker_params
records both), and once because the LIVE broker has a different surface
altogether: it carries neither max_entry_deviation nor min_fill_rr, the bot
read them as None through a getattr default, and tnet could never bind:

    execution_hash: 4c2fb0bcda0bb6ea -> f251c6e7a9ceea7a

So this asserts the two sides agree for BOTH profiles, against REAL broker
instances, rather than trusting that they do.
"""
from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.execution.paper_broker import PaperBroker
from app.forwardtest.identity import (EXECUTION_FIELDS,
                                      execution_params, execution_profile)
from live.broker import LIVE_EXECUTION_FIELDS, LiveBroker

SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD")


def _bot_side(broker, settings):
    """Exactly what TradingBot._execution_values does, including no default."""
    fields = type(broker).EXECUTION_IDENTITY_FIELDS
    return ({f: (settings.risk.slippage_bps if f == "slippage_bps"
                 else getattr(broker, f)) for f in fields}, fields)


def _paper_broker(settings):
    from app.execution.paper_broker import broker_params
    return PaperBroker({}, starting_equity=settings.risk.starting_equity,
                       slippage_bps=settings.risk.slippage_bps,
                       **broker_params(settings.risk))


def _live_broker():
    return LiveBroker(client=object(), product_ids={}, experiment_id="x",
                      tick_size={})


class TestTheTwoSidesAgree:
    def test_paper(self, monkeypatch):
        monkeypatch.delenv("DELTABOT_EXECUTION_PROFILE", raising=False)
        s = Settings()
        cli_values, cli_fields = execution_profile(s.risk)
        bot_values, bot_fields = _bot_side(_paper_broker(s), s)
        assert cli_fields == bot_fields == EXECUTION_FIELDS
        assert cli_values == bot_values
        assert (execution_params(cli_values, SYMBOLS, fields=cli_fields)
                == execution_params(bot_values, SYMBOLS, fields=bot_fields))

    def test_live(self, monkeypatch):
        monkeypatch.setenv("DELTABOT_EXECUTION_PROFILE", "live")
        s = Settings()
        cli_values, cli_fields = execution_profile(s.risk)
        bot_values, bot_fields = _bot_side(_live_broker(), s)
        assert cli_fields == bot_fields == LIVE_EXECUTION_FIELDS
        assert cli_values == bot_values, (
            "the CLI would write an execution_hash the live bot cannot "
            "reproduce, and the bot would refuse its own experiment")
        assert (execution_params(cli_values, SYMBOLS, fields=cli_fields)
                == execution_params(bot_values, SYMBOLS, fields=bot_fields))


class TestTheSurfacesAreGenuinelyDifferent:
    def test_live_does_not_claim_gates_it_does_not_have(self):
        """The reason this is two profiles and not one shared tuple."""
        live = _live_broker()
        for absent in ("max_entry_deviation", "min_fill_rr"):
            assert not hasattr(live, absent), (
                f"LiveBroker now has {absent}; if it ENFORCES it the field "
                f"belongs in LIVE_EXECUTION_FIELDS, and if it does not then "
                f"recording it would describe a gate that never fires")
            assert absent not in LIVE_EXECUTION_FIELDS

    def test_the_two_profiles_hash_differently(self):
        s = Settings()
        paper_v, paper_f = execution_profile(s.risk, "paper")
        live_v, live_f = execution_profile(s.risk, "live")
        assert (execution_params(paper_v, SYMBOLS, fields=paper_f)
                != execution_params(live_v, SYMBOLS, fields=live_f))

    def test_an_unknown_profile_is_refused(self):
        with pytest.raises(ValueError, match="execution profile"):
            execution_profile(Settings().risk, "simulated")


class TestNoSilentNone:
    def test_a_missing_declared_attribute_raises_rather_than_hashing_none(self):
        """The getattr default is what made this survivable and invisible."""
        class Broken(PaperBroker):
            EXECUTION_IDENTITY_FIELDS = ("entry_ttl_seconds", "not_a_field")

        s = Settings()
        with pytest.raises(AttributeError):
            _bot_side(Broken({}, starting_equity=10_000.0), s)


class TestPaperIdentityIsUnmoved:
    def test_the_paper_execution_hash_is_what_it_always_was(self, monkeypatch):
        """atr is mid-experiment; a moved hash makes it refuse to continue."""
        monkeypatch.delenv("DELTABOT_EXECUTION_PROFILE", raising=False)
        s = Settings()
        values, fields = execution_profile(s.risk)
        out = execution_params(values, SYMBOLS, fields=fields)
        assert set(out) == set(EXECUTION_FIELDS) | {"halt_min_run"}
        assert out["entry_ttl_seconds"] == 90
        assert out["max_entry_deviation"] == 0.25
        assert out["min_fill_rr"] == pytest.approx(1.7)
