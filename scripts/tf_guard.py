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

TWO OVERRIDES, AND THE NARROW ONE IS THE POINT.

    ALLOW_REPLACE=1        permits ANY finding below, the database included. A
                           blunt instrument, kept for a human on a deliberate
                           dispatch, and printed loudly.

    ALLOW_REPLACE_TYPES=   comma-separated resource TYPES whose REPLACEMENT
                           (delete-then-create) is an expected cost and may
                           proceed unattended. A bare DESTROY of a listed type
                           is still refused, and any type not listed is still
                           refused outright.

The narrow form is what lets a merge deploy itself. Replacing the bot host is
the ordinary consequence of editing user-data or the strategy variant, and
requiring a person to approve it every time produced either an approval nobody
read or a change that sat unshipped for days. Destroying the DATABASE is a
different act, and no automated path can reach it: `aws_db_instance` is not in
the pipeline's list and must never be added to it.
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


def _narrowly_allowed() -> set[str]:
    """Types this caller says may be replaced without a human.

    Read from the environment rather than hard-coded, because "may a plan
    replace things" is the wrong question and "which things, in this pipeline"
    is the right one. The workflow states the answer at the call site; the
    guard does not decide policy on every caller's behalf.
    """
    raw = os.environ.get("ALLOW_REPLACE_TYPES", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def main(path: str) -> int:
    with open(path) as fh:
        plan = json.load(fh)

    allowed = _narrowly_allowed()
    findings, permitted = [], []
    for change in plan.get("resource_changes", []):
        actions = set(change.get("change", {}).get("actions", []))
        if not actions & DESTRUCTIVE:
            continue
        rtype = change.get("type", "")
        if rtype not in PROTECTED_TYPES:
            continue
        # "delete"+"create" is a replacement; a bare "delete" is a removal.
        kind = "REPLACE" if "create" in actions else "DESTROY"
        # A REPLACEMENT of a named type is the expected cost of a config
        # change and proceeds. A DESTROY never is: nothing in the pipeline
        # removes a host without putting one back, so a plan that only
        # deletes one is precisely the plan to stop, listed or not.
        if kind == "REPLACE" and rtype in allowed:
            permitted.append((kind, change.get("address", "?"), rtype))
            continue
        findings.append((kind, change.get("address", "?"), rtype))

    for kind, address, rtype in permitted:
        print(f"tf_guard: {kind} {address} -- permitted by ALLOW_REPLACE_TYPES")
        print(f"          ({PROTECTED_TYPES[rtype]})")

    if not findings:
        print("tf_guard: nothing protected is destroyed, and nothing is "
              "replaced outside the permitted list.")
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
