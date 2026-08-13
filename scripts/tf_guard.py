#!/usr/bin/env python3
"""Refuse a Terraform plan that would destroy or replace something irreplaceable.

`prevent_destroy` already blocks a direct destroy of the database, and
`disable_api_termination` blocks a console termination of the instance. Neither
covers the case this guard exists for: an apply that looks routine but forces a
REPLACEMENT -- a changed AMI filter, an edited user-data script, a renamed
identifier. Terraform reports that as delete-then-create and, on a resource
holding thirty days of experiment data, it is indistinguishable from deletion.

Usage:
    terraform show -json tfplan > tfplan.json
    python scripts/tf_guard.py tfplan.json

Exit 0 if the plan only creates and updates protected resources, 1 otherwise.
Set ALLOW_REPLACE=1 to override deliberately -- it is printed loudly so an
override cannot happen by accident in a log nobody reads.
"""

from __future__ import annotations

import json
import os
import sys

#: Resources whose destruction loses data, evidence, or identity. Everything
#: else in this stack is reconstructible from the repository in minutes.
PROTECTED_TYPES = {
    "aws_db_instance": "the experiment database -- the only artifact of a run that cannot be rebuilt",
    "aws_db_subnet_group": "recreating it requires recreating the database",
    "aws_s3_bucket": "Terraform state; losing it means Terraform no longer knows what it manages",
    "aws_ecr_repository": "every image a past experiment was run from",
    "aws_cloudwatch_log_group": "the run's operational history",
    "aws_instance": "the running bot; replacement is an outage mid-experiment",
    "aws_eip": "a changed address invalidates the documented access commands",
}

DESTRUCTIVE = {"delete"}


def main(path: str) -> int:
    with open(path) as fh:
        plan = json.load(fh)

    findings = []
    for change in plan.get("resource_changes", []):
        actions = set(change.get("change", {}).get("actions", []))
        if not actions & DESTRUCTIVE:
            continue
        rtype = change.get("type", "")
        if rtype not in PROTECTED_TYPES:
            continue
        # "delete"+"create" is a replacement; a bare "delete" is a removal.
        kind = "REPLACE" if "create" in actions else "DESTROY"
        findings.append((kind, change.get("address", "?"), rtype))

    if not findings:
        print("tf_guard: no protected resource is destroyed or replaced.")
        return 0

    print("=" * 72)
    print("REFUSING THIS PLAN")
    print("=" * 72)
    for kind, address, rtype in findings:
        print(f"  {kind:8} {address}")
        print(f"           {PROTECTED_TYPES[rtype]}")
    print()
    print("If the resource already exists in AWS but not in state, IMPORT it")
    print("rather than letting Terraform recreate it:")
    print("    terraform import <address> <id>          # see infra/terraform/main.tf")
    print()
    print("If the replacement is genuinely intended, take a snapshot first and")
    print("re-run with ALLOW_REPLACE=1.")

    if os.environ.get("ALLOW_REPLACE") == "1":
        print()
        print("ALLOW_REPLACE=1 IS SET -- proceeding with the destructive plan above.")
        return 0
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: tf_guard.py <plan.json>")
    sys.exit(main(sys.argv[1]))
