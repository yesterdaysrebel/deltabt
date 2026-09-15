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


def test_nothing_live_is_created_until_a_live_stack_exists():
    """`terraform plan` must report no additions on a merge of this work."""
    assert "default = {}" in LIVE_TF, "live_stacks is not empty by default"
    assert "length(var.live_stacks) > 0 ? 1 : 0" in LIVE_TF, (
        "the live ECR repository is not gated on a live stack existing")
    assert "if s.live" in LIVE_TF, (
        "the live SSM parameter is not filtered to live stacks")


def test_the_build_skips_loudly_rather_than_failing_when_there_is_no_repository():
    """Today there is no live repository. That is the expected state, not a
    broken build, and the run must say which."""
    job = DEPLOY[DEPLOY.index("  build-live:"):DEPLOY.index("  targets:")]
    assert "describe-repositories" in job
    assert "no live stack is configured" in job
    assert "exists == 'true'" in job, (
        "the build steps are not gated on the repository existing")


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
