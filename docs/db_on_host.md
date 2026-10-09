# Postgres on the bot host instead of RDS

Written 2026-10-05. Owner: "work on option 1, we will decide on merge post paper prod run". This
change is built and tested but NOT merged; merge only after the dry run's read (100 closed trades or
2026-10-24).

## Why

Cost Explorer, 2026-10-03/04, with one bot running: **$1.36/day, about $41/month before GST**.

| item | per day | per month |
|---|---|---|
| RDS db.t4g.small | $1.01 | $30.2 |
| RDS storage + backups | $0.10 | $3.1 |
| public IPv4 (the EIP) | $0.12 | $3.6 |
| bot root disk | $0.06 | $1.8 |
| secrets (3) | $0.04 | $1.2 |
| ECR storage | $0.03 | $0.8 |
| EC2 t4g.small | $0.00 | $0.0 (billed at $0 today; ~$12/month if that ends) |

RDS is ~80% of the bill. It doubled on 2026-10-02 when it moved from db.t4g.micro to db.t4g.small
for IAM token login. One bot writing a few thousand rows a day does not need a managed server.

**After phase B (RDS removed): about $8/month before GST** — EIP, two small disks, secrets, ECR,
plus a few cents of snapshots.

## How it works (`infra/terraform/db_host.tf`, `deploy/aws/run_live.sh`)

- `db_location = "host"` (default in this change) applies to LIVE stacks. Paper stacks keep RDS.
- The bot host runs `postgres:16` as the container `deltabt-pg`, published on the docker bridge
  address **172.17.0.1:5432 only** — never on the VPC; the security groups are unchanged.
- Its data is on a **separate encrypted 10 GB gp3 EBS volume** attached as `/dev/sdf`, mounted at
  `/var/lib/deltabt-pg`, `PGDATA=/var/lib/deltabt-pg/data`. The volume has `prevent_destroy`; a
  host replacement stops the instance before detaching (`stop_instance_before_detaching`), so
  Postgres shuts down cleanly and the new host mounts the same data.
- The container has **no docker restart policy** on purpose: at boot it would start before the
  volume is mounted and initialise an empty database on the root disk. `run_live.sh` (the bot's
  systemd `ExecStart`) mounts, then starts Postgres, on every start.
- Login: user `deltabt` with a password in the secret `deltabt-paper/pg/<stack>`. The host writes the
  password the first time it initialises an empty volume, and refuses to start if the volume holds
  data while the secret is empty. No IAM tokens (`DB_IAM_AUTH=0`) and no TLS on the bridge.
- **Backups:** a systemd timer runs `run.sh --backup-db` daily at 18:30 UTC (midnight IST): `CHECKPOINT`,
  then an EBS snapshot tagged `Role=pgdata-backup, Stack=<stack>`, and deletes its snapshots older than
  14 days. A single-volume snapshot of a running Postgres is crash-consistent, which Postgres recovers
  from. No S3 bucket: the CI deploy role may not create buckets, and widening it is a manual bootstrap
  apply.
- **The daily report** checks the newest snapshot and raises an alarm if it is older than 30 hours,
  or if none exists (`--expect-db-snapshots` in `monitor.yml`).
- `user_data` (the shared template) is unchanged: it has 9 bytes of headroom. The live host's rendered
  user_data is 16,189 of 16,384 bytes with this change.

Checked locally with throwaway Postgres 16 containers (2026-10-05): the bot's schema and sample rows on
a stand-in RDS; the copy below restored into a host-style container (with ext4's `lost+found` in the
volume root); row counts equal table by table; the bot connected with `sslmode=disable`,
`DB_IAM_AUTH=0` and ran `migrate()` on the copied data; data survived a restart and a new container
on the same volume.

**Not checked before a real apply:** that Amazon Linux names the attached NVMe volume `/dev/sdf`
(its udev rules do; `ensure_pg` waits 2 minutes for it and refuses to start without it), and the first
snapshot. Both are in the verification step below.

## Switch-over (after the dry run's read)

The merge replaces the dry-run host and starts a new experiment. Both are expected.

1. **Merge the PR.** The push apply fails by design (a host replacement).
2. **Run `infrastructure`** by hand: `allow_replace` ticked, `replace_hosts` = `dryrun`; approve in
   `paper`. This creates the volume, replaces the host and, in its database step, starts Postgres on
   the new host and creates the empty `deltabt_dryrun` database.
3. **Copy the dry run's data from RDS** (keeps the record; read-only on RDS):

   ```bash
   cd infra/terraform
   RDS=$(tofu output -raw db_endpoint)
   SECRET=$(tofu output -raw db_secret_arn)
   ID=$(aws ec2 describe-instances --filters Name=tag:Stack,Values=dryrun Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].InstanceId' --output text)
   B64=$(base64 -w0 ../../deploy/aws/migrate_rds_to_host.sh)
   aws ssm send-command --instance-ids "$ID" --document-name AWS-RunShellScript \
     --parameters "commands=[\"echo $B64 | base64 -d > /tmp/migrate.sh\",\"bash /tmp/migrate.sh deltabt_dryrun $RDS $SECRET\"]"
   ```

   It refuses to overwrite a database that has tables, and fails if any table's row count differs.
4. **Run `deploy prod`**, "Roll ONE stack" = `dryrun`, tag box empty; approve in `prod-deploy`.
5. **Verify:** the bot logs `bound to experiment`; the report runs clean; the next morning's report
   says "Database backed up N hours ago"; `aws ec2 describe-snapshots --filters Name=tag:Stack,Values=dryrun`
   lists one.

## Phase B — remove RDS (a separate change, a few days after the switch)

Only after the copy matched and the host database has run for a few days with snapshots:

1. Set `db_deletion_protection = false` and apply.
2. Remove `prevent_destroy` from `aws_db_instance.main`, then remove the RDS resources, the RDS IAM
   statements (`ReadTheDatabasePasswordAndNothingElse`, `ConnectToPostgresAsTheAppRoleOnly`), the
   outputs, and make paper stacks host-mode too (they fall back to RDS today). Destroying the
   instance takes a final snapshot (`skip_final_snapshot = false`).
3. Delete the manual snapshot `deltabt-paper-pre-resize-20261002` and the old secret
   `deltabt-paper/live/venue-credentials` (owner's call).

## Restore from a snapshot

Create a volume from the snapshot in the same availability zone, stop the bot (`systemctl stop deltabt`,
`docker stop deltabt-pg`), detach the current volume, attach the restored one as `/dev/sdf`, start the
bot. Or set the snapshot as the source of a new `aws_ebs_volume.pgdata` in Terraform.

## Rolling back

Set `db_location = "rds"`. The host is replaced again and the bot uses RDS — as long as RDS still
exists (before phase B). Data written on the host meanwhile is not copied back automatically.
