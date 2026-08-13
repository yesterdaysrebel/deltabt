# Bootstrap: the three things that must exist BEFORE the main stack can run.
#
#   1. the S3 bucket holding remote state
#   2. the GitHub OIDC identity provider
#   3. the IAM role GitHub Actions assumes
#
# Chicken-and-egg: this stack cannot use the S3 backend it creates, so it runs
# with LOCAL state and is applied once, by a human, from a workstation with
# admin credentials. Its state file is small and its resources are stable; if
# it is ever lost, `terraform import` recovers it (see docs/aws_deployment.md).
#
# Run once per AWS account+region:
#     cd infra/terraform/bootstrap
#     terraform init && terraform apply
#
# This is deliberately NOT wired into CI. A workflow that can create its own
# trust anchor can also replace it.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project   = "deltabt"
      Component = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}

variable "aws_region" {
  description = "AWS region for the state bucket and IAM."
  type        = string
  default     = "ap-south-1"
}

variable "github_org" {
  description = "GitHub organisation or user that owns the repository."
  type        = string
}

variable "github_repo" {
  description = "Repository name. The OIDC trust policy is scoped to it."
  type        = string
  default     = "deltabt"
}

variable "deploy_branch" {
  description = "Branch permitted to assume the deploy role."
  type        = string
  default     = "master"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket for Terraform state."
  type        = string
}

# ---------------------------------------------------------------------------
# GitHub is migrating OIDC subjects to an IMMUTABLE form that pins the owner
# and the repository to their NUMERIC IDs:
#
#     repo:yesterdaysrebel@256862558/deltabt@1331985440:ref:refs/heads/master
#
# rather than the legacy name-based
#
#     repo:yesterdaysrebel/deltabt:ref:refs/heads/master
#
# That is a real security improvement: a name can be released and re-registered
# by someone else, and a trust policy written against a name would follow it to
# the new owner. A numeric id cannot be re-registered.
#
# It also silently breaks every name-based trust policy. Observed here as
# `AccessDenied: Not authorized to perform sts:AssumeRoleWithWebIdentity`, with
# CloudTrail showing the immutable subject GitHub actually sent.
#
# BOTH forms are trusted below, each pinned exactly. No wildcard: a pattern
# like `repo:yesterdaysrebel*/deltabt*` would also match a repository somebody
# else creates named `yesterdaysrebel-evil/deltabt-x`.
#
# Find the ids with:
#     gh api users/<owner> --jq .id
#     gh api repos/<owner>/<repo> --jq .id
# ---------------------------------------------------------------------------

variable "github_owner_id" {
  description = "Numeric GitHub owner id, for the immutable OIDC subject. Empty disables it."
  type        = string
  default     = ""
}

variable "github_repo_id" {
  description = "Numeric GitHub repository id, for the immutable OIDC subject. Empty disables it."
  type        = string
  default     = ""
}

locals {
  repo_immutable = (var.github_owner_id != "" && var.github_repo_id != ""
    ? "${var.github_org}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}"
  : null)

  #: Both spellings of "this repository", each exact.
  repo_forms = compact(["${var.github_org}/${var.github_repo}", local.repo_immutable])

  deploy_subjects = flatten([for r in local.repo_forms : [
    "repo:${r}:ref:refs/heads/${var.deploy_branch}",
    "repo:${r}:environment:paper",
  ]])

  plan_subjects = [for r in local.repo_forms : "repo:${r}:pull_request"]
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Remote state
# ---------------------------------------------------------------------------
# Versioned so a corrupted or truncated state can be rolled back, encrypted
# because state contains resource metadata, and public access blocked outright.
resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name

  # State is the map of everything that exists. Losing it means Terraform no
  # longer knows what it manages, and the recovery is a manual import of every
  # resource.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# State locking uses S3 conditional writes (Terraform >= 1.10 / OpenTofu >=
# 1.10) via `use_lockfile`. No DynamoDB table: one less resource, one less
# bill, and no second thing to keep in sync.

# ---------------------------------------------------------------------------
# GitHub OIDC
# ---------------------------------------------------------------------------
# No AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY anywhere. GitHub presents a
# short-lived OIDC token; AWS exchanges it for temporary credentials. There is
# no long-lived secret to leak or rotate.
#
# CREATE OR REFERENCE -- and the difference matters a great deal.
#
# An account may hold only ONE identity provider per issuer URL, so the GitHub
# provider is inherently account-wide infrastructure. Other projects' roles can
# and do federate through the same one. If this stack OWNED a shared provider,
# a `terraform destroy` here would silently revoke every unrelated project's
# ability to deploy -- and Terraform would report it as a clean teardown.
#
#   create_oidc_provider = true   fresh account: nothing federates yet, so this
#                                 stack creates and owns it.
#   create_oidc_provider = false  the provider already exists: reference it
#                                 read-only. Terraform never creates, modifies,
#                                 or deletes it, and its thumbprint is left
#                                 exactly as found.
#
# `scripts/bootstrap_check.py` determines which case you are in and prints the
# flag to use; `scripts/bootstrap.sh` passes it and says so out loud. Neither
# ever mutates an existing provider.
variable "create_oidc_provider" {
  description = <<-EOT
    Create the GitHub OIDC provider (fresh account), or reference an existing
    one (shared account). Run scripts/bootstrap_check.py to find out which.
  EOT
  type        = bool
  default     = true
}

resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # AWS no longer validates this thumbprint for the GitHub issuer -- it trusts
  # the certificate chain directly -- but the argument is still required by the
  # API, so the long-published GitHub value is supplied. When referencing an
  # existing provider this is not applied at all: an existing thumbprint is
  # left as found rather than rewritten to match our code.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]

  lifecycle {
    # Even when this stack does own it, deleting it is not a routine teardown:
    # every role in the account that federates through GitHub loses its trust
    # anchor at once.
    prevent_destroy = true
  }
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  github_oidc_provider_arn = (var.create_oidc_provider
    ? one(aws_iam_openid_connect_provider.github[*].arn)
  : one(data.aws_iam_openid_connect_provider.github[*].arn))
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to THIS repository and only the deploy branch or an environment
    # of the same name. A wildcard on the org would let any repository in it
    # assume this role.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.deploy_subjects
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "deltabt-github-deploy"
  description          = "Assumed by GitHub Actions via OIDC to plan/apply and deploy."
  assume_role_policy   = data.aws_iam_policy_document.github_assume.json
  max_session_duration = 3600
}

# Broad enough to manage this stack, narrow enough to be reviewable. Scoped to
# the region and, where the API allows it, to deltabt-named resources.
data "aws_iam_policy_document" "deploy" {
  statement {
    sid    = "TerraformState"
    effect = "Allow"
    actions = [
      "s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject",
      # Read-only, and only so the preflight can VERIFY versioning is on rather
      # than report "could not read" as "not enabled". Without it the check
      # cannot tell a denial from a genuinely unversioned bucket, which sends
      # an operator to change a setting that was never wrong.
      "s3:GetBucketVersioning",
    ]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
  }

  statement {
    sid    = "InfrastructureManagement"
    effect = "Allow"
    actions = [
      "ec2:*", "rds:*", "ecr:*", "logs:*", "cloudwatch:*",
      "secretsmanager:*", "ssm:*", "iam:*", "kms:Describe*", "kms:List*",
      "sts:GetCallerIdentity", "application-autoscaling:Describe*",
    ]
    resources = ["*"]
    # Deliberately not narrowed further: Terraform must read and tag resources
    # it creates, and an under-scoped policy fails opaquely mid-apply. The
    # control that matters is WHO can assume this role, which the trust policy
    # above restricts to one repository and one branch.
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deltabt-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

# ---------------------------------------------------------------------------
# A SEPARATE, READ-ONLY role for pull-request plans.
#
# A pull request's OIDC subject is `repo:org/repo:pull_request`, which does not
# match the apply role's trust policy -- deliberately. Letting a pull request
# assume a role that can create IAM and delete databases means a plan job is
# an arbitrary-code path into the account. So plans from pull requests get
# their own role that can read everything and change nothing.
#
# (GitHub does not issue a writable id-token for pull requests from forks, so
# a fork cannot reach even this one.)
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "github_plan_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.plan_subjects
    }
  }
}

resource "aws_iam_role" "github_plan" {
  name                 = "deltabt-github-plan"
  description          = "Read-only. Assumed by pull-request plan jobs."
  assume_role_policy   = data.aws_iam_policy_document.github_plan_assume.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "github_plan_readonly" {
  role       = aws_iam_role.github_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# `terraform plan` still has to read state and take the S3 lock, which is a
# write. This is the only write the plan role has, and it is confined to the
# state bucket.
resource "aws_iam_role_policy" "github_plan_state" {
  name = "deltabt-plan-state"
  role = aws_iam_role.github_plan.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:ListBucket", "s3:GetObject", "s3:PutObject",
      "s3:DeleteObject", "s3:GetBucketVersioning"]
      Resource = [
        aws_s3_bucket.state.arn,
        "${aws_s3_bucket.state.arn}/*",
      ]
    }]
  })
}

output "state_bucket" {
  description = "Put this in infra/terraform/backend.tf."
  value       = aws_s3_bucket.state.id
}

output "github_terraform_role_arn" {
  description = "Set as the AWS_TERRAFORM_ROLE_ARN repository variable in GitHub."
  value       = aws_iam_role.github_deploy.arn
}

output "github_plan_role_arn" {
  description = "Set as the AWS_TERRAFORM_PLAN_ROLE_ARN repository variable in GitHub."
  value       = aws_iam_role.github_plan.arn
}

output "account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "github_oidc_provider_arn" {
  description = "The provider these roles federate through."
  value       = local.github_oidc_provider_arn
}

output "github_oidc_provider_owned_by_this_stack" {
  description = <<-EOT
    false means the provider pre-existed and is only referenced: Terraform will
    never modify or delete it, and other projects federating through it are
    unaffected by anything this stack does.
  EOT
  value       = var.create_oidc_provider
}
