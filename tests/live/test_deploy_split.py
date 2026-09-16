"""One roll sequence, three venues, three concurrency groups.

WHY THE SPLIT HAPPENED. deploy.yml rolled paper and live in ONE run behind ONE
concurrency group. On 2026-09-16 a live roll hung for forty minutes -- the CLI
it invoked booted a bot instead of answering -- and it sat inside the same run
as the paper rolls, so "the deploy is stuck" could not be answered from the run
list. The paper stacks had in fact rolled and bound perfectly.

WHAT MUST NOT COME BACK. The two roll jobs were 410 and 320 lines and 93% of
their shared steps were byte-identical. Two copies of one fact is the failure
this repository keeps having -- three times on that single day in the identity
path alone -- so the sequence lives in _roll.yml once and the callers are thin.
These tests fail if it is copied back.
"""
from __future__ import annotations

import re

from tests.deploy_workflows import ROLL, entry_points, named

#: The steps that actually touch a host. If one of these appears in a caller,
#: the sequence is being duplicated again.
ROLL_STEPS = (
    "may we roll this host?",
    "retire the running experiment",
    "send the deploy command",
    "start the successor experiment",
)


def test_every_caller_delegates_to_the_shared_roll():
    callers = entry_points()
    assert callers, "no deploy entry points at all"
    for path in callers:
        assert "uses: ./.github/workflows/_roll.yml" in path.read_text(), (
            f"{path.name} does not call the shared roll workflow")


def test_the_roll_sequence_exists_in_exactly_one_place():
    """The assertion the split exists to make."""
    roll = ROLL.read_text()
    for step in ROLL_STEPS:
        assert f"- name: {step}" in roll, f"_roll.yml lost {step!r}"
        for path in entry_points():
            # THE DEFINITION, not a mention. deploy-paper.yml's chooser refers
            # to "may we roll this host?" in prose to explain why it does not
            # filter on experiment state itself, and that cross-reference is
            # worth keeping -- it is the copy of the STEP that must not return.
            assert f"- name: {step}" not in path.read_text(), (
                f"{path.name} defines {step!r} itself. The roll sequence was "
                f"duplicated per venue before and the copies drifted; put it "
                f"in _roll.yml and pass what differs as an input.")


def test_each_venue_has_its_own_concurrency_group():
    """Sharing one group is what made a hung live roll look like stuck paper."""
    groups = {}
    for path in entry_points():
        m = re.search(r"^concurrency:\n(?:\s*#.*\n)*\s+group:\s*(\S+)",
                      path.read_text(), re.M)
        assert m, f"{path.name} declares no concurrency group"
        groups[path.name] = m.group(1)
    assert len(set(groups.values())) == len(groups), (
        f"two deploy workflows share a concurrency group, so one queues behind "
        f"the other for no reason: {groups}")


def test_mainnet_cannot_be_reached_by_a_push():
    """Reaching mainnet must be a deliberate act, not a merge."""
    text = named("deploy-mainnet").read_text()
    assert "\n  push:\n" not in text, (
        "deploy-mainnet.yml has a push trigger, so a merge can spend real "
        "money")
    assert "workflow_dispatch:" in text


def test_the_live_workflows_refuse_the_other_venue():
    """var.live_venue is ONE GLOBAL, so a stack is only 'testnet' because the
    host's parameter says so. Splitting by venue is only meaningful if each
    half refuses the other's hosts, checked against the host and not a table."""
    for stem, venue in (("deploy-testnet", "testnet"),
                        ("deploy-mainnet", "mainnet")):
        text = named(stem).read_text()
        assert re.search(rf"^\s+expect_venue:\s*{venue}\s*$", text, re.M), (
            f"{stem}.yml does not pin expect_venue to {venue}, so it would "
            f"roll a host on the other venue")
    roll = ROLL.read_text()
    assert "delta_env" in roll, (
        "the roll does not read the host's own venue parameter")
    assert "Refusing to roll" in roll, (
        "an unreadable venue parameter does not fail closed")


def test_the_venue_check_runs_before_the_host_is_touched():
    """After the fact it is a summary line; before it, it is a control."""
    roll = ROLL.read_text()
    gate = roll.index("the host must be on the venue this workflow is for")
    for step in ("may we roll this host?", "send the deploy command"):
        assert gate < roll.index(step), (
            f"the venue check runs after {step!r}, so a mainnet host could be "
            f"interrogated or rolled by the testnet workflow first")


def test_paper_does_not_carry_a_venue_gate():
    """A paper stack has no venue parameter; gating it would fail closed on
    every roll. The input defaults to empty and the step is skipped."""
    assert "expect_venue" not in named("deploy-paper").read_text()


def test_a_live_experiment_id_is_no_longer_than_a_paper_one():
    """The budgets are 60 bare and 52 behind `LIVE-`, and those are the values
    the pre-split workflow used -- carried over unchanged so no running
    experiment's id would move.

    They are not equal: 52 + len("LIVE-") is 57, three short of 60. That is
    inherited, the reason is not recorded anywhere, and it is NOT corrected
    here -- slug_max feeds the experiment id, which is what a result is read
    back by, so changing it would rename runs to tidy an asymmetry that costs
    nothing. What must hold is only that prefixing cannot make a live id
    LONGER than a paper one."""
    testnet = named("deploy-testnet").read_text()
    assert "slug_max" not in named("deploy-paper").read_text(), (
        "paper should take the default rather than restate it")
    m = re.search(r"^\s+slug_max:\s*(\d+)", testnet, re.M)
    assert m, "deploy-testnet.yml does not set slug_max"
    default = re.search(r"slug_max:\n(?:.*\n)*?\s+default:\s*(\d+)",
                        ROLL.read_text())
    assert default, "_roll.yml has no slug_max default"
    assert int(m.group(1)) + len("LIVE-") <= int(default.group(1)), (
        f"live slug {m.group(1)} + 'LIVE-' exceeds paper slug "
        f"{default.group(1)}")


# --- the caller/callee contract --------------------------------------------
#
# GitHub validates this only when the workflow RUNS: an unknown key in `with:`
# or a missing required input fails the run, after the image has built. There
# is no actionlint in this environment, so this is the check that would
# otherwise not happen until a deploy was already half done.

def _roll_inputs() -> dict[str, bool]:
    """{name: required} from _roll.yml's workflow_call inputs."""
    text = ROLL.read_text()
    block = text[text.index("  workflow_call:"):text.index("\npermissions:")]
    out = {}
    for m in re.finditer(r"^      (\w+):$", block, re.M):
        name = m.group(1)
        tail = block[m.end():]
        nxt = re.search(r"^      \w+:$", tail, re.M)
        body = tail[:nxt.start()] if nxt else tail
        out[name] = "required: true" in body
    return out


def _caller_with(path) -> set[str]:
    text = path.read_text()
    block = text[text.index("\n    uses: ./.github/workflows/_roll.yml"):]
    return set(re.findall(r"^      (\w+):", block, re.M))


def test_callers_pass_only_inputs_that_exist():
    declared = _roll_inputs()
    assert declared, "_roll.yml declares no inputs; the parse is wrong"
    for path in entry_points():
        for key in _caller_with(path):
            assert key in declared, (
                f"{path.name} passes `{key}` to _roll.yml, which does not "
                f"declare it. GitHub fails the run on an unknown input, after "
                f"the image has already been built and pushed.")


def test_callers_supply_every_required_input():
    required = {k for k, req in _roll_inputs().items() if req}
    assert required, "_roll.yml marks nothing required; the parse is wrong"
    for path in entry_points():
        missing = required - _caller_with(path)
        assert not missing, (
            f"{path.name} omits required input(s) {sorted(missing)} of "
            f"_roll.yml")


# --- the roll must be ALLOWED to read what it reads -------------------------

def test_the_ci_role_can_read_every_ssm_parameter_the_roll_reads():
    """A plan proves what would change, never whether the principal may.

    The venue gate shipped reading /deltabt/paper/<stack>/delta_env while the
    live CI role was granted ssm:GetParameter on the image-tag parameters ONLY.
    The first testnet dispatch failed on it. It failed CLOSED -- an unreadable
    venue is refused rather than assumed -- so nothing was rolled, but the run
    was red for a permission rather than for anything about the deploy.

    Same shape as tests/live/test_deploy_role_covers_stack.py,
    test_env_forwarding.py and test_alarm_delivery.py: a value correct in one
    file and never delivered to the thing that consumes it.
    """
    roll = ROLL.read_text()
    live_tf = (ROLL.parents[2] / "infra/terraform/live.tf").read_text()

    # Parameter suffixes the roll reads, e.g. "delta_env" from
    # `SSM_PARAM: /deltabt/paper/${{ matrix.stack }}/delta_env`.
    suffixes = set(re.findall(
        r"SSM_PARAM:\s*/\S*?/\$\{\{\s*matrix\.stack\s*\}\}/(\w+)", roll))
    assert suffixes, (
        "no SSM parameter reads found in _roll.yml. Either the venue gate is "
        "gone or this parse is wrong; either way it would assert nothing.")

    # Which aws_ssm_parameter resources the live CI role may read.
    policy = live_tf[live_tf.index('resource "aws_iam_role_policy" "live_ci"'):]
    policy = policy[:policy.index("\n# ")] if "\n# " in policy else policy
    granted_resources = set(re.findall(r"aws_ssm_parameter\.(\w+)\[", policy))
    assert granted_resources, "the live CI role is granted no SSM parameters"

    # Each resource's parameter name, to learn which suffix it covers. The
    # definitions are spread across live.tf and ec2.tf -- searching only the
    # file the POLICY lives in would report "granted: []" for a parameter that
    # is in fact granted, and fail a future read for the wrong reason.
    tf_dir = ROLL.parents[2] / "infra/terraform"
    all_tf = "\n".join(f.read_text() for f in sorted(tf_dir.glob("*.tf")))
    granted_suffixes = set()
    for res in granted_resources:
        m = re.search(rf'resource "aws_ssm_parameter" "{res}" \{{'
                      rf'(?:.|\n)*?name\s*=\s*"[^"]*?/(\w+)"', all_tf)
        assert m, f"the policy grants aws_ssm_parameter.{res}, which is not declared"
        granted_suffixes.add(m.group(1))

    missing = suffixes - granted_suffixes
    assert not missing, (
        f"_roll.yml reads SSM parameter(s) ending {sorted(missing)} that the "
        f"live CI role is not granted. The step fails closed, so nothing is "
        f"rolled -- but the run goes red for a permission rather than for "
        f"anything about the deploy. Granted: {sorted(granted_suffixes)}.")
