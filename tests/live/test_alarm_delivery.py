"""An alarm that notifies nobody is not monitoring.

THE DEFECT THIS PINS
    cloudwatch.tf gates every alarm's notification on one variable:

        alarm_actions = var.alarm_email != "" ? [aws_sns_topic.alarms[0].arn] : []

    `alarm_email` defaulted to "" and nothing set it, so all 24 alarms
    evaluated correctly, transitioned correctly, and delivered to nobody. Their
    state surfaced in exactly one place: the daily report.

    And the daily report runs on GitHub's `schedule` trigger, which is
    best-effort. Measured over 2026-08-25..27 it fired ONCE, eighty minutes
    after its cron, and skipped 2026-08-27 entirely -- the same queue backlog
    that silenced CI on two pull requests. So the chain was

        alarm -> (nothing) -> daily report -> GitHub scheduler -> inbox

    with one unreliable link, and it was the only link. On 2026-08-26 a
    `bot-silent` alarm on a half-deployed stack sat in ALARM overnight and told
    no one.

WHAT IS ASSERTED
    Not that alarms exist -- they did. That the value which decides whether
    they can speak actually reaches Terraform from CI. This is the third defect
    of this exact shape in two days: a setting correct in one file and never
    delivered to the thing that consumes it. See tests/live/test_env_forwarding.py.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/infrastructure.yml"
CLOUDWATCH = ROOT / "infra/terraform/cloudwatch.tf"
VARIABLES = ROOT / "infra/terraform/variables.tf"


def test_ci_passes_the_alarm_address_to_terraform():
    """Without this the variable keeps its "" default and SNS stays off."""
    text = WORKFLOW.read_text()
    assert "TF_VAR_alarm_email" in text, (
        "infrastructure.yml does not pass TF_VAR_alarm_email, so alarm_email "
        "keeps its empty default and every alarm's alarm_actions is []")
    assert "vars.ALARM_EMAIL" in text, (
        "TF_VAR_alarm_email is set but not from the ALARM_EMAIL repository "
        "variable, so it cannot be changed without editing the workflow")


def test_the_address_is_not_committed():
    """It is a personal address; it belongs in a repo variable, not in git."""
    for path in (VARIABLES, WORKFLOW, CLOUDWATCH):
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", path.read_text()), (
            f"{path.name} contains what looks like an email address. Pass it "
            f"through the ALARM_EMAIL repository variable instead.")


def test_alarm_actions_are_gated_on_the_address_not_hardcoded_empty():
    text = CLOUDWATCH.read_text()
    assert "var.alarm_email" in text
    assert re.search(r"alarm_actions\s*=\s*var\.alarm_email\s*!=", text), (
        "alarm_actions is no longer derived from alarm_email; check that "
        "alarms can still deliver")


def test_recovery_is_notified_too_not_just_failure():
    """ok_actions matters: silence after an alarm is ambiguous otherwise."""
    text = CLOUDWATCH.read_text()
    assert "ok_actions" in text, (
        "no ok_actions, so a recovered alarm sends nothing and the only "
        "signal is the absence of further mail")


@pytest.mark.parametrize("alarm", [
    "silent", "restart_loop", "critical", "errors", "instance_status",
])
def test_the_alarms_that_matter_unattended_exist(alarm):
    """The ones that catch a dead or thrashing bot between daily reports."""
    text = CLOUDWATCH.read_text()
    assert f'"{alarm}"' in text, f"no aws_cloudwatch_metric_alarm.{alarm}"


def test_every_alarm_uses_the_shared_actions_local():
    """A new alarm that forgets alarm_actions is silent and looks fine."""
    text = CLOUDWATCH.read_text()
    blocks = re.findall(
        r'resource "aws_cloudwatch_metric_alarm" "(\w+)"(.*?)\n}', text, re.S)
    assert blocks, "no metric alarms found; has cloudwatch.tf moved?"
    missing = [name for name, body in blocks
               if "local.alarm_actions" not in body]
    assert not missing, (
        f"these alarms do not use local.alarm_actions and will never notify: "
        f"{missing}")
