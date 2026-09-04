"""The infrastructure workflow's database step, and the two ways it failed.

WHY THIS EXISTS

    Terraform cannot create a database inside its own RDS instance: the server
    sits in a private subnet with no route from a CI runner. So the
    infrastructure workflow sends `scripts/create_stack_database.sh` to each
    bot host over SSM, and each host ensures its own DB_NAME.

    That step failed twice on 2026-09-04, on the first roll of two new stacks,
    and the two failures compounded into an outage where a GREEN infrastructure
    run left two hosts with nowhere to connect:

    1. A RACE. The step waits for the host to become an SSM managed node, then
       sends the command. But the SSM agent registers while cloud-init is still
       running, and `/opt/deltabt/env` -- written by user_data, and the only
       place DB_NAME lives -- did not exist yet. Both hosts reported Online at
       20:22:06, the command ran at 20:22:08, and the file appeared at
       20:22:11. It failed with "No such file or directory".

    2. A SKIPPED JOB. The step lives in the `apply` job, which was gated on the
       plan having changes. The re-run planned NO CHANGES, skipped the whole
       job, and reported SUCCESS -- so the databases were never created and the
       deploy had a green infrastructure run to roll onto. An empty plan is the
       normal case for a re-run, which is exactly when the check matters most.

    These tests pin both fixes. They read the workflow as text because that is
    the only thing available to a test; the failure they guard against is a
    silent edit, not a syntax error.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
WF = (ROOT / ".github/workflows/infrastructure.yml").read_text()


def _job(name: str) -> str:
    """The text of one job block."""
    start = WF.index(f"\n  {name}:\n")
    rest = WF[start + 1:]
    nxt = re.search(r"\n  [a-z][a-z0-9-]*:\n", rest)
    return rest[: nxt.start()] if nxt else rest


def _step(job: str, name_fragment: str) -> str:
    """The text of one step within a job."""
    i = job.index(name_fragment)
    start = job.rindex("      - name:", 0, i)
    nxt = job.find("\n      - name:", i)
    return job[start: nxt if nxt != -1 else len(job)]


def test_the_apply_job_runs_even_when_the_plan_is_empty():
    """Failure 2: the database step must not be skipped with the job."""
    job = _job("apply")
    header = job[: job.index("    steps:")]
    assert "if: github.ref == 'refs/heads/master'" in header
    assert "needs.plan.outputs.changes" not in header, (
        "the apply JOB is gated on the plan having changes again. The "
        "database step lives in it, so an empty plan -- the normal case for a "
        "re-run -- would report success while ensuring no database.")


def test_the_tofu_apply_step_is_what_carries_the_changes_gate():
    """The gate has to live somewhere, or an empty plan is applied as stale."""
    job = _job("apply")
    step = _step(job, "apply the reviewed plan")
    assert "if: needs.plan.outputs.changes == '2'" in step, (
        "tofu apply is no longer gated on there being changes; applying a "
        "saved plan against an unchanged world fails with 'Saved plan is "
        "stale' on every re-run")


def test_the_database_step_is_not_gated_on_changes():
    job = _job("apply")
    step = _step(job, "create each stack's database")
    assert "needs.plan.outputs.changes" not in step, (
        "the database step is gated on the plan having changes; a re-run "
        "after a partial failure plans nothing and would skip it")


def test_the_sent_script_waits_for_user_data_to_finish():
    """Failure 1: SSM Online does not mean cloud-init has written the env."""
    job = _job("apply")
    step = _step(job, "create each stack's database")
    assert "[ -f /opt/deltabt/env ]" in step, (
        "the command sent to the host no longer waits for /opt/deltabt/env. "
        "The SSM agent registers before user_data finishes; the gap was three "
        "seconds on 2026-09-04 and it failed the whole run.")
    assert "user_data never wrote /opt/deltabt/env" in step, (
        "the wait no longer fails loudly when the file never appears; a host "
        "whose bootstrap did not complete is a real fault")


def test_the_wait_is_bounded_and_the_poll_outlasts_it():
    """A wait longer than the workflow's own poll would look like a hang."""
    job = _job("apply")
    step = _step(job, "create each stack's database")
    waits = [int(n) for n in re.findall(r"seq 1 (\d+)", step)]
    assert waits, "no bounded loops left in the database step"
    # The sent script's wait and the runner's poll are both in the step; the
    # poll must be the longer of the two.
    assert max(waits) > min(waits), (
        f"the runner's poll no longer outlasts the in-host wait ({waits}); "
        f"the command would still be waiting when the workflow gives up")


def test_the_step_still_reads_db_name_from_the_host():
    """The whole point: no per-stack table in the workflow to drift."""
    job = _job("apply")
    step = _step(job, "create each stack's database")
    # The name is escaped inside a python heredoc inside YAML, so it reaches
    # the file as \"$DB_NAME\". Match the variable, not the quoting.
    assert "$DB_NAME" in step and ". /opt/deltabt/env" in step, (
        "the database name is no longer taken from the host's own "
        "environment, so adding a stack now needs an edit here too")


@pytest.mark.parametrize("marker", [
    "Name=tag:Name,Values=deltabt-paper-*",
    "PingStatus",
])
def test_every_running_host_is_still_covered(marker):
    """Discovery by tag is what makes a new stack need no registration."""
    step = _step(_job("apply"), "create each stack's database")
    assert marker in step


def test_everything_that_consumes_the_plan_is_gated_with_it():
    """The job runs unconditionally so the database step can. Every step that
    needs the saved plan must therefore carry the same condition.

    The plan job uploads `tfplan` only when it found changes, so an ungated
    download fails with "Artifact not found" on every no-op master push. That
    is what broke the merge of #47 -- the first master push after the job was
    made unconditional.
    """
    job = _job("apply")
    gate = "if: needs.plan.outputs.changes == '2'"
    for fragment in ("actions/download-artifact@v4",
                     "apply the reviewed plan",
                     "AWS preflight"):
        i = job.index(fragment)
        start = job.rindex("      - ", 0, i)
        nxt = job.find("\n      - ", i)
        block = job[start: nxt if nxt != -1 else len(job)]
        assert gate in block, (
            f"the step at {fragment!r} consumes the saved plan but is not "
            f"gated on there being one")


def test_the_database_step_is_the_reason_the_job_is_unconditional():
    """Guards the guard: if nothing in the job is ungated, the change that
    made the job unconditional has been quietly undone."""
    job = _job("apply")
    step = _step(job, "create each stack's database")
    assert "if: needs.plan.outputs.changes" not in step
