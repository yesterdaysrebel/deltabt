"""The deploy workflow must name the experiment document as Terraform names it.

On 2026-09-04 the workflow built the name by APPENDING `-experiment` to the
deploy document's name, producing `deltabt-paper-atr-deploy-experiment`.
Terraform names them `<stack>-deploy` and `<stack>-experiment` -- siblings, not
parent and child -- so the call failed as an IAM AccessDenied on a document
that does not exist, which reads like a permissions problem and is not one.

The workflow cannot query Terraform (its role sees one ECR repository and a
couple of SSM documents), so the two names are derived independently and can
drift. This test is the only thing that ties them together.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: The whole deploy pipeline, four files since the 2026-09-16 split. The
#: derivation lives in _roll.yml now; reading only a caller would find nothing.
from tests.deploy_workflows import text as _deploy_text
EC2 = ROOT / "infra/terraform/ec2.tf"


def test_terraform_names_the_two_documents_as_siblings():
    """If this changes, the derivation below has to change with it."""
    tf = EC2.read_text()
    assert '"${local.name}${each.value.suffix}-deploy"' in tf
    assert '"${local.name}${each.value.suffix}-experiment"' in tf


def test_the_workflow_swaps_the_suffix_rather_than_appending():
    wf = _deploy_text()
    assert "${DEPLOY_DOC%-deploy}-experiment" in wf, (
        "the workflow must derive the experiment document by replacing the "
        "'-deploy' suffix, not by appending to it")
    assert not re.search(r"document_var \]?\}\}-experiment", wf), (
        "appending '-experiment' to the deploy document name produces "
        "'<stack>-deploy-experiment', which does not exist")


def test_the_derivation_produces_the_name_terraform_creates():
    """Exercise the shell expansion itself, not a description of it."""
    import subprocess
    out = subprocess.run(
        ["bash", "-c", 'DEPLOY_DOC=deltabt-paper-atr-deploy; '
                       'echo "${DEPLOY_DOC%-deploy}-experiment"'],
        capture_output=True, text=True, check=True).stdout.strip()
    assert out == "deltabt-paper-atr-experiment", out


def test_every_experiment_step_uses_the_derivation():
    """Retire and start are separate steps; one was fixed and one was not.

    The guard step added on 2026-09-14 is a third caller, so this counts uses
    against callers rather than pinning a literal number: the fault being
    prevented is ONE of them naming the document a different way, which is how
    2026-09-04's AccessDenied on `...-deploy-experiment` happened.
    """
    wf = _deploy_text()
    derived = wf.count("${DEPLOY_DOC%-deploy}-experiment")
    callers = wf.count('--parameters "Action=')
    assert derived == callers, (
        f"{callers} steps invoke the experiment document but only {derived} "
        f"derive its name; every one of them must derive it the same way")
    assert derived >= 3, "expected retire, start and the roll guard"
