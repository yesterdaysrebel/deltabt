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
        ]
        Resource = aws_ecr_repository.bot.arn
      },
      {
        Sid      = "ReadTheDatabasePasswordAndNothingElse"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_db_instance.main.master_user_secret[0].secret_arn
      },
      {
        Sid    = "ReadWhichImageTagToRun"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:PutParameter"]
        Resource = [
          aws_ssm_parameter.image_tag.arn,
          aws_ssm_parameter.image_tag_previous.arn,
        ]
      },
      {
        Sid    = "WriteItsOwnLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = "${aws_cloudwatch_log_group.bot.arn}:*"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
}
