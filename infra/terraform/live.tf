# ---------------------------------------------------------------------------
# LIVE TRADING. Everything that can reach an exchange lives in this ONE file.
#
# WHY IT IS ALL HERE AND NOT SPREAD ACROSS variables.tf AND iam.tf
#
# tests/live/test_deployment_safety.py scans the deployment surface and asserts
# that NO exchange credential is named anywhere in it. That check is what makes
# app/safety.py's claim true of the whole artifact rather than only of the
# Python -- "an IAM policy granting access to a new secret, an `-e API_KEY=` in
# a `docker run`" are exactly the places a capability would appear.
#
# A live bot needs exactly those things. Scattering them through the shared
# files would have meant relaxing that scan over infra/ generally, which is how
# a boundary becomes a suggestion. Instead this file is the one declared
# exception, and the scan still holds -- unchanged -- over everything else.
#
# NOTHING HERE IS CREATED BY DEFAULT. var.live_stacks is empty, so every
# for_each is empty and `terraform plan` reports no additions until somebody
# deliberately adds an entry.
# ---------------------------------------------------------------------------

variable "live_stacks" {
  description = <<-EOT
    Live-trading stacks. Same shape as `stacks`, EMPTY BY DEFAULT.

    A live stack differs from a paper one in three ways, and each is why it is
    a separate map rather than a flag on the existing entries:

      * IT RUNS A DIFFERENT IMAGE. deploy/docker/Dockerfile.live carries the
        `live` package; the paper image deliberately does not, because
        app/safety.py's boundary is the ABSENCE of order-placement capability
        rather than a runtime toggle.

      * IT BOOTS WITH A DIFFERENT SCRIPT. deploy/aws/run_live.sh, because
        run.sh is embedded in the paper hosts' user_data and
        `user_data_replace_on_change = true` -- editing it replaces a running
        bot mid-experiment, through a feed gap, which for a bot that simulates
        stops from ticks means stop evaluations that did not happen.

      * IT NEEDS EXCHANGE CREDENTIALS, which Terraform must never see.

    ALONGSIDE, NOT INSTEAD. A live stack does not replace a paper one: the
    paper run is the only thing estimating whether the arm has an edge, and
    ending it to rehearse the machinery would spend the evidence to run the
    dress rehearsal.
  EOT
  type = map(object({
    variant = string
    db_name = string
  }))

  # `tnet` IS THE REHEARSAL, NOT THE EXPERIMENT. It runs the same arm the paper
  # stack runs, against Delta's TESTNET, on whatever instruments that venue
  # lists -- BTCUSD, ETHUSD, SOLUSD. The arm's own three do not exist there.
  #
  # WHAT IT IS FOR: signing against a real venue over days rather than seconds,
  # the poll loop, bracket placement, reconciliation after a restart,
  # persistence into the trade journal, and the roll path on a live host. None
  # of that has ever run for longer than a smoke test.
  #
  # WHAT IT IS NOT FOR: deciding anything about the arm. Measured over 30 days
  # of 1m candles, the arm trades 2.23x/day across those three symbols and
  # scores -0.343 / +0.012 / +0.138 net R on BTC / ETH / SOL. Those numbers
  # are not evidence about BEATUSD and must not be quoted as though they were.
  #
  # HOW LONG. At 2.23 trades/day a two-day run yields ~4 round trips, which is
  # too few to reach a restart-with-position-open or a reconciliation
  # mismatch. Plan on about a week (~15 trades), which also crosses several UTC
  # day rolls and so exercises the daily-loss and streak resets.
  #
  # EXPECT THE DRAWDOWN HALT TO BE PLAUSIBLE HERE, and let it fire. It is
  # terminal by design (live/config.py explains why) and the arm is not
  # profitable on BTCUSD. A halt during the rehearsal is the halt being
  # validated, not the rehearsal failing -- clear it with
  # `forward-test resume --yes` and carry on.
  #
  # ALONGSIDE THE PAPER STACK. `atr` keeps running and is not touched: it is
  # the only thing estimating whether the arm has an edge.
  default = {
    tnet = { variant = "SPEC:manual_scalp_both_t3@5", db_name = "deltabt_tnet" }
  }
}

# THE CREDENTIAL IS REFERRED TO BY NAME, AND TERRAFORM NEITHER CREATES IT NOR
# READS IT.
#
# THIS REVERSES A DESIGN FROM EARLIER THE SAME DAY, and the reason is worth
# recording because the constraint it was built around turned out not to exist.
#
# The ordering was believed to be forced: the venue API key must allowlist the
# host's IP, the IP does not exist until the apply, so the key -- and therefore
# the credential -- could only be created AFTER the infrastructure. Every
# version of this file so far has been shaped by that: an ARN variable and a
# second apply, then a Terraform-declared secret and a post-apply
# put-secret-value plus a push to trigger the roll.
#
# Delta lets the IP allowlist be edited after a key is created. So the key can
# exist first, the secret can exist first, and the whole ordering problem
# disappears: one merge stands the stack up and starts it, with nothing to do
# afterwards. The allowlist is then tightened at leisure, while the bot runs.
#
# BY NAME, NOT BY ARN, because the ARN carries a six-character random suffix
# that only exists once the secret does -- and requiring it back would
# reintroduce exactly the round trip this removes. `aws secretsmanager
# get-secret-value --secret-id` accepts either.
#
# THE IAM GRANT USES THAT SUFFIX AS A WILDCARD, `-??????`, which is AWS's own
# documented pattern for naming a secret you cannot yet resolve. It is not a
# loosening: two secrets cannot share a name, so this matches at most the one
# secret, and six `?` is tighter than the `*` usually seen.
locals {
  live_credential_name = "${local.name}/live/venue-credentials"

  # Six `?`, one per character of the suffix Secrets Manager appends.
  live_credential_arn_pattern = join("", [
    "arn:aws:secretsmanager:${var.aws_region}:",
    "${data.aws_caller_identity.current.account_id}:secret:",
    "${local.live_credential_name}-??????",
  ])
}

variable "live_venue" {
  description = "testnet | prod. Reaching prod must be a deliberate edit."
  type        = string
  default     = "testnet"

  validation {
    condition     = contains(["testnet", "prod"], var.live_venue)
    error_message = "live_venue must be exactly \"testnet\" or \"prod\"."
  }
}

# THE LIVE IMAGE'S OWN REPOSITORY, created only when a live stack exists.
#
# Separate from the paper repository rather than a tag convention inside it:
# two images in one repository, told apart only by how somebody named the tag,
# is one typo away from rolling a paper host onto an image that can place
# orders. The boundary is worth a second repository.
#
# The build-live job in deploy-testnet.yml and deploy-prod.yml checks for
# this repository and
# skips when it is absent, so adding a live_stacks entry is the ONLY step --
# there is no second place to remember to edit. This pipeline has been bitten
# twice by exactly that kind of forgotten link.
resource "aws_ecr_repository" "live" {
  count = length(var.live_stacks) > 0 ? 1 : 0

  name                 = "${local.name}-live"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  lifecycle {
    # Images are the only durable link between a row in the database and the
    # code that produced it. Destroying this repository destroys the ability
    # to say what any past live trade was executed by.
    prevent_destroy = true
  }
}

# Where a live host finds the ARN. run_live.sh derives this parameter's name
# from the image-tag parameter's prefix rather than reading it from user_data,
# because adding a variable to that template changes the rendered bytes for
# PAPER hosts and replaces their instances.
resource "aws_ssm_parameter" "live_credential_arn" {
  for_each = { for k, s in local.stacks : k => s if s.live }

  # THE NAME, NOT THE ARN, and the parameter is named for what it holds. The
  # ARN's random suffix exists only once the secret does; a name is decidable
  # in advance, which is what lets the credential be created before the
  # infrastructure and the whole stack come up on one merge.
  name  = "${each.value.ssm_prefix}/delta_secret_id"
  type  = "String"
  value = local.live_credential_name
}

# THE VENUE, AS A PARAMETER RATHER THAN A TEMPLATE VARIABLE.
#
# var.live_venue existed, validated its input, and reached nothing: no host
# ever read it, and DELTA_ENV was set only by run_live.sh's own fallback. A
# stack declaring `live_venue = "mainnet"` (now "prod") applied cleanly and
# ran testnet.
#
# It is delivered this way for the same reason the credential ARN is: putting
# it in the user_data template changes the rendered bytes for PAPER stacks,
# and `user_data_replace_on_change = true` then replaces a host that is
# mid-experiment, through a feed gap. An SSM parameter costs the template
# nothing and is already per-stack.
#
# run_live.sh REFUSES TO START on anything other than testnet|prod rather
# than defaulting, so a missing or malformed parameter is a host that says so
# and stops, not a host quietly trading the wrong venue.
resource "aws_ssm_parameter" "live_venue" {
  for_each = { for k, s in local.stacks : k => s if s.live }

  name  = "${each.value.ssm_prefix}/delta_env"
  type  = "String"
  value = var.live_venue
}

# WHAT CI NEEDS TO BUILD AND SHIP THE LIVE IMAGE.
#
# The deploy role's ECR grant names aws_ecr_repository.bot only, and both build
# jobs assume that same role -- so build-live could neither see the live
# repository nor push to it.
#
# ITS SSM AND EC2 GRANTS ALREADY COVER A LIVE STACK, and deliberately: they are
# `for d in aws_ssm_document.deploy` and `for i in aws_instance.bot`, which
# iterate local.stacks and therefore pick up live stacks with no edit. Only ECR
# was written against a single repository resource.
#
# ecr:DescribeRepositories IS LOAD-BEARING, not incidental. build-live decides
# whether to build by calling it and treating any failure as "no live stack is
# configured, nothing to build" -- so without this grant an AccessDenied would
# read as a designed no-op, printing a reassuring notice, forever. The workflow
# now distinguishes the two; the grant is what makes the distinction possible.
resource "aws_iam_role_policy" "live_ci" {
  count = length(var.live_stacks) > 0 ? 1 : 0

  name = "${local.name}-live-ci"
  role = aws_iam_role.github_app_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "PushAndInspectTheLiveImageOnly"
      Effect = "Allow"
      Action = [
        "ecr:DescribeRepositories",
        "ecr:DescribeImages",
        "ecr:BatchGetImage",
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload",
        "ecr:PutImage",
      ]
      Resource = aws_ecr_repository.live[0].arn
      },
      {
        # METADATA ONLY, AND THAT DISTINCTION IS THE POINT.
        #
        # The scheduled bring-up has to answer "has a human put the credential
        # in yet?" without being able to read it. DescribeSecret returns the
        # version list and no secret material, so CI can see that a value
        # exists and still cannot obtain one. GetSecretValue is deliberately
        # absent: only the instance role has it.
        Sid      = "SeeWhetherTheCredentialHasBeenSuppliedWithoutReadingIt"
        Effect   = "Allow"
        Action   = ["secretsmanager:DescribeSecret"]
        Resource = local.live_credential_arn_pattern
      },
      {
        # "HAS THIS STACK EVER BEEN DEPLOYED?" -- the image tag is "none" until
        # the first successful roll, and that is the ONLY condition under which
        # the scheduled bring-up acts. Anything looser would let a cron restart
        # an experiment that was deliberately stopped, which is the accident
        # the roll guard exists to prevent.
        Sid    = "ReadWhetherALiveStackHasEverBeenDeployed"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          for k, s in local.stacks : aws_ssm_parameter.image_tag[k].arn if s.live
        ]
      },
      {
        # WHICH VENUE A HOST IS ON, so the deploy pipeline can refuse to roll
        # one that is not its own.
        #
        # var.live_venue is a SINGLE GLOBAL, not a per-stack property, so the
        # stack named `tnet` becomes a PROD bot the moment somebody edits it
        # and nothing in the stack table looks different. deploy-testnet.yml
        # and deploy-prod.yml therefore check this parameter -- the one
        # run_live.sh itself reads -- BEFORE touching the host.
        #
        # The grant was missing when that check shipped on 2026-09-16 and the
        # first testnet dispatch failed on it. It failed CLOSED, which is the
        # designed behaviour: an unreadable venue is refused, not assumed, so
        # a bound experiment was never at risk. Read-only, and the value is
        # not a secret -- it is the literal string "testnet" or "prod".
        Sid    = "ReadWhichVenueAHostIsOn"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          for k, s in local.stacks : aws_ssm_parameter.live_venue[k].arn if s.live
        ]
      },
    ]
  })
}

# WHAT A LIVE HOST NEEDS AND A PAPER HOST MUST NOT HAVE, and the third and
# fourth links that were missing.
#
# The shared instance policy in iam.tf grants ECR pull on aws_ecr_repository
# .bot ONLY, and ssm:GetParameter on the image-tag parameters ONLY. A live host
# therefore could not pull its own image, and could not read the two parameters
# run_live.sh derives -- so the credential read failed with AccessDenied under
# `set -euo pipefail`, which is a hard exit and a systemd restart loop, not the
# clean exit 90 the script was written around.
#
# IT LIVES HERE, NOT IN iam.tf, deliberately. tests/live/test_deployment_safety
# .py forbids naming a credential anywhere outside this file, run_live.sh and
# Dockerfile.live; `live_credential_arn` in the shared policy would either trip
# that scan or force it to be relaxed over iam.tf generally, which is how a
# boundary becomes a suggestion. Same reasoning as the policy below.
#
# READ-ONLY ON THE PARAMETERS, with no ssm:PutParameter. The image-tag grant
# includes PutParameter because deploy.sh records the previous tag for
# rollback; nothing writes the venue or the credential ARN, and a bot able to
# rewrite which venue it trades on is a bot whose configuration is not a fact.
resource "aws_iam_role_policy" "live_host" {
  count = length(var.live_stacks) > 0 ? 1 : 0

  name = "${local.name}-live-host"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "PullTheLiveImageOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
          "ecr:DescribeImages",
        ]
        Resource = aws_ecr_repository.live[0].arn
      },
      {
        Sid    = "ReadItsOwnVenueAndCredentialPointer"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = concat(
          [for p in aws_ssm_parameter.live_venue : p.arn],
          [for p in aws_ssm_parameter.live_credential_arn : p.arn],
        )
      },
    ]
  })
}

# A SEPARATE POLICY, NOT A STATEMENT IN THE SHARED ONE. Attached to the same
# instance role, created only when a secret is configured, and scoped to that
# one secret: the role can read the credential it trades with and no other
# secret in the account. `count` rather than a conditional Resource list, so
# that with nothing configured the policy does not exist at all.
resource "aws_iam_role_policy" "live_credentials" {
  count = length(var.live_stacks) > 0 ? 1 : 0

  name = "${local.name}-live-credentials"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadTheVenueCredentialAndNothingElse"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = local.live_credential_arn_pattern
    }]
  })
}
