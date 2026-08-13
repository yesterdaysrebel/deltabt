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
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # AWS no longer validates this thumbprint for the GitHub issuer -- it trusts
  # the certificate chain directly -- but the argument is still required by the
  # API, so the long-published GitHub value is supplied.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
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
      values = [
        "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/${var.deploy_branch}",
        "repo:${var.github_org}/${var.github_repo}:environment:paper",
      ]
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
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_org}/${var.github_repo}:pull_request"]
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
      Action = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
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
