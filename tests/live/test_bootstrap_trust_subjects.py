"""The bootstrap plan must not narrow who is allowed to deploy.

THE DEFECT THIS PINS
    infra/terraform/bootstrap/main.tf trusts two spellings of this repository's
    OIDC subject:

        repo:yesterdaysrebel/deltabt:...                       (name-based)
        repo:yesterdaysrebel@256862558/deltabt@1331985440:...   (immutable)

    The second is built from `github_owner_id` and `github_repo_id`, and BOTH
    DEFAULT TO "", which disables it. scripts/bootstrap.sh never passed them.

    Both roles in the account carry the immutable subjects, so on 2026-08-27 --
    the first plan after the lost bootstrap state was re-imported -- Terraform
    proposed REMOVING them from `deltabt-github-deploy` and
    `deltabt-github-plan` alike:

        BEFORE  repo:...:ref:refs/heads/master, repo:...:environment:paper,
                repo:...@ids:ref:refs/heads/master, repo:...@ids:environment:paper
        AFTER   repo:...:ref:refs/heads/master, repo:...:environment:paper

    That apply would have succeeded and revoked deploy AND pull-request-plan
    access the moment GitHub next sent an immutable subject. main.tf documents
    exactly this failure -- "It also silently breaks every name-based trust
    policy ... AccessDenied: Not authorized to perform
    sts:AssumeRoleWithWebIdentity" -- and the script that runs it dropped the
    inputs that prevent it.

WHY A TEST AND NOT A COMMENT
    The plan was only read because the roles showed as `update`. A future
    operator who runs `bootstrap.sh apply` on a clean state and trusts the
    guard would not see it: tf_guard checks for destroy and replace, and this
    is neither.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/bootstrap.sh"
MAIN = ROOT / "infra/terraform/bootstrap/main.tf"


def _plan_vars() -> set[str]:
    """Terraform variables bootstrap.sh passes to `plan`."""
    text = SCRIPT.read_text()
    start = text.index("plan -input=false")
    block = text[start: text.index("\n\n", start)]
    return set(re.findall(r'-var="([a-z_]+)=', block))


def test_the_script_supplies_both_numeric_ids():
    missing = {"github_owner_id", "github_repo_id"} - _plan_vars()
    assert not missing, (
        f"scripts/bootstrap.sh does not pass {sorted(missing)} to terraform "
        f"plan. They default to \"\", which disables the immutable OIDC "
        f"subject, so the plan will propose REMOVING it from both role trust "
        f"policies and the next workflow run will fail with AccessDenied.")


def test_every_variable_the_trust_policy_needs_is_passed():
    """A new subject input must reach the plan, not just exist in main.tf."""
    declared = set(re.findall(r'variable "(github_[a-z_]+)"', MAIN.read_text()))
    # deploy_branch has a non-empty default that is correct for this repo;
    # the id variables default to "" and "" silently narrows trust.
    unsafe_default = {"github_owner_id", "github_repo_id"}
    for name in declared & unsafe_default:
        assert name in _plan_vars(), f"{name} is declared but never passed"


def test_main_tf_still_trusts_both_forms():
    text = MAIN.read_text()
    assert "repo_immutable" in text and "repo_forms" in text, (
        "main.tf no longer builds both subject spellings; if the immutable "
        "form was dropped, this test and bootstrap.sh should change together")
    assert "compact([" in text, (
        "repo_forms no longer drops a null immutable subject, so an empty id "
        "would produce a malformed subject rather than just the name form")


@pytest.mark.parametrize("subject_kind", ["ref:refs/heads/", "environment:paper",
                                          "pull_request"])
def test_the_three_subject_shapes_survive(subject_kind):
    """deploy branch, deploy environment, and the read-only plan role."""
    assert subject_kind in MAIN.read_text()


def test_no_wildcard_org_in_the_trust_policy():
    """`repo:org*/repo*` would match a repository someone else creates."""
    text = MAIN.read_text()
    for line in text.splitlines():
        if "repo:${" in line:
            assert "*" not in line, (
                f"wildcard in an OIDC subject: {line.strip()!r}. A pattern "
                f"like repo:yesterdaysrebel*/deltabt* also matches "
                f"yesterdaysrebel-evil/deltabt-x.")
