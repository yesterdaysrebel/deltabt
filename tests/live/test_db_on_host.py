"""Postgres on the bot host instead of RDS (infra/terraform/db_host.tf, 2026-10-05).

The pieces live in five files that must agree on one address and one set of
rules. These tests pin the agreements; a real-state `tofu plan` and the switch-
over runbook (docs/db_on_host.md) are the acceptance test, as for every infra
change here (infra tests grep text, not wiring).
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
TF = (ROOT / "infra/terraform/db_host.tf").read_text()
EC2 = (ROOT / "infra/terraform/ec2.tf").read_text()
RUN = (ROOT / "deploy/aws/run_live.sh").read_text()
MKDB = (ROOT / "scripts/create_stack_database.sh").read_text()
MIG = (ROOT / "deploy/aws/migrate_rds_to_host.sh").read_text()
ADDR = "172.17.0.1"


def test_one_address_everywhere():
    """run_live.sh recognises 'local' by this address; every other piece must use it."""
    assert f'pg_local_address = "{ADDR}"' in TF
    assert f"PG_LOCAL={ADDR}" in RUN
    assert "local.pg_local_address" in EC2, "the live host must be given the local address"
    assert f'[ "$DB_HOST" = "{ADDR}" ]' in MKDB
    assert f'[ "$DB_HOST" = "{ADDR}" ]' in EC2, "the experiment document's db_env must know it too"
    assert f'[ "$DB_HOST" = "{ADDR}" ]' in MIG


def test_postgres_is_published_on_the_bridge_only():
    run = re.search(r"docker run -d --name deltabt-pg.*?postgres:16", RUN, re.S).group(0)
    assert "-p $PG_LOCAL:5432:5432" in run, "never on 0.0.0.0: the bridge address only"


def test_the_container_cannot_start_before_its_volume_is_mounted():
    """A docker restart policy would start Postgres at boot, before run_live.sh
    mounts /dev/sdf, and initdb an EMPTY database on the root disk."""
    run = re.search(r"docker run -d --name deltabt-pg.*?postgres:16", RUN, re.S).group(0)
    assert "--restart" not in run
    body = RUN[RUN.index("ensure_pg() {"):RUN.index("case \"${1:-}\" in")]
    assert body.index("mountpoint -q") < body.index("docker start deltabt-pg")


def test_the_data_lives_on_a_volume_that_outlives_the_host():
    vol = TF[TF.index('resource "aws_ebs_volume" "pgdata"'):TF.index('resource "aws_volume_attachment"')]
    assert "prevent_destroy = true" in vol and "encrypted         = true" in vol
    att = TF[TF.index('resource "aws_volume_attachment"'):TF.index('resource "aws_secretsmanager_secret"')]
    assert 'device_name = "/dev/sdf"' in att and "/dev/sdf" in RUN
    assert "stop_instance_before_detaching = true" in att, "detach only after Postgres has stopped"
    assert "PGDATA=/var/lib/postgresql/data/data" in RUN, "a subdirectory: ext4's lost+found breaks initdb"


def test_the_bot_uses_a_password_without_tls_on_the_host_and_iam_elsewhere():
    assert 'SSLM=require; [ "$DB_HOST" = "$PG_LOCAL" ] && SSLM=disable' in RUN
    assert "sslmode=$SSLM" in RUN
    assert "[ \"$DB_HOST\" = \"$PG_LOCAL\" ] && printf 'DB_IAM_AUTH=0\\n'" in RUN, (
        "the live image sets DB_IAM_AUTH=1; against a plain Postgres it must be off")
    assert "iam='DB_IAM_AUTH=0'" in EC2 and "sslm=disable" in EC2


def test_the_secret_is_never_regenerated_over_existing_data():
    body = RUN[RUN.index("ensure_pg() {"):RUN.index("case \"${1:-}\" in")]
    fill = body[body.index("sm get-secret-value >/dev/null 2>&1 ||"):body.index("docker start deltabt-pg")]
    assert fill.index("[ -d $d/data ]") < fill.index("put-secret-value"), (
        "if the volume holds data the password is in it; writing a new secret would lock the bot out")


def test_rds_iam_is_granted_only_where_it_exists():
    grant = MKDB[MKDB.index("rds_iam exists only on RDS"):]
    assert grant.index("rolname = 'rds_iam'") < grant.index("GRANT rds_iam")


def test_the_copy_never_overwrites_and_checks_every_table():
    assert "refusing to overwrite" in MIG
    assert "--no-owner --no-privileges" in MIG
    assert "MISMATCH" in MIG and "do NOT remove RDS" in MIG
    assert "pg_dump -h \"$RDS_HOST\"" in MIG and "pg_restore" in MIG
    assert "DROP " not in MIG.upper().replace("--NO-OWNER", ""), "the copy never drops anything"


def test_rds_is_not_removed_by_this_change():
    rds = (ROOT / "infra/terraform/rds.tf").read_text()
    assert 'resource "aws_db_instance" "main"' in rds and "prevent_destroy = true" in rds


def test_paper_stacks_keep_rds():
    assert "{ for k, s in local.stacks : k => s if s.live }" in TF


def test_the_daily_snapshot_is_tagged_as_the_delete_permission_requires():
    """The host may delete only snapshots tagged Role=pgdata-backup; the ones it
    takes must carry exactly that tag, and the report looks them up by it."""
    assert '"aws:ResourceTag/Role" = "pgdata-backup"' in TF
    assert "Key=Role,Value=pgdata-backup" in RUN and "Key=Stack,Value=${P##*/}" in RUN
    rep = (ROOT / "scripts/brief_report.py").read_text()
    assert '"Name=tag:Role,Values=pgdata-backup"' in rep and 'f"Name=tag:Stack,Values={args.stack}"' in rep
    assert "--expect-db-snapshots" in (ROOT / ".github/workflows/monitor.yml").read_text()
    assert '"ec2:DescribeSnapshots"' in (ROOT / "infra/terraform/monitoring.tf").read_text()


def test_the_backup_runs_daily_and_checkpoints_first():
    assert "OnCalendar=*-*-* 18:30" in RUN and "Persistent=true" in RUN
    backup = RUN[RUN.index("--backup-db)"):]
    assert backup.index("CHECKPOINT") < backup.index("create-snapshot")
