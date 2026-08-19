#!/usr/bin/env python3
"""Fail-closed preflight for the AWS deployment.

Mirrors what `app/forwardtest/preflight.py` does for the experiment: assert
every precondition before anything happens, and treat "could not determine" as
FAILURE rather than as absence of a problem. A check that silently degrades to
"skipped" when the API call errors is worse than no check, because it reports
green.

    --phase infrastructure   trust anchors and the backend must exist; the
                             application stack may legitimately not exist yet
    --phase application      everything must exist and the bot must be
                             deployable: exactly one instance, private
                             database, zero public ingress, SSM reachable

ON "MISSING MEANS SAFE TO CREATE"
    It does not, and this script never assumes it. A resource that is absent
    is a FAILURE unless a Terraform plan is supplied with --plan-json AND that
    plan actually creates it. Absence justified by a plan is reported as
    PLANNED; absence with no plan is reported as MISSING and fails.

Usage:
    python scripts/aws_preflight.py --phase application \
        --account 123456789012 --region ap-south-1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field

PASS, FAIL, PLANNED = "PASS", "FAIL", "PLANNED"

#: How old an EC2 instance must be before an empty metric series counts as a
#: misconfigured alarm rather than one that has simply not had data yet.
MIN_METRIC_AGE = 900.0
OIDC_HOST = "token.actions.githubusercontent.com"


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (PASS, PLANNED)


@dataclass
class Context:
    region: str
    environment: str
    expected_account: str | None
    state_bucket: str | None
    ecr_repository: str
    phase: str = "application"
    plan: dict = field(default_factory=dict)
    account: str = ""

    @property
    def name(self) -> str:
        return f"deltabt-{self.environment}"

    def creates(self, resource_type: str) -> bool:
        """True when the supplied plan will CREATE a resource of this type."""
        for change in self.plan.get("resource_changes", []):
            if change.get("type") == resource_type and \
                    "create" in change.get("change", {}).get("actions", []):
                return True
        return False


def aws(ctx: Context, *args: str, global_service: bool = False) -> tuple[bool, dict]:
    """Returns (succeeded, parsed). A failure is a failure -- never silently {}."""
    cmd = ["aws", *args, "--output", "json"]
    if not global_service:
        cmd += ["--region", ctx.region]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except Exception as exc:                                  # noqa: BLE001
        return False, {"error": str(exc)}
    if proc.returncode != 0:
        return False, {"error": proc.stderr.strip().splitlines()[-1] if proc.stderr else "failed"}
    try:
        return True, json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        return False, {"error": f"unparseable response: {exc}"}


def absent(ctx: Context, resource_type: str, what: str, import_hint: str = "") -> Result:
    """Uniform handling of "it is not there".

    Absence is only acceptable when a plan proves Terraform is about to create
    it. Everything else fails, because "it will probably get made" is how a
    deploy proceeds against infrastructure nobody built.
    """
    if ctx.creates(resource_type):
        return Result(what, PLANNED, f"absent; the supplied plan creates {resource_type}")
    hint = f" {import_hint}" if import_hint else ""
    return Result(what, FAIL, f"does not exist and no supplied plan creates it.{hint}")


# ---------------------------------------------------------------------------
# Identity and region
# ---------------------------------------------------------------------------

def check_credentials(ctx: Context) -> Result:
    ok, ident = aws(ctx, "sts", "get-caller-identity", global_service=True)
    if not ok:
        return Result("aws_credentials", FAIL,
                      f"no usable credentials: {ident.get('error')}")
    ctx.account = ident["Account"]
    return Result("aws_credentials", PASS, ident["Arn"])


def check_account(ctx: Context) -> Result:
    if not ctx.account:
        return Result("expected_account", FAIL, "identity unknown")
    if ctx.expected_account and ctx.account != ctx.expected_account:
        return Result("expected_account", FAIL,
                      f"connected to {ctx.account}, expected {ctx.expected_account}. "
                      f"Deploying into the wrong account is the easiest mistake here.")
    if not ctx.expected_account:
        return Result("expected_account", FAIL,
                      f"connected to {ctx.account} but --account was not given. "
                      f"Pin it: an unpinned deploy has no way to notice it is in "
                      f"the wrong place.")
    return Result("expected_account", PASS, ctx.account)


def check_region(ctx: Context) -> Result:
    ok, data = aws(ctx, "ec2", "describe-availability-zones")
    if not ok:
        return Result("expected_region", FAIL,
                      f"region {ctx.region} not usable: {data.get('error')}")
    zones = [z["ZoneName"] for z in data.get("AvailabilityZones", [])]
    if len(zones) < 2:
        return Result("expected_region", FAIL,
                      f"{ctx.region} exposes {len(zones)} AZ(s); RDS needs a subnet "
                      f"group spanning two")
    return Result("expected_region", PASS, f"{ctx.region} ({len(zones)} AZs)")


# ---------------------------------------------------------------------------
# Trust anchors
# ---------------------------------------------------------------------------

def check_oidc_provider(ctx: Context) -> Result:
    arn = f"arn:aws:iam::{ctx.account}:oidc-provider/{OIDC_HOST}"
    ok, data = aws(ctx, "iam", "get-open-id-connect-provider",
                   "--open-id-connect-provider-arn", arn, global_service=True)
    if not ok:
        return absent(ctx, "aws_iam_openid_connect_provider", "github_oidc_trust",
                      "Run ./scripts/bootstrap.sh.")
    if "sts.amazonaws.com" not in data.get("ClientIDList", []):
        return Result("github_oidc_trust", FAIL,
                      "provider exists but does not accept the sts.amazonaws.com "
                      "audience, so no workflow can assume a role through it")
    return Result("github_oidc_trust", PASS, arn)


def check_iam_roles(ctx: Context) -> Result:
    # The BOOTSTRAP roles must exist in every phase -- nothing can run without
    # them. The MAIN-STACK roles are created by the very apply this preflight
    # gates, so demanding them in the infrastructure phase makes a first apply
    # impossible: the check would only pass once the thing it gates had already
    # happened.
    required = {
        "deltabt-github-deploy": "Terraform infrastructure role",
        "deltabt-github-plan": "pull-request read-only plan role",
    }
    if ctx.phase == "application":
        required.update({
            f"{ctx.name}-instance": "EC2 instance role",
            f"{ctx.name}-github-app-deploy": "application deploy role",
        })
    missing = []
    for role, description in required.items():
        ok, _ = aws(ctx, "iam", "get-role", "--role-name", role, global_service=True)
        if not ok:
            missing.append(f"{role} ({description})")
    if missing:
        if ctx.creates("aws_iam_role"):
            return Result("iam_roles", PLANNED, f"missing but planned: {', '.join(missing)}")
        return Result("iam_roles", FAIL, f"missing: {', '.join(missing)}")
    return Result("iam_roles", PASS, f"all {len(required)} roles present")


def check_role_trust_is_scoped(ctx: Context) -> Result:
    """A role trusting `repo:*` is trusted by every repository on GitHub."""
    roles = ["deltabt-github-deploy", "deltabt-github-plan"]
    if ctx.phase == "application":
        roles.append(f"{ctx.name}-github-app-deploy")
    for role in roles:
        ok, data = aws(ctx, "iam", "get-role", "--role-name", role, global_service=True)
        if not ok:
            if ctx.creates("aws_iam_role"):
                continue
            return Result("oidc_trust_scoped", FAIL, f"cannot read {role}")
        doc = json.dumps(data["Role"]["AssumeRolePolicyDocument"])
        if OIDC_HOST not in doc:
            continue
        if ":sub" not in doc:
            return Result("oidc_trust_scoped", FAIL,
                          f"{role} trusts the GitHub issuer with no `sub` condition. "
                          f"ANY repository on GitHub can assume it.")
        if '"repo:*"' in doc or '"*"' in doc:
            return Result("oidc_trust_scoped", FAIL,
                          f"{role} has a wildcard subject condition")
    return Result("oidc_trust_scoped", PASS, "subjects pinned to this repository")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def check_backend_bucket(ctx: Context) -> Result:
    if not ctx.state_bucket:
        return Result("terraform_backend", FAIL,
                      "--state-bucket not given; cannot verify the backend exists")
    ok, _ = aws(ctx, "s3api", "head-bucket", "--bucket", ctx.state_bucket,
                global_service=True)
    if not ok:
        return absent(ctx, "aws_s3_bucket", "terraform_backend",
                      "Run ./scripts/bootstrap.sh. Do NOT point the backend "
                      "somewhere else -- a second state backend is how one stack "
                      "becomes two.")
    ok, ver = aws(ctx, "s3api", "get-bucket-versioning", "--bucket", ctx.state_bucket,
                  global_service=True)
    if not ok:
        # "Could not read" and "is disabled" are different problems with
        # different fixes, and reporting the second when it was the first sends
        # someone to change a setting that was never wrong.
        return Result("terraform_backend", FAIL,
                      f"bucket exists but its versioning setting could not be "
                      f"read ({ver.get('error')}). The caller most likely lacks "
                      f"s3:GetBucketVersioning -- that is a permissions fix, not "
                      f"a bucket fix.")
    if ver.get("Status") != "Enabled":
        return Result("terraform_backend", FAIL,
                      "bucket exists and versioning is genuinely NOT enabled; a "
                      "truncated state write would be unrecoverable")
    return Result("terraform_backend", PASS, f"s3://{ctx.state_bucket} (versioned)")


def check_state_readable(ctx: Context) -> Result:
    if not ctx.state_bucket:
        return Result("terraform_state_accessible", FAIL, "--state-bucket not given")
    key = f"deltabt/{ctx.environment}/terraform.tfstate"
    ok, data = aws(ctx, "s3api", "head-object", "--bucket", ctx.state_bucket,
                   "--key", key, global_service=True)
    if not ok:
        # Before the first apply there IS no state object, and the apply that
        # would create it is the one being gated. What matters here is that the
        # bucket is reachable, which check_backend_bucket already established.
        if ctx.plan or ctx.phase == "infrastructure":
            return Result("terraform_state_accessible", PLANNED,
                          f"{key} not written yet -- first apply")
        return Result("terraform_state_accessible", FAIL,
                      f"cannot read s3://{ctx.state_bucket}/{key}")
    return Result("terraform_state_accessible", PASS,
                  f"{key} ({data.get('ContentLength', 0)} bytes)")


# ---------------------------------------------------------------------------
# Application infrastructure
# ---------------------------------------------------------------------------

def check_ecr(ctx: Context) -> Result:
    ok, data = aws(ctx, "ecr", "describe-repositories",
                   "--repository-names", ctx.ecr_repository)
    if not ok:
        return absent(ctx, "aws_ecr_repository", "ecr_repository")
    repo = data["repositories"][0]
    if repo.get("imageTagMutability") != "IMMUTABLE":
        return Result("ecr_repository", FAIL,
                      "tags are MUTABLE. An overwritable tag makes 'which code "
                      "produced this dataset' unanswerable.")
    return Result("ecr_repository", PASS, repo["repositoryUri"])


def check_rds(ctx: Context) -> Result:
    ok, data = aws(ctx, "rds", "describe-db-instances",
                   "--db-instance-identifier", ctx.name)
    if not ok:
        return absent(ctx, "aws_db_instance", "rds_private")
    db = data["DBInstances"][0]
    problems = []
    if db.get("PubliclyAccessible"):
        problems.append("PUBLICLY ACCESSIBLE")
    if not db.get("StorageEncrypted"):
        problems.append("storage not encrypted")
    if not db.get("BackupRetentionPeriod"):
        problems.append("automated backups disabled")
    if not db.get("DeletionProtection"):
        problems.append("deletion protection off")
    if problems:
        return Result("rds_private", FAIL, "; ".join(problems))
    return Result("rds_private", PASS,
                  f"{db['DBInstanceStatus']}, private, encrypted, "
                  f"{db['BackupRetentionPeriod']}-day backups")


def _bot_instances(ctx: Context) -> tuple[bool, list[dict]]:
    ok, data = aws(ctx, "ec2", "describe-instances", "--filters",
                   "Name=tag:Project,Values=deltabt",
                   f"Name=tag:Environment,Values={ctx.environment}",
                   "Name=instance-state-name,Values=pending,running")
    if not ok:
        return False, []
    return True, [i for r in data.get("Reservations", []) for i in r.get("Instances", [])]


def check_one_instance_per_stack(ctx: Context) -> Result:
    """ONE BOT PER STACK -- which is not the same as one bot in total.

    This check used to require exactly one instance anywhere, on the grounds
    that two bots would interleave one paper account. That reasoning is
    specific to a SHARED DATABASE: the advisory lock, the single-RUNNING-
    experiment index and the one-open-position-per-symbol index are all
    per-database, so two bots on two databases collide on none of them.

    Two bots on the SAME database is still exactly as fatal as it was, and an
    untagged instance is treated as the same stack as any other untagged one
    precisely so that a host which lost its tags cannot slip through as "a
    different stack".
    """
    ok, instances = _bot_instances(ctx)
    if not ok:
        return Result("one_instance_per_stack", FAIL, "could not enumerate instances")
    if not instances:
        return absent(ctx, "aws_instance", "one_instance_per_stack")

    by_stack: dict[str, list[str]] = {}
    for i in instances:
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        by_stack.setdefault(tags.get("Stack", "<untagged>"), []).append(i["InstanceId"])

    duplicated = {s: ids for s, ids in by_stack.items() if len(ids) > 1}
    if duplicated:
        detail = "; ".join(f"{s}: {', '.join(ids)}" for s, ids in duplicated.items())
        return Result("one_instance_per_stack", FAIL,
                      f"more than one running bot in the same stack ({detail}). "
                      f"Two bots against one database interleave a single paper "
                      f"account. The PostgreSQL advisory lock refuses the second, "
                      f"so the visible symptom is a crash loop -- resolve this by "
                      f"hand before deploying.")

    summary = ", ".join(f"{s}={ids[0]}" for s, ids in sorted(by_stack.items()))
    return Result("one_instance_per_stack", PASS,
                  f"{len(by_stack)} stack(s): {summary}")


def check_no_public_ingress(ctx: Context) -> Result:
    ok, data = aws(ctx, "ec2", "describe-security-groups", "--filters",
                   "Name=tag:Project,Values=deltabt",
                   f"Name=tag:Environment,Values={ctx.environment}")
    if not ok:
        return Result("zero_public_ingress", FAIL, "could not enumerate security groups")
    groups = data.get("SecurityGroups", [])
    if not groups:
        return absent(ctx, "aws_security_group", "zero_public_ingress")
    open_rules = []
    for group in groups:
        for rule in group.get("IpPermissions", []):
            for cidr in rule.get("IpRanges", []):
                if cidr.get("CidrIp") == "0.0.0.0/0":
                    open_rules.append(
                        f"{group['GroupName']} {rule.get('IpProtocol')} "
                        f"{rule.get('FromPort', 'all')}")
            for cidr in rule.get("Ipv6Ranges", []):
                if cidr.get("CidrIpv6") == "::/0":
                    open_rules.append(f"{group['GroupName']} IPv6 "
                                      f"{rule.get('FromPort', 'all')}")
    if open_rules:
        return Result("zero_public_ingress", FAIL,
                      f"open to the internet: {'; '.join(open_rules)}. Access is "
                      f"via SSM Session Manager, which needs no inbound rule.")
    return Result("zero_public_ingress", PASS,
                  f"{len(groups)} security group(s), no ingress from 0.0.0.0/0")


def check_ssm(ctx: Context) -> Result:
    ok, instances = _bot_instances(ctx)
    if not ok or not instances:
        return absent(ctx, "aws_instance", "ssm_available")
    ids = [i["InstanceId"] for i in instances]
    ok, data = aws(ctx, "ssm", "describe-instance-information", "--filters",
                   f"Key=InstanceIds,Values={','.join(ids)}")
    if not ok:
        return Result("ssm_available", FAIL, "could not query SSM")
    online = [i for i in data.get("InstanceInformationList", [])
              if i.get("PingStatus") == "Online"]
    if not online:
        return Result("ssm_available", FAIL,
                      "no instance is Online in SSM. Deployment goes through SSM "
                      "and there is no SSH fallback by design, so this blocks "
                      "every deploy and every operator session.")
    return Result("ssm_available", PASS,
                  f"{len(online)} instance(s) Online, agent "
                  f"{online[0].get('AgentVersion', '?')}")


def check_secret(ctx: Context) -> Result:
    ok, data = aws(ctx, "rds", "describe-db-instances",
                   "--db-instance-identifier", ctx.name)
    if not ok:
        return absent(ctx, "aws_db_instance", "database_secret")
    secret = data["DBInstances"][0].get("MasterUserSecret") or {}
    arn = secret.get("SecretArn")
    if not arn:
        return Result("database_secret", FAIL,
                      "RDS is not managing the master password in Secrets Manager. "
                      "The password would then live wherever it was typed.")
    if secret.get("SecretStatus") != "active":
        return Result("database_secret", FAIL,
                      f"secret status is {secret.get('SecretStatus')}")
    # Confirm it is readable as a secret, not merely referenced.
    ok, _ = aws(ctx, "secretsmanager", "describe-secret", "--secret-id", arn)
    if not ok:
        return Result("database_secret", FAIL, f"cannot describe {arn}")
    return Result("database_secret", PASS, arn)


def check_alarms(ctx: Context) -> Result:
    """PER-STACK ALARMS, DISCOVERED FROM THE RUNNING STACKS.

    The names were once unprefixed -- deltabt-paper-bot-silent -- from when a
    single bot was the whole deployment. Alarms became per-stack when a second
    concurrent experiment was added, and this check kept asking for the old
    names. It went unnoticed until v1 and v2 were decommissioned on
    2026-08-19: v1's alarms WERE the unprefixed ones, so destroying that stack
    took them with it and every apply began failing its own post-apply
    verification, naming three alarms that were correctly gone.

    So the stacks are derived from the instance tags, exactly as
    check_one_instance_per_stack does, rather than restated here. A hardcoded
    stack list is the thing that just broke.
    """
    ok, instances = _bot_instances(ctx)
    if not ok:
        return Result("cloudwatch_alarms", FAIL, "could not enumerate instances")
    if not instances:
        return absent(ctx, "aws_instance", "cloudwatch_alarms")

    stacks = sorted({
        tags.get("Stack") for i in instances
        if (tags := {t["Key"]: t["Value"] for t in i.get("Tags", [])}).get("Stack")
    })

    # The database is shared across stacks, so its alarm is not per-stack.
    required = {f"{ctx.name}-db-no-connections"}
    silence = {f"{ctx.name}-{s}-bot-silent" for s in stacks}
    required |= silence
    required |= {f"{ctx.name}-{s}-{k}" for s in stacks
                 for k in ("critical-events", "instance-status")}

    ok, data = aws(ctx, "cloudwatch", "describe-alarms",
                   "--alarm-name-prefix", ctx.name)
    if not ok:
        return Result("cloudwatch_alarms", FAIL, "could not list alarms")
    present = {a["AlarmName"] for a in data.get("MetricAlarms", [])}
    missing = required - present
    if missing:
        if ctx.creates("aws_cloudwatch_metric_alarm"):
            return Result("cloudwatch_alarms", PLANNED, f"missing but planned: {missing}")
        return Result("cloudwatch_alarms", FAIL, f"missing: {', '.join(sorted(missing))}")

    # The silence alarm is the one that catches a dead evaluation loop, and it
    # only works if missing data counts as breaching -- otherwise a bot that
    # never starts leaves the alarm permanently green.
    for alarm in data.get("MetricAlarms", []):
        if alarm["AlarmName"] in silence:
            if alarm.get("TreatMissingData") != "breaching":
                return Result("cloudwatch_alarms", FAIL,
                              f"{alarm['AlarmName']} has TreatMissingData="
                              f"{alarm.get('TreatMissingData')}; it must be "
                              "'breaching' or a bot that never logs stays green")
    return Result("cloudwatch_alarms", PASS,
                  f"{len(required)} required alarms present across "
                  f"{len(stacks)} stack(s) ({', '.join(stacks)}); "
                  f"silence alarms breach on missing data")


def _ago(seconds: int) -> str:
    """Absolute UTC timestamp. The CLI does not accept relative durations here,
    and passing one silently yields a parameter error that reads like a
    permissions problem."""
    import datetime
    t = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def check_alarms_watch_real_metrics(ctx: Context) -> Result:
    """Every alarm's configured dimensions must resolve to a metric with data.

    Presence is not enough. An alarm can exist, be correctly configured in
    every other respect, and be pointed at a dimension value that has never had
    a datapoint -- and if it treats missing data as NOT breaching, it then sits
    in OK forever and can never fire.

    That happened here: three AWS/RDS alarms were built from
    `aws_db_instance.main.id`, which is the DbiResourceId, while CloudWatch
    publishes under DBInstanceIdentifier = the identifier. Two of them were
    silent-by-construction for the entire deployment and nothing noticed,
    because a green alarm looks the same whether it is watching or blind.

    A BRAND NEW INSTANCE IS NOT A BLIND ALARM. EC2 does not publish
    StatusCheckFailed for the first few minutes of an instance's life, so this
    check failed the apply that CREATED the instance -- three times, on
    2026-08-15, once per stack. The apply itself had already succeeded each
    time, which made the red run purely misleading: it reported a
    misconfiguration that did not exist and said nothing about the one it is
    built to catch.

    "No datapoints yet" and "no datapoints ever" are different claims. Below
    MIN_METRIC_AGE the finding is reported and not failed; above it, an empty
    series still means the dimension is wrong.
    """
    ok, data = aws(ctx, "cloudwatch", "describe-alarms", "--alarm-name-prefix", ctx.name)
    if not ok:
        return Result("alarms_watch_real_metrics", FAIL, "could not list alarms")
    alarms = data.get("MetricAlarms", [])
    if not alarms:
        if ctx.creates("aws_cloudwatch_metric_alarm"):
            return Result("alarms_watch_real_metrics", PLANNED, "alarms not created yet")
        return Result("alarms_watch_real_metrics", FAIL, "no alarms exist")

    # Instances younger than this are exempt: EC2 has not had time to publish.
    # Measured from the 2026-08-15 applies, where the check ran roughly 90s
    # after launch and StatusCheckFailed first appeared some minutes later.
    young = set()
    ok_i, inst = aws(ctx, "ec2", "describe-instances", "--filters",
                     "Name=tag:Project,Values=deltabt",
                     f"Name=tag:Environment,Values={ctx.environment}",
                     "Name=instance-state-name,Values=pending,running")
    if ok_i:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        for r in inst.get("Reservations", []):
            for i in r.get("Instances", []):
                launched = i.get("LaunchTime")
                if not launched:
                    continue
                if isinstance(launched, str):
                    launched = datetime.datetime.fromisoformat(
                        launched.replace("Z", "+00:00"))
                if (now - launched).total_seconds() < MIN_METRIC_AGE:
                    young.add(i["InstanceId"])

    blind, warming = [], []
    for alarm in alarms:
        namespace = alarm.get("Namespace")
        # AWS-published namespaces always emit for a live resource, so "no
        # data" there means the dimension is wrong. The custom DeltaBt
        # namespace legitimately has no data until the bot logs, so it is
        # reported but not failed.
        if namespace not in ("AWS/RDS", "AWS/EC2"):
            continue
        dims = [f"Name={d['Name']},Value={d['Value']}" for d in alarm.get("Dimensions", [])]
        if not dims:
            continue
        found, stats = aws(ctx, "cloudwatch", "get-metric-statistics",
                           "--namespace", namespace,
                           "--metric-name", alarm["MetricName"],
                           "--dimensions", *dims,
                           "--start-time", _ago(3 * 3600), "--end-time", _ago(0),
                           "--period", "300", "--statistics", "Maximum")
        if not found:
            blind.append(f"{alarm['AlarmName']} (could not query)")
        elif not stats.get("Datapoints"):
            on_young = any(d.get("Name") == "InstanceId" and d.get("Value") in young
                           for d in alarm.get("Dimensions", []))
            target = warming if on_young else blind
            target.append(f"{alarm['AlarmName']} -> {namespace}/{alarm['MetricName']} "
                          f"{dims} has NO datapoints")
    if blind:
        return Result("alarms_watch_real_metrics", FAIL,
                      "alarms pointed at dimensions with no data: " + "; ".join(blind))
    watched = sum(1 for a in alarms if a.get("Namespace") in ("AWS/RDS", "AWS/EC2"))
    if warming:
        return Result("alarms_watch_real_metrics", PASS,
                      f"{watched} AWS-namespace alarms checked; "
                      f"{len(warming)} on an instance younger than "
                      f"{MIN_METRIC_AGE // 60} minutes, still warming: "
                      + "; ".join(warming))
    return Result("alarms_watch_real_metrics", PASS,
                  f"all {watched} AWS-namespace alarms resolve to metrics with data")


# ---------------------------------------------------------------------------

INFRASTRUCTURE_CHECKS = [
    check_credentials, check_account, check_region,
    check_oidc_provider, check_iam_roles, check_role_trust_is_scoped,
    check_backend_bucket, check_state_readable,
]

APPLICATION_CHECKS = INFRASTRUCTURE_CHECKS + [
    check_ecr, check_rds, check_one_instance_per_stack, check_no_public_ingress,
    check_ssm, check_secret, check_alarms, check_alarms_watch_real_metrics,
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("infrastructure", "application"),
                    default="application")
    ap.add_argument("--account", help="expected AWS account id; pin it")
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--environment", default="paper")
    ap.add_argument("--state-bucket")
    ap.add_argument("--ecr-repository", default="deltabt")
    ap.add_argument("--plan-json",
                    help="terraform show -json output. Absence of a resource is "
                         "only acceptable when this plan creates it.")
    args = ap.parse_args()

    plan = {}
    if args.plan_json:
        with open(args.plan_json) as fh:
            plan = json.load(fh)

    ctx = Context(region=args.region, environment=args.environment,
                  phase=args.phase,
                  expected_account=args.account, state_bucket=args.state_bucket,
                  ecr_repository=args.ecr_repository, plan=plan)

    checks = (INFRASTRUCTURE_CHECKS if args.phase == "infrastructure"
              else APPLICATION_CHECKS)

    print(f"AWS preflight -- phase: {args.phase}")
    print("=" * 72)

    results: list[Result] = []
    for check in checks:
        try:
            result = check(ctx)
        except Exception as exc:                              # noqa: BLE001
            # An exception is a FAILURE, never a skip. A preflight that treats
            # its own crash as "inconclusive, carry on" is decoration.
            result = Result(check.__name__.replace("check_", ""), FAIL,
                            f"check raised: {exc!r}")
        results.append(result)
        mark = {PASS: "PASS", PLANNED: "PLAN", FAIL: "FAIL"}[result.status]
        print(f"  [{mark}] {result.name:<28} {result.detail}")

    failures = [r for r in results if not r.ok]
    planned = [r for r in results if r.status == PLANNED]

    print("=" * 72)
    if planned:
        print(f"{len(planned)} resource(s) absent but created by the supplied plan.")
    if failures:
        print(f"PREFLIGHT FAILED -- {len(failures)} check(s):")
        for r in failures:
            print(f"    {r.name}: {r.detail}")
        print()
        print("Nothing was changed. Fix each failure and re-run.")
        return 1

    print(f"All {len(results)} checks passed.")
    print()
    print("This says the INFRASTRUCTURE is ready. It says nothing about trading:")
    print("no experiment has been created and no paper trading has started.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
