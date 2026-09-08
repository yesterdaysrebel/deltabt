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
DEPLOY = (ROOT / ".github/workflows/deploy.yml").read_text()
MONITOR = (ROOT / ".github/workflows/monitor.yml").read_text()

#: The arms that were RUNNING when the `cross` stack was added. Adding a
#: family to the catalog must not move either hash: `config_hash` is a digest
#: of the spec, not of the catalog, and if that ever stops being true a new
#: family would silently end both experiments through ConfigurationDrift.
RUNNING_ARMS = {
    "manual_scalp_both_t3":
        "41e764beceaf787f4b54ec25106b4c366e375478f4dbf3660d1a3b20c686f88d",
    "manual_scalp_st_banded_h18_24":
        "89ed49527806c0e3b6394c4c3fff87cb8d02d6ec450bcb77d5b9068991e065f6",
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
        expr = "[.[] | select(.pinned != true)]"
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

def test_the_running_arms_are_pinned():
    by_stack = {r["stack"]: r for r in _table()}
    for stack in ("atr", "hours", "cross"):
        assert by_stack[stack].get("pinned") is True, (
            f"stack '{stack}' is no longer pinned; the next merge to master "
            f"would retire its running experiment and reset its risk ledger")


def test_a_push_rolls_nothing_while_every_arm_is_running():
    """`cross` joined the pinned set on 2026-09-08, once it was RUNNING.

    It was unpinned for exactly as long as it took to roll the stack up. An
    empty selection is the correct steady state while all three arms are
    mid-experiment -- not a misconfiguration -- and the deploy job treats it
    as success rather than as an invalid matrix.
    """
    assert _pick(_table()) == []


def test_the_notice_names_what_was_skipped():
    assert "pinned, NOT rolled" in DEPLOY and "only_stack=" in DEPLOY, (
        "a push now silently skips stacks; it must say which and how to "
        "override, or a stack stops being deployed and nobody notices")


# --- 3. the override still works -------------------------------------------

@pytest.mark.parametrize("stack", ["atr", "hours", "cross"])
def test_only_stack_can_still_roll_any_stack(stack):
    """Pinning is a guard against ACCIDENT, not a lock."""
    assert _pick(_table(), only=stack) == [stack]


def test_pinning_is_overridden_loudly():
    assert "pinning is overridden" in DEPLOY


# --- 4. an all-pinned table must not produce an invalid matrix -------------

def test_the_roll_job_is_gated_on_a_non_empty_matrix():
    """`matrix.include: []` is INVALID, not empty: GitHub rejects the whole
    workflow rather than skipping the job. Reachable as soon as every stack is
    pinned, which is the normal state once each arm is mid-experiment."""
    job = DEPLOY[DEPLOY.index("\n  deploy:"):]
    cond = job[:job.index("runs-on:")]
    assert "needs.targets.outputs.matrix != '[]'" in cond


def test_a_fully_pinned_table_selects_nothing_rather_than_erroring():
    assert _pick([dict(r, pinned=True) for r in _table()]) == []


# --- 5. a pinned stack still gets its daily report -------------------------

def test_every_stack_is_still_reported_on():
    """Pinning stops DEPLOYS, and must not stop monitoring: an arm nobody
    rolls is exactly the one nobody would notice going silent."""
    reported = set(re.findall(r"^\s+- stack: (\w+)$", MONITOR, re.M))
    for row in _table():
        assert row["stack"] in reported, (
            f"stack '{row['stack']}' has no monitor.yml entry, so it runs "
            f"unwatched")
