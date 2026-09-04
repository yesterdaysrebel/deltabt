"""The guard's narrow allowance, which is what lets a merge deploy itself.

`ALLOW_REPLACE_TYPES` exists so the pipeline can replace the bot host without a
human, while the database stays unreachable from any automated path. The
distinction is the entire safety argument for unattended deploys, so it is
asserted here rather than trusted to a workflow's env block.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "tf_guard.py"


def _plan(*changes) -> dict:
    return {"resource_changes": [
        {"address": addr, "type": rtype, "change": {"actions": list(actions)}}
        for addr, rtype, actions in changes]}


def _run(plan: dict, tmp_path: Path, env: dict | None = None):
    p = tmp_path / "tfplan.json"
    p.write_text(json.dumps(plan))
    import os
    e = dict(os.environ)
    e.pop("ALLOW_REPLACE", None)
    e.pop("ALLOW_REPLACE_TYPES", None)
    e.update(env or {})
    return subprocess.run([sys.executable, str(GUARD), str(p)],
                          capture_output=True, text=True, env=e)


REPLACE = ("delete", "create")
DESTROY = ("delete",)
INSTANCE = ('aws_instance.bot["atr"]', "aws_instance", REPLACE)
DB_REPLACE = ("aws_db_instance.paper", "aws_db_instance", REPLACE)
DB_DESTROY = ("aws_db_instance.paper", "aws_db_instance", DESTROY)
INSTANCE_DESTROY = ('aws_instance.bot["atr"]', "aws_instance", DESTROY)

PIPELINE = {"ALLOW_REPLACE_TYPES": "aws_instance,aws_eip"}


def test_replacing_the_host_is_refused_by_default(tmp_path):
    assert _run(_plan(INSTANCE), tmp_path).returncode == 1


def test_the_pipeline_may_replace_the_host(tmp_path):
    r = _run(_plan(INSTANCE), tmp_path, PIPELINE)
    assert r.returncode == 0, r.stdout
    assert "permitted by ALLOW_REPLACE_TYPES" in r.stdout


def test_the_pipeline_may_never_replace_the_database(tmp_path):
    """The line that makes unattended applies defensible."""
    r = _run(_plan(DB_REPLACE), tmp_path, PIPELINE)
    assert r.returncode == 1
    assert "REFUSING THIS PLAN" in r.stdout


def test_a_permitted_type_next_to_a_forbidden_one_still_refuses(tmp_path):
    r = _run(_plan(INSTANCE, DB_REPLACE), tmp_path, PIPELINE)
    assert r.returncode == 1
    assert "aws_db_instance" in r.stdout


def test_a_bare_destroy_of_a_permitted_type_is_still_refused(tmp_path):
    """Replacement puts a host back. Deletion does not, so it is never routine."""
    r = _run(_plan(INSTANCE_DESTROY), tmp_path, PIPELINE)
    assert r.returncode == 1
    assert "DESTROY" in r.stdout


def test_the_blunt_override_still_permits_everything(tmp_path):
    r = _run(_plan(DB_DESTROY), tmp_path, {"ALLOW_REPLACE": "1"})
    assert r.returncode == 0
    assert "ALLOW_REPLACE=1 IS SET" in r.stdout


def test_an_unset_list_changes_nothing(tmp_path):
    assert _run(_plan(INSTANCE), tmp_path, {"ALLOW_REPLACE_TYPES": ""}).returncode == 1


def test_a_clean_plan_passes(tmp_path):
    plan = _plan(('aws_instance.bot["atr"]', "aws_instance", ("update",)))
    assert _run(plan, tmp_path, PIPELINE).returncode == 0


@pytest.mark.parametrize("rtype", ["aws_db_instance", "aws_s3_bucket",
                                   "aws_ecr_repository", "aws_db_subnet_group"])
def test_the_stateful_resources_are_not_in_the_pipeline_list(rtype):
    """Reads the workflow, so widening it there fails here."""
    wf = (Path(__file__).resolve().parents[1]
          / ".github/workflows/infrastructure.yml").read_text()
    for line in wf.splitlines():
        if "ALLOW_REPLACE_TYPES:" in line:
            assert rtype not in line, (
                f"{rtype} appears in the pipeline's ALLOW_REPLACE_TYPES; an "
                f"unattended apply could then destroy it")
