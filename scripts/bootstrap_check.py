#!/usr/bin/env python3
"""Report which bootstrap trust anchors already exist, and how to adopt them.

The bootstrap stack creates the things that must exist before anything else
can run: the state bucket, the GitHub OIDC provider, and the three IAM roles.
It is the ONLY intentionally manual AWS operation in this system, and it runs
with local state -- so the situation "the resource exists but this state file
has never seen it" is not an edge case here, it is what happens the second time
anyone runs it from a different machine.

This script never changes anything. It reads AWS, reads the local state if
there is one, and prints a table plus the exact `terraform import` lines. It
does NOT import: adoption of an existing trust anchor is a decision, and a
script that silently imported an IAM role would be a script that silently
took ownership of who can deploy.

Usage:
    python scripts/bootstrap_check.py --state-bucket <name> [--region ap-south-1]

Exit codes:
    0  nothing exists yet (a clean first apply), or everything is managed
    2  something exists in AWS but is not in this state file -- import first
    1  could not determine (no credentials, CLI failure)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

OIDC_HOST = "token.actions.githubusercontent.com"

#: Terraform address -> human description. Order matters for the report.
ANCHORS = [
    ("aws_s3_bucket.state", "Terraform state bucket"),
    ("aws_iam_openid_connect_provider.github", "GitHub OIDC identity provider"),
    ("aws_iam_role.github_deploy", "Terraform infrastructure role"),
    ("aws_iam_role.github_plan", "Pull-request read-only plan role"),
]


def aws(*args: str) -> tuple[bool, dict]:
    try:
        proc = subprocess.run(["aws", *args, "--output", "json"],
                              capture_output=True, text=True, timeout=60)
    except Exception as exc:                                  # noqa: BLE001
        print(f"  (aws {' '.join(args)}: {exc})", file=sys.stderr)
        return False, {}
    if proc.returncode != 0:
        return False, {}
    return True, json.loads(proc.stdout) if proc.stdout.strip() else {}


def terraform_state_addresses(directory: str) -> set[str] | None:
    """Addresses in the bootstrap's LOCAL state, or None if there is no state."""
    try:
        proc = subprocess.run(["terraform", f"-chdir={directory}", "state", "list"],
                              capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        # No state file, or not initialised. Both mean "manages nothing yet".
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-bucket", required=True)
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--bootstrap-dir", default="infra/terraform/bootstrap")
    args = ap.parse_args()

    ok, ident = aws("sts", "get-caller-identity")
    if not ok:
        print("No usable AWS credentials. Nothing was checked and nothing was "
              "changed.", file=sys.stderr)
        print("Configure credentials for the account you intend to bootstrap, "
              "then re-run.", file=sys.stderr)
        return 1
    account = ident["Account"]

    print(f"Account : {account}")
    print(f"Region  : {args.region}")
    print(f"Identity: {ident['Arn']}")
    print()
    print("READ THAT ACCOUNT ID. Bootstrapping the wrong account is the easiest")
    print("mistake available here, and it creates a trust anchor for this")
    print("repository inside somebody else's account.")
    print()

    managed = terraform_state_addresses(args.bootstrap_dir)
    if managed is None:
        print("terraform is not on PATH; cannot tell what local state manages.",
              file=sys.stderr)
        return 1

    region = ["--region", args.region]
    exists: dict[str, str] = {}   # address -> physical id

    if aws("s3api", "head-bucket", "--bucket", args.state_bucket)[0]:
        exists["aws_s3_bucket.state"] = args.state_bucket

    provider_arn = f"arn:aws:iam::{account}:oidc-provider/{OIDC_HOST}"
    if aws("iam", "get-open-id-connect-provider",
           "--open-id-connect-provider-arn", provider_arn)[0]:
        exists["aws_iam_openid_connect_provider.github"] = provider_arn

    for address, role in (("aws_iam_role.github_deploy", "deltabt-github-deploy"),
                          ("aws_iam_role.github_plan", "deltabt-github-plan")):
        if aws("iam", "get-role", "--role-name", role, *region)[0]:
            exists[address] = role

    width = max(len(d) for _, d in ANCHORS) + 2
    print(f"{'Trust anchor':<{width}} {'In AWS':<10} {'In state':<10} Status")
    print("-" * (width + 34))

    unmanaged: list[tuple[str, str, str]] = []
    for address, description in ANCHORS:
        in_aws = address in exists
        in_state = address in managed
        if in_aws and in_state:
            status = "managed"
        elif in_aws and not in_state:
            status = "NEEDS IMPORT"
            unmanaged.append((address, exists[address], description))
        elif not in_aws and in_state:
            status = "STALE STATE"
        else:
            status = "will be created"
        print(f"{description:<{width}} {'yes' if in_aws else 'no':<10} "
              f"{'yes' if in_state else 'no':<10} {status}")

    print()
    if not exists:
        print("Nothing exists yet. This is a clean first bootstrap:")
        print()
        print("    ./scripts/bootstrap.sh")
        return 0

    if not unmanaged:
        print("Every existing trust anchor is already managed by this state file.")
        print("`terraform apply` will reconcile configuration and create nothing new.")
        return 0

    print("=" * 72)
    print("EXISTING RESOURCES ARE NOT IN THIS STATE FILE")
    print("=" * 72)
    print()
    print("Terraform would try to CREATE these and fail with 'already exists'.")
    print("That failure is safe -- it never adopts or replaces -- but it happens")
    print("part-way through an apply. Import them first, deliberately:")
    print()
    print(f"    cd {args.bootstrap_dir}")
    for address, physical, _ in unmanaged:
        print(f"    terraform import {address:<45} {physical}")
    print()
    print("Then re-run this check and read the plan before applying.")
    print()
    print("Do NOT delete these in the console to 'start clean'. The state bucket")
    print("holds the map of all other infrastructure, and the roles are what")
    print("GitHub Actions authenticates as; deleting either breaks deployment")
    print("for everyone until it is rebuilt and re-wired.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
