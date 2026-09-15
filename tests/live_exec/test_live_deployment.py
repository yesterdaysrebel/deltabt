"""The live image is built, goes somewhere separate, and needs no second edit.

WHY THESE EXIST

This pipeline has twice shipped a change that reached one link of a chain and
not the next: max_daily_loss_pct reached the host's env file and never entered
the container, and the deploy workflow's stack table drifted from Terraform and
dispatched rolls at two terminated instances. Both were "somebody has to
remember the other place".

Adding a live stack must therefore be ONE edit -- a `live_stacks` entry -- with
everything else keying off it. These tests hold the links together.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEPLOY = (ROOT / ".github/workflows/deploy.yml").read_text()
LIVE_TF = (ROOT / "infra/terraform/live.tf").read_text()


def test_something_builds_the_live_image():
    """An image nothing builds cannot be deployed, and nothing would say so."""
    assert "deploy/docker/Dockerfile.live" in DEPLOY, (
        "no workflow builds Dockerfile.live; a live stack would look "
        "configured and have no image to run")
    assert "build-live" in DEPLOY


def test_the_live_image_goes_to_its_own_repository():
    """Two images in one repository, told apart only by a tag convention, is
    one typo away from rolling a paper host onto a bot that can place orders."""
    assert 'aws_ecr_repository" "live"' in LIVE_TF
    # The paper build must not have been repointed at it. Note the job order in
    # this file is test -> build-live -> targets -> build -> deploy; YAML job
    # order does not affect execution, which `needs` decides.
    paper_build = DEPLOY[DEPLOY.index("  build:"):DEPLOY.index("  deploy:")]
    assert paper_build, "could not locate the paper build job"
    assert "Dockerfile.live" not in paper_build
    assert "file: deploy/docker/Dockerfile\n" in paper_build


def test_the_workflow_and_terraform_agree_on_the_repository_name():
    """The drift that dispatched rolls at terminated instances was exactly
    this: two places naming the same thing, and one of them stale."""
    # Terraform: "${local.name}-live", and local.name is the project prefix
    # that also produces the paper repository.
    assert '"${local.name}-live"' in LIVE_TF
    # The workflow's fallback must match that suffix.
    match = re.search(r"ECR_REPOSITORY_LIVE \|\| '([^']+)'", DEPLOY)
    assert match, "the live build has no repository name fallback"
    assert match.group(1).endswith("-live"), match.group(1)


def test_every_live_resource_is_gated_on_its_own_variable():
    """The `tnet` rehearsal stack now EXISTS in the default, deliberately.

    THIS TEST USED TO ASSERT `default = {}` -- that merging this work created
    no infrastructure at all. That guarantee was deliberately spent when the
    testnet rehearsal was scheduled (2026-09-15, by operator instruction), so
    re-asserting it would pin a decision that has been reversed. Recorded here
    rather than in a commit message, because a deleted assertion leaves no
    trace at the place someone will look.

    WHAT STILL HAS TO HOLD, and what this now checks: every live resource keys
    off a variable rather than existing unconditionally. Emptying
    `live_stacks` still removes the infrastructure, and the credential-bearing
    IAM policy still appears only when a credential is actually configured --
    so the blast radius of this file is still exactly what its variables say.
    """
    assert "length(var.live_stacks) > 0 ? 1 : 0" in LIVE_TF, (
        "the live ECR repository is not gated on a live stack existing")
    assert "if s.live" in LIVE_TF, (
        "the live SSM parameter is not filtered to live stacks")
    assert 'var.live_credential_secret_arn != "" ? 1 : 0' in LIVE_TF, (
        "the credential policy is not gated on a credential being configured; "
        "an empty ARN must grant access to nothing, not to everything")


def test_the_venue_defaults_to_testnet():
    """Reaching mainnet must be a deliberate edit, not a default."""
    block = LIVE_TF[LIVE_TF.index('variable "live_venue"'):]
    block = block[:block.index("\n}\n")]
    assert 'default     = "testnet"' in block, block


def test_the_credential_arn_actually_reaches_terraform():
    """THE LINK THAT WAS MISSING, and the reason this test exists.

    live.tf promises that adding a `live_stacks` entry is the only edit
    needed. But live_credential_secret_arn arrives from OUTSIDE Terraform, and
    nothing passed it: the workflow set TF_VAR_alarm_email and nothing else.
    With the variable defaulted to "", the failure is silent in the worst way
    -- aws_iam_role_policy.live_credentials gets count = 0, the SSM parameter
    is written "none", and the host boots, reads "none" and exit 90s forever,
    which is indistinguishable from a host waiting for its first deploy.

    This is the third time this pipeline has shipped a change that reached one
    link of a chain and not the next. The other two are in the header above.
    """
    infra = (ROOT / ".github/workflows/infrastructure.yml").read_text()
    assert "TF_VAR_live_credential_secret_arn" in infra, (
        "nothing passes the venue credential ARN to Terraform; a live stack "
        "would apply with it empty and the host would never trade")
    assert "vars.LIVE_CREDENTIAL_SECRET_ARN" in infra, (
        "the ARN is not read from a repository variable")
    # The VALUE must never be passed, only the ARN: Terraform writes every
    # variable it is given into state, in plaintext.
    for forbidden in ("DELTA_API_KEY", "DELTA_API_SECRET", "api_secret"):
        assert forbidden not in infra, (
            f"{forbidden} appears in the infrastructure workflow; Terraform "
            "state would then hold a venue credential in plaintext")


def test_the_build_skips_loudly_rather_than_failing_when_there_is_no_repository():
    """Today there is no live repository. That is the expected state, not a
    broken build, and the run must say which."""
    job = DEPLOY[DEPLOY.index("  build-live:"):DEPLOY.index("  targets:")]
    assert "describe-repositories" in job
    assert "no live stack is configured" in job
    assert "exists == 'true'" in job, (
        "the build steps are not gated on the repository existing")


def test_the_live_host_is_pointed_at_the_live_repository():
    """It was pointed at the PAPER one, for every stack.

    ec2.tf hardcoded `ecr_repository_url = aws_ecr_repository.bot.repository
    _url`, so a live host would pull `<paper repo>:<live sha>` -- a tag that
    exists only in the live repository. The pull fails and the bot never
    starts, on a host whose alarms are green because the container never came
    up to be silent.
    """
    ec2 = (ROOT / "infra/terraform/ec2.tf").read_text()
    assert "aws_ecr_repository.live[0].repository_url" in ec2, (
        "live hosts are not pointed at the live ECR repository")
    assert "aws_ecr_repository.live[0].name" in ec2, (
        "deploy.sh's tag pre-check would query the paper repository")
    # Keyed on the stack being live, not on anything else.
    assert "each.value.live" in ec2


def test_the_live_host_may_pull_its_image_and_read_its_parameters():
    """The shared instance policy grants NEITHER, and that is not visible.

    iam.tf's ECR grant names aws_ecr_repository.bot only, and its SSM grant
    names the image-tag parameters only. run_live.sh reads two more parameters
    and pulls a different repository, so without these the host fails with
    AccessDenied -- and because the credential read has no `|| true` under
    `set -euo pipefail`, that is a hard exit and a systemd restart loop rather
    than the clean exit 90 the script was written around.
    """
    assert 'aws_iam_role_policy" "live_host"' in LIVE_TF, (
        "nothing grants a live host access to its own image or parameters")
    assert "aws_ecr_repository.live[0].arn" in LIVE_TF
    assert "aws_ssm_parameter.live_venue" in LIVE_TF
    assert "aws_ssm_parameter.live_credential_arn" in LIVE_TF
    # Read-only. A bot that can rewrite which venue it trades on is a bot
    # whose configuration is not a fact about it.
    policy = LIVE_TF[LIVE_TF.index('aws_iam_role_policy" "live_host"'):]
    policy = policy[:policy.index("\n}\n")]
    assert "ssm:PutParameter" not in policy, (
        "the live host can overwrite its own venue or credential pointer")


def test_the_venue_reaches_the_host():
    """var.live_venue was declared, validated, and wired to NOTHING.

    `live_venue = "mainnet"` applied cleanly and the host ran testnet, because
    the only thing setting DELTA_ENV was run_live.sh's own fallback.
    """
    assert 'aws_ssm_parameter" "live_venue"' in LIVE_TF, (
        "live_venue reaches no host; it is a variable that changes nothing")
    assert "value = var.live_venue" in LIVE_TF

    # WHOLE-LINE COMMENTS STRIPPED, as tests/live/test_deployment_safety.py
    # does for its own scanners and for the same reason: run_live.sh explains
    # the fallback it REMOVED, and matching that prose would fail on the
    # documentation rather than the code. A scanner that fires on its own
    # explanation teaches people to delete the explanation.
    run_live = "\n".join(
        line for line in (ROOT / "deploy/aws/run_live.sh").read_text().splitlines()
        if not line.lstrip().startswith("#"))
    assert "delta_env" in run_live, "run_live.sh never reads the venue parameter"
    # No silent default: an unreadable venue must stop the host, not quietly
    # become testnet, which is the same lie in a different place.
    assert "${DELTA_ENV:-testnet}" not in run_live, (
        "run_live.sh still defaults the venue instead of refusing")
    assert "testnet|mainnet" in run_live, (
        "the venue value is not validated before use")


def test_the_live_image_is_immutable_and_protected():
    """The image tag is the only durable link between a database row and the
    code that executed the trade."""
    assert 'image_tag_mutability = "IMMUTABLE"' in LIVE_TF
    assert "prevent_destroy = true" in LIVE_TF


def test_the_git_sha_reaches_the_live_image():
    """preflight FAILS on an unknown SHA: a result that cannot be tied to code
    is not reproducible, and the container has no git."""
    job = DEPLOY[DEPLOY.index("  build-live:"):DEPLOY.index("  targets:")]
    assert "GIT_SHA=${{ github.sha }}" in job
