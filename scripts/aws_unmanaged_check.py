#!/usr/bin/env python3
"""Fail if AWS holds a deltabt resource that Terraform state does not know about.

Terraform's own collision behaviour already prevents the worst outcome: names
are fixed, so a create against an existing resource errors instead of adopting
or clobbering it. But that error arrives MID-APPLY, after some resources have
been created, and it arrives as a provider message that reads like a bug rather
than as an instruction. This runs first and says exactly what to do.

It also catches the case Terraform cannot see at all: a SECOND EC2 instance
tagged for this environment. Terraform is content -- its one instance exists --
while two bots race for the same paper account. The database advisory lock is
the authoritative protection and would refuse the second one, but discovering
that from a crash loop at 3am is worse than discovering it here.

Usage (from infra/terraform, after `terraform init`):
    terraform show -json > state.json
    python ../../scripts/aws_unmanaged_check.py state.json --environment paper

Requires read-only AWS access. Exits 1 on any finding.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


def aws(*args: str) -> dict:
    """Run an AWS CLI call and return parsed JSON. {} on any failure.

    Failures are non-fatal on purpose: this check must never be the reason a
    deploy cannot happen. It exists to catch a specific, dangerous situation,
    not to gate on the CLI behaving.
    """
    try:
        out = subprocess.run(
            ["aws", *args, "--output", "json"],
            capture_output=True, text=True, timeout=60, check=True).stdout
        return json.loads(out) if out.strip() else {}
    except Exception as exc:                                # noqa: BLE001
        print(f"  (could not query: aws {' '.join(args)}: {exc})", file=sys.stderr)
        return {}


def managed_ids(state_path: str) -> set[str]:
    """Every physical id Terraform currently manages, flattened."""
    with open(state_path) as fh:
        state = json.load(fh)

    ids: set[str] = set()

    def walk(module: dict) -> None:
        for res in module.get("resources", []):
            for key in ("id", "arn", "identifier", "name", "instance_id"):
                value = res.get("values", {}).get(key)
                if isinstance(value, str):
                    ids.add(value)
        for child in module.get("child_modules", []):
            walk(child)

    walk(state.get("values", {}).get("root_module", {}))
    return ids


def group_by_stack(instances: list[dict]) -> dict[str, list[str]]:
    """Instance ids grouped by their Stack tag.

    An untagged host groups with every other untagged host, deliberately: a
    machine that lost its tags must not pass as "a different stack" and slip
    past the duplicate check.

    THIS LOGIC EXISTS IN FOUR PLACES and they are separate on purpose --
    aws_preflight.py, verify_deployment.py, scripts/daily_report.py and here
    are standalone tools, invoked by path on CI runners and on hosts, with no
    shared package between them. That duplication is also why the move from
    "one instance" to "one instance per stack" had to be made four times, and
    was missed here until a plan refused with two legitimate hosts running.
    """
    out: dict[str, list[str]] = {}
    for i in instances:
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        out.setdefault(tags.get("Stack", "<untagged>"), []).append(i["InstanceId"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("state_json", help="output of `terraform show -json`")
    ap.add_argument("--environment", default="paper")
    ap.add_argument("--region", default=None)
    args = ap.parse_args()

    region = ["--region", args.region] if args.region else []
    prefix = f"deltabt-{args.environment}"
    known = managed_ids(args.state_json)

    problems: list[str] = []

    # --- EC2 ---------------------------------------------------------------
    reservations = aws(
        "ec2", "describe-instances", *region,
        "--filters",
        "Name=tag:Project,Values=deltabt",
        f"Name=tag:Environment,Values={args.environment}",
        "Name=instance-state-name,Values=pending,running,stopping,stopped",
    ).get("Reservations", [])
    instances = [i for r in reservations for i in r.get("Instances", [])]

    for inst in instances:
        iid = inst["InstanceId"]
        if iid not in known:
            problems.append(
                f"EC2 instance {iid} is tagged for this environment but is NOT in "
                f"Terraform state.\n"
                f"    Import it before deploying:\n"
                f"        terraform import aws_instance.bot {iid}\n"
                f"    Or, if it is an orphan from a previous experiment, terminate it\n"
                f"    DELIBERATELY -- after confirming it is not the bot currently\n"
                f"    holding the database advisory lock.")

    # ONE BOT PER STACK, NOT ONE BOT IN TOTAL.
    #
    # Two experiments now run side by side, each on its own host and its own
    # DATABASE. The collision this check exists for is per-database: the
    # advisory lock, the single-RUNNING-experiment index and
    # ux_positions_open_symbol are all scoped to one. Two bots in one stack is
    # exactly as wrong as it ever was; two stacks is the intended shape.
    #
    # An untagged host counts as one stack with every other untagged host, so
    # a machine that lost its tags cannot pass as "a different stack".
    running = [i for i in instances if i["State"]["Name"] in ("pending", "running")]
    for stack, ids in sorted(group_by_stack(running).items()):
        if len(ids) > 1:
            problems.append(
                f"{len(ids)} bot instances are running in stack '{stack}' for "
                f"environment '{args.environment}': {', '.join(ids)}\n"
                f"    Exactly one bot may run per stack. Two share a database and\n"
                f"    would interleave one paper account; the PostgreSQL advisory\n"
                f"    lock will refuse the second, so the visible symptom is a\n"
                f"    crash loop rather than corrupt data -- but the situation is\n"
                f"    still wrong and must be resolved by hand.")

    # --- RDS ---------------------------------------------------------------
    for db in aws("rds", "describe-db-instances", *region).get("DBInstances", []):
        ident = db["DBInstanceIdentifier"]
        if ident.startswith(prefix) and ident not in known:
            problems.append(
                f"RDS instance {ident} exists but is NOT in Terraform state.\n"
                f"    THIS MAY HOLD FORWARD-TEST DATA. Do not delete it. Import it:\n"
                f"        terraform import aws_db_instance.main {ident}")

    # --- ECR ---------------------------------------------------------------
    for repo in aws("ecr", "describe-repositories", *region).get("repositories", []):
        name = repo["repositoryName"]
        if name.startswith("deltabt") and name not in known:
            problems.append(
                f"ECR repository {name} exists but is NOT in Terraform state.\n"
                f"    It holds the images past experiments ran from. Import it:\n"
                f"        terraform import aws_ecr_repository.bot {name}")

    if not problems:
        print(f"No unmanaged deltabt-{args.environment} resources found in AWS.")
        print(f"Running bot instances: {len(running)} (exactly 1 is expected once deployed).")
        return 0

    print("=" * 72)
    print("UNMANAGED OR DUPLICATE INFRASTRUCTURE -- REFUSING TO PROCEED")
    print("=" * 72)
    for i, problem in enumerate(problems, 1):
        print(f"\n{i}. {problem}")
    print()
    print("Nothing has been changed. Terraform will not destroy or replace any of")
    print("the above; resolve each one deliberately and re-run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
