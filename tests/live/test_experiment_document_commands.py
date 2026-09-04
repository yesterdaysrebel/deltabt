"""The experiment SSM document must call the CLI the way the CLI accepts.

The document's content is a shell script embedded in Terraform. Nothing type
checks it, and a wrong invocation is not discovered until it runs -- which, for
this document, is after the host has already been replaced. On 2026-09-04 the
first real pipeline roll failed with

    forward-test stop: error: the following arguments are required: --reason

leaving the arm down until it was finished by hand. These tests read the
document and the argument parser and check they agree.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EC2 = ROOT / "infra/terraform/ec2.tf"
CLI = ROOT / "app/cli.py"


def document() -> str:
    """The experiment document's runCommand block."""
    tf = EC2.read_text()
    i = tf.index('resource "aws_ssm_document" "experiment"')
    return tf[i:tf.index('resource "aws_ssm_document" "deploy"', i)]


def required_args(subcommand: str) -> set[str]:
    """Flags app/cli.py marks required=True for one forward-test subcommand."""
    src = CLI.read_text()
    m = re.search(rf'fts\.add_parser\("{subcommand}"', src)
    assert m, f"no parser for forward-test {subcommand}"
    # The parser's own add_argument calls, up to the next add_parser.
    nxt = src.find("fts.add_parser(", m.end())
    block = src[m.start():nxt if nxt != -1 else len(src)]
    return {re.search(r'"(--[a-z-]+)"', line).group(1)
            for line in block.splitlines()
            if "add_argument(" in line and "required=True" in line}


@pytest.mark.parametrize("subcommand", ["stop", "start"])
def test_the_document_passes_every_required_flag(subcommand):
    doc = document()
    call = re.search(rf"cli forward-test {subcommand}\b.*", doc)
    assert call, f"the document never calls forward-test {subcommand}"
    for flag in required_args(subcommand):
        assert flag in call.group(0), (
            f"the experiment document runs `forward-test {subcommand}` without "
            f"{flag}, which app/cli.py marks required. It will fail at run "
            f"time, after the host has been replaced.")


def test_stop_is_known_to_require_a_reason():
    """Guards the guard: if --reason stops being required, this test is telling
    us nothing and should be revisited rather than silently passing."""
    assert "--reason" in required_args("stop")


def test_start_names_an_experiment_explicitly():
    """Letting the id default produced a mislabelled experiment once already."""
    doc = document()
    assert "--experiment-id" in doc


def test_the_document_uses_the_bots_own_environment():
    """config_hash and risk_hash are computed from these; defaults would
    register an experiment describing a rule nobody is running."""
    doc = document()
    for var in ("DELTABOT_SYMBOLS", "DELTABOT_VARIANT", "DELTABOT_MAX_HOLD"):
        assert var in doc, f"{var} is not passed to the one-off container"


# --- a stack's FIRST roll, which has nothing to retire ----------------------
#
# Added 2026-09-04 with the second stack. The deploy retires before it rolls,
# so on a host Terraform has just created the stop branch runs before anything
# has ever started there. Three preconditions fail on such a host and each one
# aborts the deploy under `set -e`, leaving the new arm down: run.sh has not
# written /run/deltabt/env, the image tag is still "none", and the CLI exits 1
# because no experiment is RUNNING.

def test_stop_tolerates_a_host_that_has_never_run_a_container():
    doc = document()
    assert "/run/deltabt/env" in doc and "nothing to retire" in doc, (
        "the stop branch no longer guards a host where run.sh has never run; "
        "a new stack's first deploy will fail before it rolls")


def test_stop_tolerates_an_undeployed_image_tag():
    doc = document()
    assert '"$TAG" = "none"' in doc, (
        "the stop branch no longer guards the pre-deploy image tag; "
        "`docker run repo:none` fails and aborts the first roll")


def test_stop_tolerates_there_being_no_running_experiment():
    """And it matches on the CLI's words, so this pins them together."""
    doc = document()
    message = "no experiment is RUNNING"
    assert message in CLI.read_text(), (
        f"app/cli.py no longer prints {message!r} when nothing is running. "
        f"The experiment document matches on that exact string to tell "
        f"'nothing to retire' apart from a real failure, and it now cannot.")
    assert message in doc, (
        "the stop branch no longer tolerates 'no experiment is RUNNING', so "
        "the first roll of a new stack fails on an empty database")


def test_stop_still_fails_on_a_real_error():
    """The tolerance must be narrow, or a broken retire ships silently."""
    doc = document()
    assert "retire FAILED" in doc and 'exit "$rc"' in doc, (
        "the stop branch swallows every non-zero exit; a genuine failure to "
        "retire would then be followed by a roll that cannot bind")
