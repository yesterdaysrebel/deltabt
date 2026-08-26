"""Every DELTABOT_* user_data writes must reach the container.

THE DEFECT THIS PINS
    The chain from a Terraform variable to the risk engine has THREE links:

        variables.tf  ->  user_data.sh.tftpl writes /opt/deltabt/env
                      ->  run.sh forwards it into `docker run -e`
                      ->  settings.py reads it

    On 2026-08-26 `max_daily_loss_pct` was added to the first two and not the
    third. `source /opt/deltabt/env` puts a variable in RUN.SH's environment,
    not in the CONTAINER's -- only an explicit `-e` does that. So the value
    reached the host, sat in /opt/deltabt/env looking correct, and the gate ran
    at its code default of 1.0, which means disabled.

    Nothing detected it. The Terraform plan was clean, the apply succeeded, the
    file on the host had the right value, and the daily report has no column
    for "gates that are configured but not in force". It was found by reading
    `docker exec deltabot printenv` by hand.

WHY THE LIST IS HAND-MAINTAINED AT ALL
    run.sh cannot forward everything in /opt/deltabt/env: that file also holds
    DB_SECRET_ARN and the RDS endpoint, and the DSN is deliberately passed by
    --env-file from a root-only tmpfs so it appears in neither `docker inspect`
    nor the process table. An allow-list is right; an UNCHECKED allow-list is
    what broke.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
USER_DATA = ROOT / "infra/terraform/templates/user_data.sh.tftpl"
RUN_SH = ROOT / "deploy/aws/run.sh"

#: Written into /opt/deltabt/env for run.sh's own use, deliberately NOT passed
#: to the container. DELTABOT_GIT_* are set by the Dockerfile at build time.
NOT_FORWARDED = frozenset()


def _written() -> set[str]:
    """DELTABOT_* names user_data writes into /opt/deltabt/env."""
    return set(re.findall(r"^(DELTABOT_[A-Z_]+)=", USER_DATA.read_text(), re.M))


def _forwarded() -> set[str]:
    """DELTABOT_* names run.sh passes with an explicit -e."""
    return set(re.findall(r'-e\s+"?(DELTABOT_[A-Z_]+)=', RUN_SH.read_text()))


def test_every_written_variable_is_forwarded():
    missing = _written() - _forwarded() - NOT_FORWARDED
    assert not missing, (
        f"user_data writes {sorted(missing)} into /opt/deltabt/env but run.sh "
        f"never passes them to `docker run -e`. The value will reach the host "
        f"and never the container, and the setting will silently run at its "
        f"code default.")


def test_nothing_is_forwarded_that_is_never_written():
    """A -e for a name nothing sets is dead, or a typo in one of the two."""
    extra = _forwarded() - _written()
    # API_PORT is set inline in run.sh rather than coming from user_data.
    extra -= {"DELTABOT_API_PORT"}
    assert not extra, (
        f"run.sh forwards {sorted(extra)}, which user_data never writes. "
        f"Either the name is misspelled on one side or the -e is dead.")


def test_the_gate_that_broke_is_covered():
    """Named explicitly so the regression cannot be deleted by accident."""
    assert "DELTABOT_MAX_DAILY_LOSS" in _written()
    assert "DELTABOT_MAX_DAILY_LOSS" in _forwarded()


@pytest.mark.parametrize("name", [
    "DELTABOT_SYMBOLS", "DELTABOT_VARIANT", "DELTABOT_MAX_OPEN",
    "DELTABOT_MAX_DRAWDOWN", "DELTABOT_MAX_DAILY_LOSS",
    "DELTABOT_MAX_CONSEC_LOSSES", "DELTABOT_MAX_HOLD",
])
def test_each_risk_variable_reaches_the_container(name):
    assert name in _written(), f"{name} is not written by user_data"
    assert name in _forwarded(), f"{name} is not forwarded by run.sh"


def test_every_forwarded_risk_variable_is_read_by_settings():
    """A -e nothing reads is as useless as a value nothing forwards."""
    settings = (ROOT / "app/config/settings.py").read_text()
    for name in _forwarded():
        if name in {"DELTABOT_API_PORT", "DELTABOT_LOG_LEVEL",
                    "DELTABOT_SYMBOLS", "DELTABOT_VARIANT"}:
            continue          # read elsewhere: api, logging, variants
        assert name in settings, (
            f"run.sh forwards {name} but app/config/settings.py never reads "
            f"it, so it has no effect on the running bot")
