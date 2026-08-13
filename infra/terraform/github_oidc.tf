# GitHub OIDC, application side.
#
# The identity PROVIDER and the broad Terraform role live in
# infra/terraform/bootstrap, because they must exist before this stack can run
# at all. What lives here is the narrow role the IMAGE deploy uses -- and the
# separation is the point: the workflow that ships a container cannot change
# infrastructure, and the workflow that changes infrastructure does not need
# permission to touch the running bot.
#
# Neither role has a password, an access key, or any long-lived credential.
# GitHub presents a short-lived OIDC token and AWS exchanges it for a session
# that expires within the hour.

# ---------------------------------------------------------------------------
# The deploy role assumed by GitHub Actions.
#
# The OIDC provider and a broad bootstrap role live in infra/terraform/bootstrap
# because they must exist before this stack can run at all. This role is the
# NARROW one used by the image-deploy workflow: push an image, tell the
# instance to restart. It cannot change infrastructure.
# ---------------------------------------------------------------------------

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "github_app_deploy" {
  name        = "${local.name}-github-app-deploy"
  description = "Push images and restart the bot. Cannot modify infrastructure."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        # Scoped to this repository AND either the deploy branch or the
        # protected `paper` environment. Without the `sub` condition ANY GitHub
        # repository in the world could assume the role; with a trailing `:*`
        # any branch or pull request in this one could.
        #
        # Both the legacy name-based subject and GitHub's IMMUTABLE
        # numeric-id subject are listed, each pinned exactly -- see the long
        # note in infra/terraform/bootstrap/main.tf. A name-only policy is
        # denied outright on repositories that have moved to immutable
        # subjects.
        "ForAnyValue:StringLike" = {
          "token.actions.githubusercontent.com:sub" = local.github_subjects
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_app_deploy" {
  name = "${local.name}-github-app-deploy"
  role = aws_iam_role.github_app_deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*"
      },
      {
        Sid    = "PushImages"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:DescribeImages",
        ]
        Resource = aws_ecr_repository.bot.arn
      },
      {
        Sid    = "RunTheDeployDocumentOnTheBotHostOnly"
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          aws_ssm_document.deploy.arn,
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.bot.id}",
        ]
      },
      {
        Sid      = "ReadBackTheResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = "*" # these actions are not resource-scopable
      },
    ]
  })
}

locals {
  #: Both spellings of "this repository", each exact. Empty ids fall back to
  #: the legacy form alone.
  repo_immutable = (var.github_owner_id != "" && var.github_repo_id != ""
    ? "${var.github_org}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}"
  : null)

  repo_forms = compact(["${var.github_org}/${var.github_repo}", local.repo_immutable])

  github_subjects = flatten([for r in local.repo_forms : [
    "repo:${r}:ref:refs/heads/master",
    "repo:${r}:environment:paper",
  ]])
}
