# ---------------------------------------------------------------------------
# The instance role.
#
# Note what is NOT here: no exchange credentials, and no permission that could
# obtain any. The bot reads only public market data. The only secret it can
# read is the database password, and it can read exactly that one ARN.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "instance" {
  name = "${local.name}-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Gives Session Manager. This is what replaces SSH: no key pair, no bastion,
# no inbound port, and every session is logged in CloudTrail.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_role_policy" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = "ecr:GetAuthorizationToken"
        Resource = "*" # this action does not accept a resource restriction
      },
      {
        Sid    = "EcrPullThisRepositoryOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
          # deploy.sh refuses to touch the running service until it has
          # confirmed the requested tag is actually in ECR -- a deploy that
          # half-succeeds is worse than one that never starts. That check is a
          # metadata read on a repository this host can already pull from, so
          # it grants no capability the three actions above do not.
          "ecr:DescribeImages",
        ]
        Resource = aws_ecr_repository.bot.arn
      },
      {
        # STILL NEEDED, but only for BOOTSTRAP: create_stack_database.py uses
        # the master user once, to create the database and the IAM-auth role.
        # The bot itself no longer reads this -- see the statement below.
        Sid      = "ReadTheDatabasePasswordAndNothingElse"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_db_instance.main.master_user_secret[0].secret_arn
      },
      {
        # HOW THE BOT ACTUALLY AUTHENTICATES NOW.
        #
        # An IAM token is minted locally per connection and lasts ~15 minutes,
        # so there is no stored password to go stale when RDS rotates the
        # master credential on its own schedule. That rotation used to leave
        # the cached DSN wrong in a way that broke nothing until the NEXT new
        # connection -- possibly days later, looking like an unrelated outage.
        #
        # The resource id is the DBI RESOURCE id (db-XXXX), not the identifier,
        # and it is scoped to ONE database user. A token for `deltabt_app`
        # cannot be used to connect as the master user.
        Sid      = "ConnectToPostgresAsTheAppRoleOnly"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:aws:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${aws_db_instance.main.resource_id}/${var.db_app_username}"
      },
      {
        Sid    = "ReadWhichImageTagToRun"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:PutParameter"]
        Resource = concat(
          [for p in aws_ssm_parameter.image_tag : p.arn],
          [for p in aws_ssm_parameter.image_tag_previous : p.arn],
        )
      },
      {
        Sid    = "WriteItsOwnLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = [for g in aws_cloudwatch_log_group.bot : "${g.arn}:*"]
      },
    ]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
}
