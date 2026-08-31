"""A failed rollback must stop the host, not leave it crash-looping.

WHAT HAPPENED. On 2026-08-31 the ATR stack moved to SPEC:manual_scalp@5. The
roll failed, deploy.sh rolled back to the previous tag -- an image built before
that catalog family existed -- and the container died on

    ValueError: DELTABOT_VARIANT='SPEC:manual_scalp@5' names no catalog family

every twenty seconds until the service was stopped by hand.

ROLLBACK ASSUMES THE PREVIOUS IMAGE CAN RUN THE CURRENT ENVIRONMENT, and that
stops being true the moment user_data changes. start_and_verify uses
`systemctl restart`, the unit is Restart=always, so a failed rollback loops
rather than stopping.

A stopped host is honest: /readyz is unreachable, the daily report says so, the
alarms fire. A crash-looping host looks alive to systemd and hides which image
is broken.
"""
from __future__ import annotations

import pathlib
import re

DEPLOY = pathlib.Path(__file__).resolve().parents[2] / "deploy/aws/deploy.sh"


def _rollback_failure_branch() -> str:
    """The `else` arm of `if start_and_verify "$PREVIOUS"`."""
    text = DEPLOY.read_text()
    start = text.index('if start_and_verify "$PREVIOUS"')
    return text[text.index("else", start):text.index("\nfi", start)]


def test_the_failed_rollback_branch_stops_the_service():
    assert re.search(r"systemctl\s+stop\s+deltabt", _rollback_failure_branch()), (
        "a rollback that also fails must stop the service; Restart=always "
        "turns 'log and exit' into an unattended crash loop")


def test_it_says_why_rather_than_only_that():
    """The operator needs the variant/catalog hint, because the previous image
    being unable to run the current env is not an obvious failure mode."""
    branch = _rollback_failure_branch()
    assert "DELTABOT_VARIANT" in branch


def test_the_no_previous_tag_branch_already_stopped_and_still_does():
    """The two failure paths must agree; this one was always correct."""
    text = DEPLOY.read_text()
    branch = text[text.index('if [[ -z "$PREVIOUS"'):]
    branch = branch[:branch.index("\nfi")]
    assert re.search(r"systemctl\s+stop\s+deltabt", branch)


def test_deploy_still_exits_nonzero_after_a_failed_rollback():
    """Stopping the host must not turn a failed deploy into a green run."""
    text = DEPLOY.read_text()
    tail = text[text.index('if start_and_verify "$PREVIOUS"'):]
    assert tail.rstrip().endswith("exit 1")
