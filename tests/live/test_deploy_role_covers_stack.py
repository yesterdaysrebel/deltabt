"""The CI role must be allowed to manage everything the stack declares.

THE DEFECT THIS PINS
    infra/terraform/cloudwatch.tf has always declared an SNS topic and an email
    subscription -- gated on `alarm_email`, which was empty, so they were never
    created. On 2026-08-27 the address was finally supplied and the apply died:

        AuthorizationError: User: .../deltabt-github-deploy is not authorized
        to perform: SNS:CreateTopic ... because no identity-based policy allows
        the SNS:CreateTopic action

    The bootstrap policy granted `cloudwatch:*` but not `sns:*`. Those are two
    services: cloudwatch:* creates the alarms, sns:* is what lets one DELIVER.
    So the account held twelve correct alarms and no way to notify anyone, and
    every signal available beforehand said monitoring was provisioned.

WHY NEITHER THE PLAN NOR THE GUARD CAUGHT IT
    A plan asks "what would change", not "may this principal change it".
    Terraform makes no authorization call at plan time, and the pull-request
    plan runs under a DIFFERENT, read-only role by design -- so a plan is
    structurally incapable of proving the apply role can do what it describes.
    The first authorization check in the whole pipeline is the mutating API
    call itself, after review, after approval, half way through an apply.

WHAT IS ASSERTED
    Every AWS service the main stack declares a resource for is granted to the
    apply role by the bootstrap policy. This is a static cross-file check, the
    same shape as tests/live/test_env_forwarding.py and
    tests/live/test_alarm_delivery.py: a value correct in one file and never
    delivered to the thing that consumes it. Fourth of that shape in two days.

    It cannot prove an apply will succeed -- IAM has conditions, boundaries and
    SCPs this does not model. It does catch the failure that actually happened:
    a new resource type for a service nobody added to the policy.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
STACK_DIR = ROOT / "infra/terraform"
BOOTSTRAP = ROOT / "infra/terraform/bootstrap/main.tf"

#: Terraform resource type -> the IAM service prefix its APIs live under. The
#: mapping is not derivable from the name: aws_cloudwatch_log_group is `logs`,
#: aws_db_instance is `rds`, and every networking resource is `ec2`. An
#: unmapped type is a test failure rather than a silent pass -- see
#: test_every_declared_resource_type_is_mapped.
SERVICE_OF = {
    "aws_cloudwatch_dashboard": "cloudwatch",
    "aws_cloudwatch_log_group": "logs",
    "aws_cloudwatch_log_metric_filter": "logs",
    "aws_cloudwatch_metric_alarm": "cloudwatch",
    "aws_db_instance": "rds",
    "aws_db_subnet_group": "rds",
    "aws_ecr_lifecycle_policy": "ecr",
    "aws_ecr_repository": "ecr",
    "aws_eip": "ec2",
    "aws_iam_instance_profile": "iam",
    "aws_iam_role": "iam",
    "aws_iam_role_policy": "iam",
    "aws_iam_role_policy_attachment": "iam",
    "aws_instance": "ec2",
    "aws_internet_gateway": "ec2",
    "aws_route_table": "ec2",
    "aws_route_table_association": "ec2",
    "aws_security_group": "ec2",
    "aws_security_group_rule": "ec2",
    "aws_sns_topic": "sns",
    "aws_sns_topic_subscription": "sns",
    "aws_ssm_document": "ssm",
    "aws_ssm_parameter": "ssm",
    "aws_subnet": "ec2",
    "aws_vpc": "ec2",
}


def _declared_types() -> set[str]:
    """aws_* resource types the main stack declares. Excludes bootstrap."""
    found: set[str] = set()
    for tf in sorted(STACK_DIR.glob("*.tf")):
        found |= set(re.findall(r'^resource "(aws_[a-z0-9_]+)"',
                                tf.read_text(), re.M))
    return found


def _granted_prefixes() -> set[str]:
    """Service prefixes the deploy policy allows, from its actions lists.

    Reads the `deploy` policy document only: the plan role is a separate,
    read-only principal and is not what applies.
    """
    text = BOOTSTRAP.read_text()
    start = text.index('data "aws_iam_policy_document" "deploy"')
    end = text.index('resource "aws_iam_role_policy" "deploy"', start)
    body = text[start:end]
    # Strip comments first: this file explains itself at length, and prose
    # naming a service is not a grant of it.
    body = re.sub(r"#.*", "", body)
    return {a.split(":", 1)[0] for a in re.findall(r'"([a-z0-9-]+:[^"]+)"', body)}


def test_every_declared_resource_type_is_mapped():
    """A new resource type must be classified before it can be checked."""
    unmapped = _declared_types() - set(SERVICE_OF)
    assert not unmapped, (
        f"{sorted(unmapped)} are declared in infra/terraform but absent from "
        f"SERVICE_OF, so this test cannot tell whether the deploy role may "
        f"create them. Add each one with its IAM service prefix (which is not "
        f"always the obvious part of the name).")


@pytest.mark.parametrize("resource_type", sorted(SERVICE_OF))
def test_the_deploy_role_is_granted_the_service(resource_type):
    service = SERVICE_OF[resource_type]
    assert service in _granted_prefixes(), (
        f"infra/terraform declares {resource_type}, which needs {service}:* "
        f"permissions, but the deploy policy in "
        f"infra/terraform/bootstrap/main.tf grants no {service}: action. The "
        f"apply will fail with AuthorizationError AFTER the plan, the guard "
        f"and the human approval have all passed.")


def test_sns_specifically_the_one_that_broke():
    """Named so the regression cannot be deleted as incidental."""
    assert "sns" in _granted_prefixes(), (
        "sns:* is missing from the deploy policy. CloudWatch alarms will be "
        "created and will never be able to notify anyone -- the exact state "
        "the account was in until 2026-08-27.")


def test_the_stack_still_declares_a_topic_to_notify_through():
    """If the SNS resources go, the grant above is dead weight -- and so is
    every alarm's alarm_actions. Fails loudly rather than rotting."""
    text = (STACK_DIR / "cloudwatch.tf").read_text()
    assert 'resource "aws_sns_topic" "alarms"' in text
    assert 'resource "aws_sns_topic_subscription" "alarms_email"' in text


def test_bootstrap_is_the_only_place_the_role_policy_is_defined():
    """The main stack holds iam:* and could grant itself anything. It must not:
    a CI role that edits its own permissions has no ceiling. Keep the deploy
    policy in the bootstrap stack, which only a human applies."""
    for tf in sorted(STACK_DIR.glob("*.tf")):
        text = tf.read_text()
        assert "deltabt-github-deploy" not in text or "data" in text, (
            f"{tf.name} references the deploy role. Its permissions belong in "
            f"the bootstrap stack; a role that can widen itself is unbounded.")
