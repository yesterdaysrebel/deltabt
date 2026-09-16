"""Every `environment:` a workflow job declares must be a trusted OIDC subject.

WHY THIS EXISTS -- IT HAS HAPPENED TWICE

GitHub's OIDC subject for a job that declares `environment: X` is
`repo:<org>/<repo>:environment:X`, NOT the ref subject. So a job with a new
environment cannot assume the deploy role until that exact string is added to
the trust policy, and the failure is
`Not authorized to perform sts:AssumeRoleWithWebIdentity` -- which names
neither the environment nor the workflow, and looks like a broken role.

  2026-08-19  `paper-deploy` was split out of `paper` and this list was not
              updated. Every roll failed. It went unnoticed for three commits
              because paths-ignore meant no deploy ran. `build` kept working
              throughout -- it declares no environment, so it matched the ref
              subject instead, which made the role look healthy.

  2026-09-15  `live-deploy` was added for the live roll, for the good reason
              that a reviewer should be attachable to live rolls without
              gating paper. Same omission. The first live roll built the
              image, pushed it, and died at the credentials step.

Both times the fix was one line and the diagnosis was the expensive part. This
derives the environments from the workflows instead of restating them, so the
list cannot drift again.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
OIDC = (ROOT / "infra/terraform/github_oidc.tf").read_text()

#: `environment: name` at job level. Deliberately NOT PyYAML: it is absent from
#: the test environment, and installing it means editing pyproject.toml, which
#: is in the deploy allow-list -- so adding a dependency to test the pipeline
#: would roll the running experiment. The sibling deploy-path tests take the
#: same approach for the same reason.
ENVIRONMENT = re.compile(r"^\s{4}environment:\s*([A-Za-z0-9._-]+)\s*$", re.M)

#: THE SAME FACT, ONE LEVEL OF INDIRECTION DOWN. Since the 2026-09-16 split the
#: roll job lives in the reusable _roll.yml and its `environment:` is
#: `${{ inputs.environment }}` -- an expression, which the literal regex above
#: cannot see and must not pretend to. The real name is in each CALLER's
#: `with:` block, two spaces further in. Without this the scan would still find
#: environments (infrastructure.yml has literal ones) so the vacuity guard
#: below would PASS while every deploy environment went unchecked -- the exact
#: shape of failure this file exists to prevent.
ENVIRONMENT_INPUT = re.compile(
    r"^\s{6}environment:\s*([A-Za-z0-9._-]+)\s*$", re.M)

#: A job whose environment is an expression. It is only safe because the
#: callers supply a literal; test_a_reusable_environment_is_fed_a_literal pins
#: that, so this cannot become a hole.
ENVIRONMENT_EXPR = re.compile(r"^\s{4}environment:\s*\$\{\{", re.M)


def declared_environments() -> dict[str, set[str]]:
    """Environment names per workflow file, for a useful failure message."""
    found: dict[str, set[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text()
        names = set(ENVIRONMENT.findall(text)) | set(
            ENVIRONMENT_INPUT.findall(text))
        if names:
            found[path.name] = names
    return found


def test_the_scan_finds_the_environments_that_are_actually_there():
    """A scan that finds nothing would pass vacuously and prove nothing."""
    found = declared_environments()
    names = {n for s in found.values() for n in s}
    assert names, "no `environment:` found in any workflow; the regex is wrong"
    # The two we know are real, so a change in indentation cannot silently
    # empty this test.
    assert "paper-deploy" in names, found
    assert "live-deploy" in names, found
    # Added with the split. `mainnet-deploy` is declared by a workflow nothing
    # has run yet, which is precisely when a missing trust subject goes
    # unnoticed -- the 2026-08-19 failure hid behind paths-ignore for three
    # commits for the same reason.
    assert "mainnet-deploy" in names, found


def test_a_reusable_environment_is_fed_a_literal():
    """The indirection must not become a hole.

    A job may write `environment: ${{ inputs.environment }}` only because every
    caller passes a literal that the scan above can read. If a caller ever
    passed an expression instead, the name would be invisible here and the
    trust-policy check would silently stop covering it.
    """
    callers = [p for p in sorted(WORKFLOWS.glob("*.yml"))
               if "uses: ./.github/workflows/_roll.yml" in p.read_text()]
    assert callers, (
        "nothing calls _roll.yml. Either the reusable roll workflow was "
        "renamed and this test was not, or the pipeline no longer uses it -- "
        "and this test would otherwise pass by checking nothing.")
    for path in callers:
        assert ENVIRONMENT_INPUT.search(path.read_text()), (
            f"{path.name} calls _roll.yml without a LITERAL `environment:` in "
            f"its `with:` block. _roll.yml's environment is an expression, so "
            f"this file is the only place the real name appears; an "
            f"expression here makes it invisible to the trust-policy check.")


def test_the_roll_workflow_takes_its_environment_from_the_caller():
    """Guards the guard: if _roll.yml ever hardcodes an environment, the test
    above is checking an indirection that no longer exists."""
    roll = WORKFLOWS / "_roll.yml"
    assert roll.exists(), "the shared roll workflow is gone"
    assert ENVIRONMENT_EXPR.search(roll.read_text()), (
        "_roll.yml no longer takes its environment from an input, so the "
        "caller-side check above may be asserting nothing")


def test_every_workflow_environment_is_a_trusted_oidc_subject():
    missing = {
        f"{workflow}:{name}"
        for workflow, names in declared_environments().items()
        for name in names
        if f'"repo:${{r}}:environment:{name}"' not in OIDC
    }
    assert not missing, (
        f"these job environments are not in github_oidc.tf's github_subjects: "
        f"{sorted(missing)}. A job declaring `environment: X` presents the "
        f"subject repo:<org>/<repo>:environment:X, so it will fail with "
        f"'Not authorized to perform sts:AssumeRoleWithWebIdentity' -- an "
        f"error that names neither the environment nor the workflow.")


def test_the_check_catches_a_planted_environment():
    """The scanner must fire on a violation, or it is decoration."""
    assert '"repo:${r}:environment:nonesuch"' not in OIDC
    fake = {"deploy.yml": {"nonesuch"}}
    missing = [
        f"{w}:{n}" for w, names in fake.items() for n in names
        if f'"repo:${{r}}:environment:{n}"' not in OIDC
    ]
    assert missing == ["deploy.yml:nonesuch"]
