#!/usr/bin/env python3
"""Print what a Terraform plan will create and flag anything expensive.

Not a billing estimator -- it does not call the pricing API and it would be
dishonest to present its numbers as a quote. It answers the narrower question
that actually catches mistakes: "is this plan about to create something whose
monthly cost is a multiple of the whole stack's?"

The specific mistake it exists to catch is a NAT gateway. This architecture has
none, deliberately; one appearing in a plan costs more per month than every
other resource here combined, and it appears the moment somebody moves the
instance to a private subnet "to be safer".

It FAILS (exit 1) when a plan creates anything on the rejected list. A warning
is not a control: the plan output nobody reads is exactly where an accidental
NAT gateway survives. Override deliberately with ALLOW_EXPENSIVE=1, which is
printed loudly so it cannot happen quietly.

Usage:
    terraform show -json tfplan > tfplan.json
    python scripts/tf_cost_preview.py tfplan.json
"""

from __future__ import annotations

import collections
import json
import os
import sys

#: When set, an unrecognised resource kind is also a failure and not just a
#: note. Off by default: a new IAM policy attachment should not block a deploy.
STRICT = os.environ.get("STRICT_COST_GUARD") == "1"

#: type -> (approximate USD/month at ap-south-1 list price, note)
#: Rounded and deliberately conservative. Sourced from public on-demand
#: pricing; verify against the actual bill in the first week.
EXPECTED = {
    "aws_instance": (12.0, "t4g.small on-demand"),
    "aws_db_instance": (13.0, "db.t4g.micro plus 20 GB gp3"),
    "aws_ebs_volume": (2.0, "gp3 per 20 GB"),
    "aws_eip": (3.6, "charged even while attached, since 2024"),
    "aws_cloudwatch_log_group": (1.0, "ingest plus 90-day retention at this volume"),
    "aws_ecr_repository": (1.0, "20 retained arm64 images"),
    "aws_s3_bucket": (0.1, "state only"),
}

#: Things this design does not use. Their presence in a plan FAILS the job.
#: These are not estimates to add up -- they are architecture decisions with
#: prices attached, and reversing one silently is the mistake to prevent.
EXPENSIVE = {
    "aws_nat_gateway": "~$32/mo plus data processing. This stack does not need one: "
                       "the bot sits in a public subnet with no ingress rules, and "
                       "SSM is outbound-only.",
    "aws_lb": "~$18/mo. Nothing serves public traffic; the dashboard is reached "
              "through SSM port forwarding.",
    "aws_alb": "~$18/mo. See aws_lb.",
    "aws_eks_cluster": "~$73/mo for the control plane alone, to run one container "
                       "that must never run twice.",
    "aws_dynamodb_table": "Not needed since Terraform 1.10 -- S3 native locking "
                          "(use_lockfile) replaced the lock table.",
    "aws_rds_cluster": "Aurora starts around 4x db.t4g.micro for this workload.",
    "aws_elasticache_cluster": "Nothing in the bot uses a cache.",
    "aws_vpc_endpoint": "~$7/mo per endpoint per AZ. Only worth it if the instance "
                        "is moved off the public subnet.",
    "aws_autoscaling_group": "Not a cost problem, a CORRECTNESS one: an autoscaler "
                             "exists to run more than one of something, and exactly "
                             "one bot may run.",
    "aws_ecs_service": "Same objection as an autoscaling group, plus a scheduler "
                       "that can start a replacement task before the old one exits.",
    "aws_cloudfront_distribution": "Nothing is served publicly.",
    "aws_efs_file_system": "The bot keeps no state on disk; the database holds it.",
}

#: Resources this stack legitimately creates. Anything creatable that is not
#: here and not priced above is reported as UNEXPECTED -- the point is to
#: notice a plan growing a resource kind nobody discussed, whatever it is.
EXPECTED_KINDS = {
    "aws_vpc", "aws_subnet", "aws_internet_gateway", "aws_route_table",
    "aws_route_table_association", "aws_security_group", "aws_security_group_rule",
    "aws_instance", "aws_eip", "aws_ami", "aws_key_pair",
    "aws_db_instance", "aws_db_subnet_group",
    "aws_ecr_repository", "aws_ecr_lifecycle_policy",
    "aws_iam_role", "aws_iam_role_policy", "aws_iam_role_policy_attachment",
    "aws_iam_instance_profile", "aws_iam_openid_connect_provider",
    "aws_iam_policy", "aws_iam_policy_attachment",
    "aws_ssm_parameter", "aws_ssm_document",
    "aws_cloudwatch_log_group", "aws_cloudwatch_log_metric_filter",
    "aws_cloudwatch_metric_alarm", "aws_cloudwatch_dashboard",
    "aws_sns_topic", "aws_sns_topic_subscription",
    "aws_s3_bucket", "aws_s3_bucket_versioning",
    "aws_s3_bucket_server_side_encryption_configuration",
    "aws_s3_bucket_public_access_block", "aws_s3_bucket_policy",
    "aws_s3_bucket_lifecycle_configuration",
}


def main(path: str) -> int:
    with open(path) as fh:
        plan = json.load(fh)

    creating = collections.Counter()
    for change in plan.get("resource_changes", []):
        actions = change.get("change", {}).get("actions", [])
        if "create" in actions:
            creating[change["type"]] += 1

    if not creating:
        print("Plan creates nothing.")
        return 0

    print("Resources this plan will CREATE")
    print("-" * 72)
    known_total = 0.0
    for rtype, count in sorted(creating.items()):
        cost, note = EXPECTED.get(rtype, (0.0, ""))
        known_total += cost * count
        line = f"  {count:>3} x {rtype}"
        if cost:
            line += f"{'':<{max(0, 44 - len(rtype))}} ~${cost * count:6.2f}/mo  ({note})"
        print(line)

    print("-" * 72)
    print(f"  Recurring resources above account for roughly ${known_total:.0f}/month.")
    print("  Free or usage-priced and not counted: VPC, subnets, route tables,")
    print("  internet gateway, security groups, IAM, SSM parameters and documents,")
    print("  CloudWatch alarms (first 10 free), SNS at this volume.")
    print("  This is an ORDER OF MAGNITUDE, not a quote. Check the real bill after")
    print("  the first week.")

    unexpected = sorted(
        t for t in creating
        if t not in EXPECTED_KINDS and t not in EXPECTED and t not in EXPENSIVE
    )
    if unexpected:
        print()
        print("UNRECOGNISED RESOURCE KINDS IN THIS PLAN")
        print("-" * 72)
        for rtype in unexpected:
            print(f"  {creating[rtype]} x {rtype}")
        print()
        print("  Not necessarily wrong, but nothing in this architecture asked for")
        print("  them. Price each one before approving, then add it to")
        print("  EXPECTED_KINDS in this script so the next plan is quiet again.")

    flagged = sorted(t for t in creating if t in EXPENSIVE)
    if not flagged:
        return 1 if unexpected and STRICT else 0

    print()
    print("!" * 72)
    print("REFUSING THIS PLAN: RESOURCES THIS ARCHITECTURE DELIBERATELY AVOIDS")
    print("!" * 72)
    for rtype in flagged:
        print(f"\n  {creating[rtype]} x {rtype}")
        print(f"      {EXPENSIVE[rtype]}")
    print()
    print("  Each of these was considered and rejected, with the reasoning in")
    print("  docs/aws_deployment.md. If one is genuinely needed now, that is a")
    print("  design change to discuss -- not a plan to approve quickly.")

    if os.environ.get("ALLOW_EXPENSIVE") == "1":
        print()
        print("ALLOW_EXPENSIVE=1 IS SET -- proceeding with the resources above.")
        return 0
    print()
    print("  To proceed deliberately: ALLOW_EXPENSIVE=1")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: tf_cost_preview.py <plan.json>")
    sys.exit(main(sys.argv[1]))
