"""The workflows derive SSM document names. Terraform must still agree.

WHY THIS EXISTS

    Adding a stack used to require repository VARIABLES that only a human
    could set, and one of them -- the instance id -- does not exist until
    Terraform has applied. So a new arm was live but unreported until someone
    remembered, and the daily report failed every night in the meantime on a
    stack that was perfectly healthy.

    Both workflows now derive what they can: `deltabt-paper-<stack>-deploy`
    and `-monitor`, with the repository variable kept as an override for the
    stacks that already have one. That derivation is a SECOND COPY of
    Terraform's naming scheme, and a second copy is a thing that drifts. If it
    does, the failure is an IAM AccessDenied on a document nobody can find --
    which is exactly how the experiment document failed on 2026-09-04, and it
    cost an outage to diagnose.

    So: assert the scheme, and assert the override still matches it where one
    exists.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EC2 = (ROOT / "infra/terraform/ec2.tf").read_text()
MONITORING = (ROOT / "infra/terraform/monitoring.tf").read_text()
NETWORK = (ROOT / "infra/terraform/network.tf").read_text()
DEPLOY = (ROOT / ".github/workflows/deploy.yml").read_text()
MONITOR = (ROOT / ".github/workflows/monitor.yml").read_text()


def test_the_resource_prefix_is_what_the_workflows_hardcode():
    """`deltabt-paper` in the fallbacks is local.name with the default env."""
    assert re.search(r'name\s*=\s*"deltabt-\$\{var\.environment\}"', NETWORK), (
        "local.name is no longer deltabt-${environment}; every derived "
        "document name in the workflows is now wrong")
    assert re.search(r'variable "environment"[\s\S]*?default\s*=\s*"paper"',
                     (ROOT / "infra/terraform/variables.tf").read_text()), (
        "the environment default moved off 'paper'")


def test_a_non_legacy_stack_gets_its_name_as_a_suffix():
    """`suffix` is what turns local.name into the per-stack document name."""
    assert re.search(r'suffix\s*=\s*k == local\.legacy_stack \? "" : "-\$\{k\}"',
                     EC2), "the suffix scheme changed; the fallbacks are wrong"
    assert 'legacy_stack = "v1"' in EC2, (
        "the legacy stack moved. A stack named here takes UNSUFFIXED document "
        "names, and the workflows' derivation would be wrong for it.")


@pytest.mark.parametrize("kind,source,pattern", [
    ("deploy", EC2, r'name\s*=\s*"\$\{local\.name\}\$\{each\.value\.suffix\}-deploy"'),
    ("monitor", MONITORING, r'name\s*=\s*"\$\{local\.name\}\$\{each\.value\.suffix\}-monitor"'),
])
def test_terraform_names_the_document_the_way_the_workflow_derives_it(kind, source, pattern):
    assert re.search(pattern, source), (
        f"the {kind} document is no longer named "
        f"local.name + suffix + '-{kind}'")


@pytest.mark.parametrize("workflow,kind", [(DEPLOY, "deploy"), (MONITOR, "monitor")])
def test_the_workflow_falls_back_to_the_derived_name(workflow, kind):
    assert f"format('deltabt-paper-{{0}}-{kind}', matrix.stack)" in workflow, (
        f"the {kind} workflow no longer derives a document name, so adding a "
        f"stack needs a repository variable a human must set")


def test_no_workflow_reads_an_instance_id_from_a_repository_variable():
    """The value that could not be known before the apply.

    deploy.yml moved off it after five stale values; monitor.yml followed on
    2026-09-04. `instance_var` survives in both matrices as a label, which is
    why this asserts on the USE and not on the key.
    """
    for name, text in (("deploy.yml", DEPLOY), ("monitor.yml", MONITOR)):
        # Comments discuss the old mechanism at length, deliberately. Only
        # what the workflow RUNS counts.
        live = "\n".join(l for l in text.splitlines()
                         if not l.lstrip().startswith("#"))
        assert "vars[matrix.instance_var]" not in live, (
            f"{name} reads the instance id from a repository variable again. "
            f"It goes stale on every host replacement, and a new stack cannot "
            f"set it before Terraform has applied.")
        assert "steps.host.outputs.instance_id" in text, (
            f"{name} no longer discovers its host by tag")


def test_every_registered_stack_is_discoverable_by_the_name_tag():
    """Discovery is by `Name`, and Terraform must still set it that way."""
    assert re.search(r'tags\s*=\s*\{\s*Name\s*=\s*"\$\{local\.name\}-\$\{each\.key\}"',
                     EC2), "the instance Name tag scheme changed"
    for name, text in (("deploy.yml", DEPLOY), ("monitor.yml", MONITOR)):
        assert "NAME_TAG: deltabt-paper-${{ matrix.stack }}" in text, (
            f"{name} no longer builds the Name tag from the stack key")
