# ---------------------------------------------------------------------------
# READ THIS BEFORE RUNNING ANYTHING.
#
# WHAT THIS STACK IS
#   One EC2 instance running one paper-trading bot, one RDS PostgreSQL holding
#   the experiment, an ECR repository holding the images, and the CloudWatch
#   plumbing to tell whether it is still alive. Roughly $30/month.
#
# WHAT IT DELIBERATELY IS NOT
#   No NAT gateway, no load balancer, no EKS, no autoscaling group, no
#   DynamoDB lock table, no SSH key pair, no bastion. Each was considered and
#   rejected: see docs/aws_deployment.md. A single-instance bot that must not
#   run twice concurrently gains nothing from an autoscaler except the risk of
#   running twice concurrently.
#
# WHAT IT CANNOT DO
#   Place an order. The bot has no exchange credentials, no signing code, and
#   no order-placement methods; the safety scan in the test workflow fails the
#   build if any appear. Nothing in this directory changes that -- there is no
#   secret, parameter, or IAM permission here that could turn paper into live.
#
# ORDER OF OPERATIONS
#   1. infra/terraform/bootstrap   (S3 state bucket, GitHub OIDC provider,
#                                   the deploy role). Runs with local state,
#                                   once, by a human.
#   2. infra/terraform             (this stack). Runs in CI against S3 state.
#   3. the deploy workflow         (build, push, roll the image).
#
# ---------------------------------------------------------------------------
# ON RESOURCES THAT ALREADY EXIST IN AWS BUT NOT IN STATE
#
# Terraform's default behaviour here is already the safe one, and this stack
# leans on it deliberately: every resource that can be named IS named
# (deltabt-paper-*), so a create against an existing resource FAILS with
# "already exists" rather than adopting or replacing it.
#
#   EntityAlreadyExists: Role with name deltabt-paper-instance already exists
#   BucketAlreadyOwnedByYou / DBInstanceAlreadyExists / RepositoryAlreadyExists
#
# When that happens, DO NOT delete the resource in the console and re-run.
# Import it:
#
#   terraform import aws_iam_role.instance         deltabt-paper-instance
#   terraform import aws_ecr_repository.bot        deltabt
#   terraform import aws_db_instance.main          deltabt-paper
#   terraform import aws_instance.bot              i-0123456789abcdef0
#   terraform import aws_cloudwatch_log_group.bot  /deltabt/paper/bot
#
# then re-plan and read the diff before applying. The CI plan is additionally
# gated by scripts/tf_guard.py, which fails the job if the plan would destroy
# or replace anything holding data or state.
# ---------------------------------------------------------------------------

# Deliberately empty of resources. Every resource lives in the file named for
# the service it belongs to.
