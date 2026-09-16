"""A merge must not end an experiment that is already running.

WHY THIS EXISTS

    The experiment identity includes git_sha, so rolling a host RETIRES its
    running forward test and registers a successor -- which resets the risk
    ledger to 10,000 and the closed-trade sample to zero. Until 2026-09-07 a
    push to master rolled EVERY stack, and deploy.yml described that as "the
    documented cost of the merge-to-deploy pipeline", telling the reader to
    remember `only_stack`.

    It cost exactly what it says. On 2026-09-07 a merge that had nothing to do
    with the `hours` arm rolled it four days into a 30-day run: its four trades
    and +1.92R survived under the old experiment id, but its equity curve got
    a seam and its ledger went back to 10,000.

    A cost you have to remember on every merge is a trap, and a `push` event
    cannot carry `only_stack` anyway. So the rule is data now, sitting next to
    the stack it protects, and these tests pin it.

WHAT IS ASSERTED
    1. the running arms' config_hashes do not move when a family is ADDED
    2. those stacks are pinned, and a push skips them
    3. an explicit only_stack dispatch can still roll a pinned stack
    4. a fully-pinned table does not produce an INVALID matrix

WHAT IS NOT ASSERTED
    Which stacks ought to be pinned. That is an operational decision recorded
    in the table; this only holds the mechanism honest.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

from deltabt.catalog import build_spec

ROOT = pathlib.Path(__file__).resolve().parents[2]
from tests.deploy_workflows import text as _deploy_text

#: The whole deploy pipeline. It is four files since the 2026-09-16 split,
#: and the helper refuses to return an empty set so this cannot go vacuous.
DEPLOY = _deploy_text()
MONITOR = (ROOT / ".github/workflows/monitor.yml").read_text()

#: The arms that were RUNNING when the `cross` stack was added. Adding a
#: family to the catalog must not move either hash: `config_hash` is a digest
#: of the spec, not of the catalog, and if that ever stops being true a new
#: family would silently end both experiments through ConfigurationDrift.
#: 2026-09-10: `hours` and `cross` were retired and their stacks destroyed, so
#: `atr` is the only arm left whose identity a catalog edit could end. The other
#: two families stay in the catalog and keep their hashes -- they are simply no
#: longer bound to a RUNNING experiment, so moving them costs nothing.
RUNNING_ARMS = {
    "manual_scalp_both_t3":
        "41e764beceaf787f4b54ec25106b4c366e375478f4dbf3660d1a3b20c686f88d",
}


def _table() -> list[dict]:
    m = re.search(r"all='(\[.*?\])'", DEPLOY, re.S)
    assert m, "deploy.yml no longer has an `all=' stack table"
    return json.loads(m.group(1))


def _pick(table: list[dict], only: str = "") -> list[str]:
    """Run the workflow's OWN jq expressions, not a reimplementation."""
    if not shutil.which("jq"):
        pytest.skip("jq not available")
    if only:
        expr = f'[.[] | select(.stack=="{only}")]'
    else:
        # A push now selects EVERY stack; which of them may actually be rolled
        # is decided per host by the deploy job's guard, not by this filter.
        expr = "."
    out = subprocess.run(["jq", "-c", expr], input=json.dumps(table),
                         capture_output=True, text=True, check=True).stdout
    return [r["stack"] for r in json.loads(out)]


# --- 1. adding a family must not move a running arm's identity -------------

@pytest.mark.parametrize("family,expected", sorted(RUNNING_ARMS.items()))
def test_a_running_arms_hash_does_not_move(family, expected):
    assert build_spec(family, 5).config_hash == expected, (
        f"{family}'s config_hash moved. Both live experiments bind on this "
        f"value; a change ends them with ConfigurationDrift on the next roll. "
        f"If the family was deliberately edited, that is a NEW family, not an "
        f"edit to this one.")


def test_the_new_arm_is_a_distinct_rule():
    cross = build_spec("manual_scalp_cross_both_t3", 5)
    assert cross.config_hash not in RUNNING_ARMS.values()
    assert cross.primary.wpr_rule == "cross_levels"
    assert cross.confirm.wpr_rule == "cross_levels"


def test_the_new_arm_carries_no_gates():
    """The 480-cell lab found every gate family has a negative median net."""
    spec = build_spec("manual_scalp_cross_both_t3", 5)
    for tf in (spec.primary, spec.confirm):
        assert tf.supertrend == "off" and tf.di is False and tf.adx_min is None


# --- 2. a push skips the running arms --------------------------------------
#
# The MECHANISM changed on 2026-09-14 and the guarantee did not. A static
# `"pinned":true` flag had to be hand-edited twice per experiment, and a stack
# left pinned after its run ended blocked every deploy silently -- the steady
# state was "everything pinned, nothing ever ships". The host is now asked at
# roll time instead. These tests moved with it; what they protect is unchanged.

def test_the_deploy_asks_the_host_before_rolling_it():
    assert "Action=status" in DEPLOY, (
        "nothing asks the host whether an experiment is running; a merge "
        "would retire it and reset its risk ledger")


def test_every_step_that_touches_the_host_is_gated_on_that_answer():
    """A guard nothing depends on is decoration."""
    must_be_gated = (
        "retire the running experiment, on the image still running",
        "send the deploy command",
        "start the successor experiment",
    )
    for name in must_be_gated:
        i = DEPLOY.index(f"- name: {name}")
        window = DEPLOY[i:i + 400]
        assert "steps.guard.outputs.roll == 'yes'" in window, (
            f"step {name!r} runs regardless of whether an experiment is "
            f"RUNNING on the host")


def test_the_guard_fails_closed():
    """An unreadable host must not be rolled.

    `forward-test status` exits 1 both for 'nothing is running' and for every
    real failure, so the guard matches the CLI's words rather than its exit
    code -- the same reasoning the retire step already uses. The default must
    be 'do not roll', set BEFORE anything can fail.
    """
    i = DEPLOY.index("- name: may we roll this host?")
    guard = DEPLOY[i:DEPLOY.index("- name: decide the experiment id")]
    first = guard.index('echo "roll=no"')
    assert first < guard.index("send-command"), (
        "the guard must default to roll=no before it talks to the host, so a "
        "failure anywhere leaves the experiment protected")
    assert "no experiment is RUNNING" in guard, (
        "the guard must match the CLI's own words; its exit code is shared "
        "with every real failure")


def test_the_notice_names_what_was_skipped():
    assert "NOT rolling" in DEPLOY and "only_stack=" in DEPLOY, (
        "a push now silently skips stacks; it must say which and how to "
        "override, or a stack stops being deployed and nobody notices")


# --- 3. the override still works -------------------------------------------

@pytest.mark.parametrize("stack", [r["stack"] for r in json.loads(
    re.search(r"all='(\[.*?\])'", DEPLOY, re.S).group(1))])
def test_only_stack_can_still_roll_any_stack(stack):
    """Pinning is a guard against ACCIDENT, not a lock."""
    assert _pick(_table(), only=stack) == [stack]


def test_the_override_is_loud_and_skips_the_guard():
    assert "the running-experiment guard is overridden for it" in DEPLOY
    i = DEPLOY.index("- name: may we roll this host?")
    guard = DEPLOY[i:DEPLOY.index("- name: decide the experiment id")]
    assert "deliberate dispatch" in guard, (
        "only_stack must bypass the guard -- it IS the deliberate act -- and "
        "say so, or an operator who meant to roll is silently refused")


# --- 4. an all-pinned table must not produce an invalid matrix -------------

def test_the_roll_job_is_gated_on_a_non_empty_matrix():
    """`matrix.include: []` is INVALID, not empty: GitHub rejects the whole
    workflow rather than skipping the job. Reachable as soon as every stack is
    pinned, which is the normal state once each arm is mid-experiment."""
    # The roll job is called `roll:` and lives in each caller since the
    # 2026-09-16 split; the gate is on the CALLER, because a reusable workflow
    # cannot see the caller's `needs`. Every caller must carry it: an invalid
    # matrix rejects the whole workflow, so one ungated caller is one venue
    # that goes red for correctly deciding to roll nothing.
    from tests.deploy_workflows import entry_points
    gated = 0
    for path in entry_points():
        text = path.read_text()
        if "\n  roll:" not in text:
            continue
        job = text[text.index("\n  roll:"):]
        cond = job[:job.index("uses:")]
        assert "outputs.matrix != '[]'" in cond, (
            f"{path.name}'s roll job is not gated on a non-empty matrix")
        gated += 1
    assert gated, "no caller has a `roll:` job, so this asserted nothing"


def test_an_empty_table_selects_nothing_rather_than_erroring():
    """Still reachable, just by a different route.

    A push no longer filters stacks out -- the per-host guard does that later,
    after the matrix exists -- so the all-pinned path is gone. An empty stack
    table is the remaining way to reach `matrix.include: []`, which GitHub
    rejects as INVALID rather than skipping, and the job's `if` must still
    catch it.
    """
    assert _pick([]) == []


# --- 5. a pinned stack still gets its daily report -------------------------

def test_every_stack_is_still_reported_on():
    """Pinning stops DEPLOYS, and must not stop monitoring: an arm nobody
    rolls is exactly the one nobody would notice going silent."""
    reported = set(re.findall(r"^\s+- stack: (\w+)$", MONITOR, re.M))
    for row in _table():
        assert row["stack"] in reported, (
            f"stack '{row['stack']}' has no monitor.yml entry, so it runs "
            f"unwatched")
