# ---------------------------------------------------------------------------
# POSTGRES ON THE BOT HOST INSTEAD OF RDS (2026-10-05, owner: "work on option 1,
# we will decide on merge post paper prod run"). Full reasoning and the
# switch-over runbook: docs/db_on_host.md.
#
# WHY. The RDS instance is ~80% of the bill: db.t4g.small $1.01/day plus
# storage and backups, about $33 of a ~$41/month run rate (Cost Explorer,
# 2026-10-03/04). One bot writing a few thousand rows a day does not need a
# managed server. A Postgres container on the bot host costs only its disk.
#
# WHAT CHANGES FOR A LIVE STACK when db_location = "host":
#   * the bot host runs `postgres:16` as container `deltabt-pg`, published on
#     the docker bridge address only (172.17.0.1:5432), never on the VPC;
#   * its data lives on a SEPARATE encrypted EBS volume (below), attached as
#     /dev/sdf, so a host replacement keeps the data. prevent_destroy guards it;
#   * the login is a password in a Secrets Manager secret (below) that the host
#     fills on first start; no IAM tokens (DB_IAM_AUTH=0), no TLS on the bridge;
#   * a daily EBS snapshot of that volume, taken by the host, kept
#     db_snapshot_retention_days (no S3 bucket: the CI deploy role may not
#     create buckets, and widening it means a manual bootstrap apply).
# deploy/aws/run_live.sh does the host side (ensure_pg); user_data is NOT
# changed, because the shared template has 9 bytes of headroom.
#
# WHAT DOES NOT CHANGE: the RDS instance stays (prevent_destroy) until its data
# has been copied and checked -- removing it is a separate, later change
# (docs/db_on_host.md, phase B). Paper stacks keep RDS whatever this says.
# ---------------------------------------------------------------------------

variable "db_location" {
  description = <<-EOT
    Where a LIVE stack's Postgres runs: "rds" (the shared aws_db_instance) or
    "host" (a container on the bot host, data on its own EBS volume). Changing
    it changes user_data, so it REPLACES the live hosts and starts new
    experiments. See docs/db_on_host.md before flipping it.
  EOT
  type        = string
  default     = "host"
  validation {
    condition     = contains(["rds", "host"], var.db_location)
    error_message = "db_location must be \"rds\" or \"host\"."
  }
}

variable "pg_volume_gb" {
  description = "Size of each live stack's Postgres data volume (gp3). The dry run's database is well under 1 GB."
  type        = number
  default     = 10
}

variable "db_snapshot_retention_days" {
  description = "Days the host keeps its daily snapshots of the Postgres volume."
  type        = number
  default     = 14
}

locals {
  db_on_host = var.db_location == "host"
  #: Live stacks whose Postgres runs on the host. Empty when db_location = "rds".
  pg_stacks = local.db_on_host ? { for k, s in local.stacks : k => s if s.live } : {}
  #: The docker bridge address deploy/aws/run_live.sh recognises as "local".
  pg_local_address = "172.17.0.1"
}

# --- the data volume: outlives any host ------------------------------------

resource "aws_ebs_volume" "pgdata" {
  for_each = local.pg_stacks

  availability_zone = aws_subnet.public[var.bot_subnet_index].availability_zone
  size              = var.pg_volume_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${local.name}-${each.key}-pgdata", Stack = each.key, Role = "pgdata" }

  lifecycle {
    # This volume IS the experiment record once RDS is gone. Removing it must
    # be a deliberate edit of this line, not a side effect of a plan.
    prevent_destroy = true
  }
}

resource "aws_volume_attachment" "pgdata" {
  for_each = local.pg_stacks

  device_name = "/dev/sdf" # Amazon Linux's udev rules name the NVMe device /dev/sdf too
  volume_id   = aws_ebs_volume.pgdata[each.key].id
  instance_id = aws_instance.bot[each.key].id
  # A host replacement detaches the volume first. Stopping the instance makes
  # systemd stop docker, which shuts Postgres down cleanly, before the detach.
  stop_instance_before_detaching = true
}

# --- the login: a secret the host fills on first start ----------------------
# No random-password provider is added for this: the host writes the password
# the first time it initialises the volume (deploy/aws/run_live.sh, ensure_pg)
# and refuses to start if the volume holds data while the secret is empty.

resource "aws_secretsmanager_secret" "pg" {
  for_each = local.pg_stacks

  name                    = "${local.name}/pg/${each.key}"
  description             = "Login for the Postgres container on the ${each.key} bot host. Filled by the host."
  recovery_window_in_days = 7
}

# --- daily snapshots: where the host finds its volume -------------------------
# The host snapshots its own data volume once a day (deploy/aws/run_live.sh,
# --backup-db) and deletes its snapshots older than the retention. A snapshot
# of a running Postgres's single volume is crash-consistent, which Postgres
# recovers from on start; the host runs CHECKPOINT first to keep that short.

resource "aws_ssm_parameter" "pgdata_volume" {
  for_each = local.pg_stacks

  name  = "${each.value.ssm_prefix}/pgdata_volume"
  type  = "String"
  value = jsonencode({ volume = aws_ebs_volume.pgdata[each.key].id, keep_days = var.db_snapshot_retention_days })
}

# --- what the host may do, and nothing more ---------------------------------

resource "aws_iam_role_policy" "pg_host" {
  count = length(local.pg_stacks) > 0 ? 1 : 0

  name = "${local.name}-pg-host"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadAndFirstFillTheLocalPostgresLogin"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
        Resource = [for s in aws_secretsmanager_secret.pg : s.arn]
      },
      {
        Sid      = "SnapshotItsOwnDataVolume"
        Effect   = "Allow"
        Action   = ["ec2:CreateSnapshot"]
        Resource = concat([for v in aws_ebs_volume.pgdata : v.arn], ["arn:aws:ec2:${var.aws_region}::snapshot/*"])
      },
      {
        Sid       = "TagTheSnapshotItTakes"
        Effect    = "Allow"
        Action    = ["ec2:CreateTags"]
        Resource  = "arn:aws:ec2:${var.aws_region}::snapshot/*"
        Condition = { StringEquals = { "ec2:CreateAction" = "CreateSnapshot" } }
      },
      {
        Sid      = "ListSnapshots"
        Effect   = "Allow"
        Action   = ["ec2:DescribeSnapshots"]
        Resource = "*"
      },
      {
        Sid       = "DeleteOnlyItsOwnBackupSnapshots"
        Effect    = "Allow"
        Action    = ["ec2:DeleteSnapshot"]
        Resource  = "arn:aws:ec2:${var.aws_region}::snapshot/*"
        Condition = { StringEquals = { "aws:ResourceTag/Role" = "pgdata-backup" } }
      },
      {
        Sid      = "ReadWhichVolumeToSnapshot"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [for p in aws_ssm_parameter.pgdata_volume : p.arn]
      },
    ]
  })
}
