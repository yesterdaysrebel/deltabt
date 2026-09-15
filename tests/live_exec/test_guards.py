"""Mainnet may not start with its circuit breakers off."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from live.guards import (GuardError, circuit_breaker_failures,
                         kill_switch_engaged, reason_to_refuse,
                         require_circuit_breakers)


@dataclass
class Risk:
    """The three limits that matter, at the values the repo ships."""
    max_drawdown_pct: float = 0.10
    max_daily_loss_pct: float = 0.02
    max_consecutive_losses: int = 3


#: What is ACTUALLY deployed today, from infra/terraform/variables.tf.
DEPLOYED_TODAY = Risk(max_drawdown_pct=1.0, max_daily_loss_pct=1.0,
                      max_consecutive_losses=0)


def test_the_configuration_running_today_would_be_refused_on_mainnet():
    """THE test. variables.tf ships 1.0 / 1.0 / 0 -- every breaker off. That
    is correct for an unbiased paper measurement and is what the settings
    module calls "indefensible for real capital"."""
    bad = circuit_breaker_failures(DEPLOYED_TODAY, "mainnet")
    assert len(bad) == 3
    assert any("max_drawdown_pct" in b for b in bad)
    assert any("max_daily_loss_pct" in b for b in bad)
    assert any("max_consecutive_losses" in b for b in bad)


def test_a_configured_set_passes():
    assert circuit_breaker_failures(Risk(), "mainnet") == []
    require_circuit_breakers(Risk(), "mainnet")      # does not raise


def test_testnet_is_exempt():
    """A rehearsal on instruments the arm does not trade; halting it early
    only cuts the rehearsal short."""
    assert circuit_breaker_failures(DEPLOYED_TODAY, "testnet") == []


@pytest.mark.parametrize("field,value", [
    ("max_drawdown_pct", 1.0),
    ("max_daily_loss_pct", 1.0),
    ("max_consecutive_losses", 0),
])
def test_each_breaker_is_checked_on_its_own(field, value):
    risk = Risk(**{field: value})
    bad = circuit_breaker_failures(risk, "mainnet")
    assert len(bad) == 1 and field in bad[0]


def test_a_value_beyond_the_sentinel_is_also_off():
    """1.5 is not 'stricter than 1.0'; it is further into never-halting."""
    assert circuit_breaker_failures(Risk(max_drawdown_pct=1.5), "mainnet")


def test_a_missing_limit_is_refused_rather_than_defaulted():
    class Empty:
        pass
    bad = circuit_breaker_failures(Empty(), "mainnet")
    assert len(bad) == 3 and all("not configured" in b for b in bad)


def test_the_refusal_says_it_does_not_choose_the_numbers():
    """The code has no standing to decide whether the halt belongs at 5% or
    15% -- only to insist somebody decided."""
    with pytest.raises(GuardError) as exc:
        require_circuit_breakers(DEPLOYED_TODAY, "mainnet")
    assert "does not choose the numbers" in str(exc.value)
    assert "censors the sample" in str(exc.value)


# -- the kill switch ---------------------------------------------------------

def test_the_kill_switch_is_a_file(tmp_path):
    p = tmp_path / "HALT"
    assert kill_switch_engaged(str(p)) is False
    p.write_text("stop")
    assert kill_switch_engaged(str(p)) is True


def test_an_unreadable_kill_switch_counts_as_engaged(tmp_path, monkeypatch):
    """Whatever is wrong with the filesystem is a better argument for
    stopping than against it."""
    import pathlib
    def boom(self):
        raise OSError("filesystem is gone")
    monkeypatch.setattr(pathlib.Path, "exists", boom)
    assert kill_switch_engaged(str(tmp_path / "HALT")) is True


def test_the_kill_switch_beats_a_healthy_configuration(tmp_path):
    p = tmp_path / "HALT"
    p.write_text("")
    assert "kill switch" in reason_to_refuse(Risk(), "mainnet",
                                             kill_switch=str(p))


def test_nothing_to_refuse_when_configured_and_no_switch(tmp_path):
    assert reason_to_refuse(Risk(), "mainnet",
                            kill_switch=str(tmp_path / "absent")) is None


def test_breakers_are_reported_when_no_switch_is_set(tmp_path):
    msg = reason_to_refuse(DEPLOYED_TODAY, "mainnet",
                           kill_switch=str(tmp_path / "absent"))
    assert msg and "circuit breakers disabled" in msg


# -- the operator's chosen values pass their own gate ------------------------

def test_the_chosen_mainnet_values_would_be_accepted():
    """The point of choosing them is that mainnet can then start."""
    from live.config import MAINNET_RISK
    from live.guards import circuit_breaker_failures

    class _Risk:
        pass
    risk = _Risk()
    for k, v in MAINNET_RISK.items():
        setattr(risk, k, v)
    assert circuit_breaker_failures(risk, "mainnet") == []


def test_the_chosen_values_are_not_the_disabled_sentinels():
    from live.config import MAINNET_RISK
    from live.guards import DISABLED
    for name, off in DISABLED.items():
        assert MAINNET_RISK[name] != off, f"{name} is still the off switch"


def test_they_are_not_in_the_paper_stacks_terraform():
    """Editing risk values in variables.tf feeds risk_hash AND user_data, which
    replaces the paper bot's host and then fails to bind its experiment on
    drift. The live bot's numbers must not live there."""
    import pathlib
    tf = (pathlib.Path(__file__).resolve().parents[2]
          / "infra/terraform/variables.tf").read_text()
    for line in tf.splitlines():
        stripped = line.strip()
        if stripped.startswith("default") and "0.03" in stripped:
            raise AssertionError(
                "the live daily-loss value has been written into the paper "
                "stack's terraform; that replaces the paper host on apply")
