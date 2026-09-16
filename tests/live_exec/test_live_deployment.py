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
from tests.deploy_workflows import text as _deploy_text

DEPLOY = _deploy_text()

#: THE SPLIT, 2026-09-16. deploy.yml became one caller per venue plus the
#: shared _roll.yml. `DEPLOY` above is all of them concatenated, which is right
#: for "does the pipeline mention X" but wrong for "what does THIS job do" --
#: there are three `roll:` jobs now, so indexing into the concatenation would
#: silently read whichever came first. These name the file they mean.
from tests.deploy_workflows import ROLL as _ROLL_PATH
from tests.deploy_workflows import named as _named

PAPER_WF = _named("deploy-paper").read_text()
TESTNET_WF = _named("deploy-testnet").read_text()
ROLL_WF = _ROLL_PATH.read_text()


def _job(text: str, name: str) -> str:
    """One job's body, from `  <name>:` to the next top-level job."""
    start = text.index(f"\n  {name}:")
    rest = text[start + 1:]
    nxt = [i for i in (rest.find(f"\n  {n}:") for n in
                       ("wait-for-infrastructure", "test", "build", "build-live",
                        "targets", "targets-live", "roll"))
           if i > 0]
    return rest[:min(nxt)] if nxt else rest
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
    paper_build = _job(PAPER_WF, "build")
    assert paper_build, "could not locate the paper build job"
    assert "Dockerfile.live" not in paper_build
    assert "file: deploy/docker/Dockerfile\n" in paper_build


def test_the_workflow_and_terraform_agree_on_the_repository_name():
    """The drift that dispatched rolls at terminated instances was exactly
    this: two places naming the same thing, and one of them stale.

    AND THIS TEST FAILED TO CATCH IT, which is the lesson worth keeping. It
    asserted only that the workflow's fallback ENDED WITH "-live". The
    fallback was 'deltabt-live'; Terraform creates 'deltabt-paper-live',
    because local.name is "deltabt-${var.environment}". Both end in "-live",
    so this passed green while the two names disagreed, and the first live
    build failed with AccessDenied -- the role is granted on the real
    repository, so asking about a different name is a permissions error, not a
    missing one. It read as an IAM bug for two runs.

    A suffix is a property of the name. The name is the thing that has to
    match, so it is now derived and compared in full.
    """
    assert '"${local.name}-live"' in LIVE_TF

    variables = (ROOT / "infra/terraform/variables.tf").read_text()
    block = variables[variables.index('variable "environment"'):]
    block = block[:block.index("\n}\n")]
    default = next(l for l in block.splitlines() if l.strip().startswith("default"))
    environment = default.split("=", 1)[1].strip().strip('"')

    network = (ROOT / "infra/terraform/network.tf").read_text()
    assert 'name = "deltabt-${var.environment}"' in network, (
        "local.name is no longer deltabt-<environment>; this derivation is stale")

    expected = f"deltabt-{environment}-live"
    match = re.search(r"ECR_REPOSITORY_LIVE \|\| '([^']+)'", DEPLOY)
    assert match, "the live build has no repository name fallback"
    assert match.group(1) == expected, (
        f"the workflow looks for {match.group(1)!r}; Terraform creates "
        f"{expected!r}. A mismatch surfaces as AccessDenied, not NotFound.")


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
    # The credential grant is scoped to the secret's NAME pattern, because the
    # secret is created by an operator and its ARN suffix cannot be known in
    # advance. A pattern is not a loosening -- two secrets cannot share a name,
    # so it matches at most one -- but `*` would be, so that is checked.
    assert "local.live_credential_arn_pattern" in LIVE_TF, (
        "the credential policy is not scoped to the credential's name")
    assert "-??????" in LIVE_TF, (
        "the ARN pattern does not pin the suffix length")
    assert ':secret:*"' not in LIVE_TF and '"*"' not in LIVE_TF.replace(
        'Resource = "*" # this action does not accept a resource restriction', ""), (
        "a live grant is scoped to every secret in the account")
    assert LIVE_TF.count("length(var.live_stacks) > 0 ? 1 : 0") >= 4, (
        "not every live resource is gated on a live stack existing")


def test_the_venue_defaults_to_testnet():
    """Reaching prod must be a deliberate edit, not a default."""
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

    This was the third time this pipeline shipped a change that reached one
    link of a chain and not the next. The other two are in the header above.

    THE LINK IS NOW GONE RATHER THAN REPAIRED, which is the better fix. The
    first version of this test asserted the workflow passed
    TF_VAR_live_credential_secret_arn; the second asserted Terraform declared
    the secret. It now does neither: the credential is referred to by a NAME
    Terraform can decide without the secret existing, so there is nothing to
    communicate in either direction. A link that does not exist cannot rot.
    """
    infra = (ROOT / ".github/workflows/infrastructure.yml").read_text()
    assert "live_credential_name" in LIVE_TF, (
        "nothing names the credential, so its location must again be supplied "
        "from outside -- the link that was missing before")
    assert "live_credential_secret_arn" not in infra, (
        "the workflow still passes an ARN variable that no longer exists")
    # TERRAFORM MUST NEITHER CREATE NOR READ IT. A resource would put the
    # container in state and force it to exist before the credential could;
    # a data source would fail the apply whenever it does not yet. Either way
    # the credential's lifecycle stops being the operator's.
    assert 'resource "aws_secretsmanager_secret"' not in LIVE_TF, (
        "Terraform manages the credential container, which forces it to exist "
        "after the infrastructure rather than before")
    assert 'data "aws_secretsmanager_secret"' not in LIVE_TF, (
        "Terraform reads the secret, so an apply fails whenever it is absent")
    assert "secret_string" not in LIVE_TF, (
        "a secret VALUE appears in Terraform; it would be written to state "
        "in plaintext")
    # And no credential may be handed to Terraform by the workflow either.
    for forbidden in ("DELTA_API_KEY", "DELTA_API_SECRET", "api_secret"):
        assert forbidden not in infra, (
            f"{forbidden} appears in the infrastructure workflow; Terraform "
            "state would then hold a venue credential in plaintext")


def test_the_build_skips_loudly_rather_than_failing_when_there_is_no_repository():
    """Today there is no live repository. That is the expected state, not a
    broken build, and the run must say which."""
    job = _job(TESTNET_WF, "build-live")
    assert "describe-repositories" in job
    assert "no live stack is configured" in job
    assert "exists == 'true'" in job, (
        "the build steps are not gated on the repository existing")


def test_the_live_build_waits_for_the_infrastructure_that_grants_it_access():
    """It did not, and the first merge with a live stack failed because of it.

    build-live asks ECR whether the live repository exists. Both that
    repository and the ecr:DescribeRepositories grant that lets it ask are
    created by the infrastructure apply. Declaring only `needs: test`, it ran
    four minutes ahead of the apply on 2026-09-15 and got AccessDenied.

    It failed loudly rather than reporting "no live stack is configured" --
    that part worked -- but a job that cannot succeed on the merge that
    introduces its own dependencies is ordered wrongly, not merely unlucky.
    """
    d = _job(TESTNET_WF, "build-live")
    needs = next(l for l in d.splitlines() if l.strip().startswith("needs:"))
    assert "wait-for-infrastructure" in needs, (
        "build-live does not wait for the apply that creates the repository "
        "it inspects and grants the permission it inspects it with")


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

    `live_venue = "mainnet"` (now "prod") applied cleanly and the host ran
    testnet, because
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
    assert "testnet|prod" in run_live, (
        "the venue value is not validated before use")


def test_the_live_image_is_immutable_and_protected():
    """The image tag is the only durable link between a database row and the
    code that executed the trade."""
    assert 'image_tag_mutability = "IMMUTABLE"' in LIVE_TF
    assert "prevent_destroy = true" in LIVE_TF


def test_something_actually_deploys_the_live_image():
    """build-live pushed an image that NOTHING consumed.

    No job declared `needs: build-live`, and `deploy` reads the paper build's
    tag and vars.ECR_REPOSITORY. So the live image was built, pushed to its own
    repository, and never deployed by anything -- a pipeline that looked
    complete because the build step went green.
    """
    assert "  roll:" in TESTNET_WF, "deploy-testnet.yml has no roll job"
    assert "needs['build-live']" in TESTNET_WF, (
        "nothing consumes build-live; the live image is built and abandoned")
    job = _job(TESTNET_WF, "roll")
    # The LIVE repository, not the paper one.
    assert "needs['build-live'].outputs.repository" in job, (
        "the live deploy does not use the live repository")
    assert "vars.ECR_REPOSITORY " not in job and "vars.ECR_REPOSITORY }}" not in job, (
        "the live deploy reads the PAPER repository variable")


def test_build_live_publishes_what_it_built():
    """An output-less build cannot be consumed, however correct it is."""
    job = _job(TESTNET_WF, "build-live")
    assert "outputs:" in job, "build-live publishes nothing"
    for name in ("tag:", "repository:", "exists:"):
        assert name in job, name


def test_the_live_repository_check_distinguishes_absent_from_denied():
    """`2>&1` made AccessDenied indistinguishable from 'not configured yet'.

    The check decided whether to build at all, and treated every failure as the
    expected empty state -- printing a reassuring notice while the repository
    existed and the role simply could not see it.
    """
    job = _job(TESTNET_WF, "build-live")
    assert "RepositoryNotFoundException" in job, (
        "the repository check cannot tell 'absent' from 'denied'")
    assert "could not determine whether" in job, (
        "an unreadable repository does not fail the build loudly")


def test_the_live_stack_list_is_not_a_second_table():
    """The paper table drifted from Terraform and rolled terminated hosts.

    Duplicating that for live stacks would reintroduce the exact failure the
    live work exists to avoid, so the list is read from live.tf.
    """
    assert "scripts/live_stacks_matrix.py" in DEPLOY, (
        "the live stack list is not derived from Terraform")
    assert "  targets-live:" in TESTNET_WF, "there is no live targets job"
    job = _job(TESTNET_WF, "targets-live")
    assert '"stack":' not in job, (
        "targets-live carries a hardcoded stack table, which is what drifted "
        "for the paper path")


def test_the_live_matrix_matches_the_configured_live_stacks():
    """The parser and Terraform must agree, or the deploy rolls the wrong set."""
    import json
    import subprocess
    import sys

    out = subprocess.run([sys.executable, str(ROOT / "scripts/live_stacks_matrix.py")],
                         capture_output=True, text=True, check=True).stdout
    rows = json.loads(out)
    # Every stack named in live.tf's default block, and no others.
    block = LIVE_TF[LIVE_TF.index('variable "live_stacks"'):]
    block = block[block.index("default = {"):]
    names = {r["stack"] for r in rows}
    assert names, "the live matrix is empty; nothing would ever be deployed"
    for name in names:
        assert f"{name} =" in block, f"{name} is not a live stack in live.tf"
    for r in rows:
        assert r["variant"].startswith("SPEC:"), r


def test_the_secret_name_in_the_workflow_matches_terraform():
    """A name spelled in two places is the drift this pipeline keeps shipping.

    The readiness check asks Secrets Manager for the credential by NAME, and
    Terraform decides that name. If they disagree the check gets
    ResourceNotFoundException, reports "no live stack has been applied", and
    the roll skips forever on a stack that is entirely ready -- another
    reassuring notice over a broken link.

    Derived here rather than duplicated: local.name is "deltabt-${var
    .environment}" (infra/terraform/network.tf) and environment defaults to
    "paper", so the two halves are checked against their sources.
    """
    network = (ROOT / "infra/terraform/network.tf").read_text()
    assert 'name = "deltabt-${var.environment}"' in network, (
        "local.name is no longer deltabt-<environment>; the derivation below "
        "is stale")

    variables = (ROOT / "infra/terraform/variables.tf").read_text()
    block = variables[variables.index('variable "environment"'):]
    block = block[:block.index("\n}\n")]
    default = next(l for l in block.splitlines() if l.strip().startswith("default"))
    environment = default.split("=", 1)[1].strip().strip('"')

    # What Terraform tells the host to look for.
    line = next(l for l in LIVE_TF.splitlines()
                if l.strip().startswith("live_credential_name"))
    suffix = line.split("=", 1)[1].strip().strip('"')
    expected = suffix.replace("${local.name}", f"deltabt-{environment}")

    assert f"SECRET_NAME: {expected}" in DEPLOY, (
        f"the workflow looks for a secret named something other than "
        f"{expected!r}, which is what Terraform creates")


def test_a_live_stack_can_be_rolled_to_an_explicit_tag():
    """THE ESCAPE HATCH FOR A HOST TOO BROKEN TO ANSWER FOR ITSELF.

    Both the roll guard and the retire step talk to the image ALREADY RUNNING.
    On 2026-09-15 that image could not complete a bar, so `forward-test status`
    and `forward-test stop` each booted it, crash-looped, and never returned --
    the guard skipped the roll while reporting success, and with `only_stack`
    forcing past it the retire hung instead. A bot too broken to answer blocked
    the deploy that would have fixed it.

    A dispatch with an explicit `image_tag` sets the experiment id to 'none',
    which skips retire and successor, and rolls the image without asking the
    old one anything.

    THAT PATH WAS DEAD. build-live carried `if: github.event.inputs.image_tag
    == ''` at JOB level, so a manual tag skipped the whole job; deploy-live
    requires its `exists` output, and a skipped job reports nothing. The one
    recovery route a live stack had was disabled by the flag that selects it.
    """
    job = _job(TESTNET_WF, "build-live")
    header = job[:job.index("    steps:")]
    assert "if: github.event.inputs.image_tag == ''" not in header, (
        "build-live skips entirely on a manual tag, so deploy-live sees no "
        "`exists` output and the live rollback path is dead")
    # The BUILD steps must still be skipped -- there is nothing to build for a
    # tag that already exists.
    assert "github.event.inputs.image_tag == ''" in job[job.index("    steps:"):], (
        "nothing stops build-live rebuilding on a manual rollback")

    live = ROLL_WF
    for step in ("retire the running experiment", "start the successor experiment"):
        i = live.index(step)
        cond = live[i:i + 400]
        assert "id != 'none'" in cond, (
            f"{step!r} is not skipped on a manual tag, so a rollback would "
            f"still have to interrogate the image it is replacing")


def test_the_live_roll_waits_for_a_credential_rather_than_going_red():
    """The first merge brings up a host that CANNOT have a credential yet.

    The venue key must allowlist the host's IP and the EIP does not exist until
    the apply, so the secret is necessarily created afterwards. Until then
    run_live.sh reads "none" and exit 90s -- correct and deliberate, but it
    means /readyz never passes and deploy.sh rolls back. Without this gate the
    job would go red on exactly the merge that is meant to stand the stack up,
    and a red deploy is how a real failure gets ignored later.
    """
    job = _job(TESTNET_WF, "roll")
    condition = job[job.index("if:"):job.index("uses:")]
    assert "needs['targets-live'].outputs.ready == 'true'" in condition, (
        "the live roll is not gated on a credential existing; the first merge "
        "would roll a host that cannot start and fail the run")
    targets = _job(TESTNET_WF, "targets-live")
    # ASKED OF THE ACCOUNT, NOT OF A VARIABLE. Readiness was a repository
    # variable a human set after creating the secret by hand; it is now whether
    # Secrets Manager holds a value, which cannot be forgotten or mistyped.
    assert "describe-secret" in targets, (
        "readiness is not determined from the secret itself")
    assert "AWSCURRENT" in targets, (
        "nothing distinguishes a declared secret from a populated one")
    # Metadata only: CI must never be able to read the credential it checks.
    # COMMENTS STRIPPED -- the step explains that only the instance role holds
    # GetSecretValue, and matching that prose would fail on the documentation
    # rather than the code. Same rule tests/live/test_deployment_safety.py uses.
    code = "\n".join(l for l in targets.splitlines()
                     if not l.lstrip().startswith("#"))
    assert "GetSecretValue" not in code and "get-secret-value" not in code, (
        "the readiness check reads the credential value")
    # And the skip must say why, or it is just an absent job.
    assert "holds no value" in targets, (
        "nothing explains why the live deploy did not run")


def test_only_stack_may_name_a_live_stack():
    """`only_stack=tnet` is the NORMAL way a live stack is first brought up.

    Creating the venue API key needs the host's EIP allowlisted, and the EIP
    does not exist until the apply -- so the credential secret is necessarily
    created AFTER the host, and the host then has to be kicked to read it.
    That kick is a dispatch with only_stack set to the live stack.

    The paper `targets` job used to `exit 1` on any name not in its own table,
    which made that routine dispatch a red run. Worse, `deploy` is gated on
    this job's matrix, so the failure would not have explained itself.
    """
    job = _job(PAPER_WF, "targets")
    assert "live_stacks_matrix.py" in job, (
        "the paper targets job cannot tell a live stack from a typo, so a "
        "routine live dispatch fails the run")
    assert "is a LIVE stack" in job
    # A genuinely unknown name must STILL fail -- silently rolling nothing is
    # how a deploy that did not happen looks like one that did.
    assert "matches no paper or live stack" in job
    assert "exit 1" in job


def test_the_live_roll_keeps_the_experiment_guard():
    """Rolling retires the running experiment and resets its sample to zero.

    The paper path learned this the hard way when `hours` was rolled four days
    into a thirty-day run by an unrelated merge. A separate live job means a
    separate copy of the guard, and a copy that was dropped would be silent.
    """
    # SHARED WITH PAPER SINCE THE SPLIT, which is the point: the guard used
    # to exist twice and a dropped copy would have been silent. It now lives in
    # _roll.yml, and deploy-testnet.yml reaching it is asserted above.
    job = ROLL_WF
    assert "no experiment is RUNNING" in job, "the live roll has no guard"
    assert "roll=no" in job, "the live guard does not fail closed"
    assert 'Action=stop' in job, "the live roll never retires the experiment"
    assert "Action=start,ExperimentId=" in job, (
        "the live roll never registers a successor experiment")


def test_the_git_sha_reaches_the_live_image():
    """preflight FAILS on an unknown SHA: a result that cannot be tied to code
    is not reproducible, and the container has no git."""
    job = _job(TESTNET_WF, "build-live")
    assert "GIT_SHA=${{ github.sha }}" in job
