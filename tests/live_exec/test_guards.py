"""Prod may not start with its circuit breakers off."""

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


def test_the_configuration_running_today_would_be_refused_on_prod():
    """THE test. variables.tf ships 1.0 / 1.0 / 0 -- every breaker off. That
    is correct for an unbiased paper measurement and is what the settings
    module calls "indefensible for real capital"."""
    bad = circuit_breaker_failures(DEPLOYED_TODAY, "prod")
    assert len(bad) == 3
    assert any("max_drawdown_pct" in b for b in bad)
    assert any("max_daily_loss_pct" in b for b in bad)
    assert any("max_consecutive_losses" in b for b in bad)


def test_a_configured_set_passes():
    assert circuit_breaker_failures(Risk(), "prod") == []
    require_circuit_breakers(Risk(), "prod")      # does not raise


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
    bad = circuit_breaker_failures(risk, "prod")
    assert len(bad) == 1 and field in bad[0]


def test_a_value_beyond_the_sentinel_is_also_off():
    """1.5 is not 'stricter than 1.0'; it is further into never-halting."""
    assert circuit_breaker_failures(Risk(max_drawdown_pct=1.5), "prod")


def test_a_missing_limit_is_refused_rather_than_defaulted():
    class Empty:
        pass
    bad = circuit_breaker_failures(Empty(), "prod")
    assert len(bad) == 3 and all("not configured" in b for b in bad)


def test_the_refusal_says_it_does_not_choose_the_numbers():
    """The code has no standing to decide whether the halt belongs at 5% or
    15% -- only to insist somebody decided."""
    with pytest.raises(GuardError) as exc:
        require_circuit_breakers(DEPLOYED_TODAY, "prod")
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
    assert "kill switch" in reason_to_refuse(Risk(), "prod",
                                             kill_switch=str(p))


def test_nothing_to_refuse_when_configured_and_no_switch(tmp_path):
    assert reason_to_refuse(Risk(), "prod",
                            kill_switch=str(tmp_path / "absent")) is None


def test_breakers_are_reported_when_no_switch_is_set(tmp_path):
    msg = reason_to_refuse(DEPLOYED_TODAY, "prod",
                           kill_switch=str(tmp_path / "absent"))
    assert msg and "circuit breakers disabled" in msg


# -- the values that reach a prod host pass their own gate --------------------
#
# REWRITTEN 2026-10-02. These read a PROD_RISK dict in live/config.py that
# nothing deployed ever used; they now read the variables.tf defaults that are
# rendered into /opt/deltabt/env on every host.

def _tf_default(name):
    import pathlib
    import re
    tf = (pathlib.Path(__file__).resolve().parents[2]
          / "infra/terraform/variables.tf").read_text()
    block = tf[tf.index(f'variable "{name}"'):]
    block = block[:block.index("\n}\n")]
    return float(re.search(r"^\s*default\s*=\s*([0-9.]+)", block, re.M).group(1))


def test_the_deployed_breakers_would_let_prod_start():
    from live.guards import DISABLED, circuit_breaker_failures

    class _Risk:
        pass
    risk = _Risk()
    for name in DISABLED:
        setattr(risk, name, _tf_default(name))
    assert circuit_breaker_failures(risk, "prod") == []


def test_the_deployed_breakers_are_the_pre_registered_pilot_values():
    """Pinned so a change is a decision, not an accident: 10% day, 8 in a row,
    and the drawdown latch at 50% FOR THE DRY RUN (docs/prod_dry_run_prereg.md,
    amendment 2026-10-03: no money is at risk, and a 20% latch would have
    ended the record early; the report states when 20% would have fired).
    REAL MONEY RETURNS THIS TO 0.20 before any trading key exists -- the
    real-money pre-registration must change this pin when it does."""
    assert _tf_default("max_drawdown_pct") == 0.50
    assert _tf_default("max_daily_loss_pct") == 0.10
    assert _tf_default("max_consecutive_losses") == 8


def test_the_drawdown_latch_is_still_a_latch():
    """Lifted for the dry run, not removed: live/guards.py refuses only the
    1.0 off switch (test_the_deployed_breakers_would_let_prod_start covers
    that it passes); this pins that nobody quietly turns 0.50 into 1.0."""
    assert 0 < _tf_default("max_drawdown_pct") < 1.0


def test_the_pilot_gates_are_not_silently_inherited_by_a_paper_stack():
    """The gates are GLOBAL. Paper arms have run ungated on purpose -- a halt
    censors the sample -- so a paper stack added while these are set would
    measure a censored arm. Adding one must revisit them."""
    import pathlib
    import re
    tf = (pathlib.Path(__file__).resolve().parents[2]
          / "infra/terraform/variables.tf").read_text()
    start = tf.index('variable "stacks"')
    block = tf[start:tf.index("\nvariable ", start + 10)]
    entries = re.findall(r"^\s{4}(\w+)\s*=\s*\{", block, re.M)
    assert not entries, (
        f"paper stacks {entries} would inherit the prod pilot's breakers")


# --- the exemption is testnet, and ONLY testnet -----------------------------

@pytest.mark.parametrize("venue", [
    "mainnet",          # the name before 2026-09-16: must not be silently exempt
    "production",
    "live",
    "",
    None,
    "Prod ",            # case and whitespace are normalised, not exploited
    "testnet-ish",
])
def test_every_venue_other_than_testnet_requires_the_breakers(venue):
    """The guard used to read `if venue != "mainnet": return []`.

    That exempted everything that was not that exact string -- testnet, but
    also a typo, an empty value and any new name. It was safe only because
    nothing else could set the venue. Renaming the venue to "prod" is precisely
    the change that breaks it: miss the one comparison and a prod host skips
    its circuit breakers while every other file agrees it spends real money.

    `"prod"` passing on its own proves nothing -- the fail-open version passes
    that too. What proves the guard fails CLOSED is that no other value, the
    old name included, gets the exemption.
    """
    bad = circuit_breaker_failures(DEPLOYED_TODAY, venue)
    assert len(bad) == 3, (
        f"venue {venue!r} was exempted from the circuit breakers; only an "
        f"explicit testnet may be")
    with pytest.raises(GuardError):
        require_circuit_breakers(DEPLOYED_TODAY, venue)


@pytest.mark.parametrize("venue", ["testnet", "TESTNET", " testnet "])
def test_only_testnet_is_exempt_however_it_is_written(venue):
    assert circuit_breaker_failures(DEPLOYED_TODAY, venue) == []
