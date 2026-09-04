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
