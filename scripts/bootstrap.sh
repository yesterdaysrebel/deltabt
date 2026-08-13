#!/usr/bin/env bash
# The one intentionally manual AWS operation.
#
# Creates only trust anchors: the Terraform state bucket (with native S3
# locking, no DynamoDB table), the GitHub OIDC identity provider, and the IAM
# roles GitHub Actions assumes. Nothing else. It cannot start the bot, create
# an experiment, reach an exchange, or touch the database -- those resources do
# not exist in this stack and this role is never used to deploy the
# application.
#
# It runs with LOCAL state, from a workstation, with admin credentials, once
# per account+region. It is deliberately NOT a GitHub workflow: a workflow that
# can create its own trust anchor can also replace it, and then the answer to
# "who is allowed to deploy" is decided by whoever can push a branch.
#
# Usage:
#     TF_STATE_BUCKET=deltabt-tfstate-<unique> \
#     GITHUB_ORG=your-org \
#     ./scripts/bootstrap.sh [plan|apply]
set -euo pipefail

cd "$(dirname "$0")/.."

BOOTSTRAP_DIR="infra/terraform/bootstrap"
ACTION="${1:-plan}"
REGION="${AWS_REGION:-ap-south-1}"
BUCKET="${TF_STATE_BUCKET:?set TF_STATE_BUCKET to a globally unique bucket name}"
ORG="${GITHUB_ORG:?set GITHUB_ORG to the GitHub organisation or user}"
REPO="${GITHUB_REPO:-deltabt}"

case "$ACTION" in
  plan|apply) ;;
  *) echo "usage: $0 [plan|apply]" >&2; exit 2 ;;
esac

echo "=== 1. What already exists ==============================================="
# Never imports. Reports, and prints the import commands if adoption is needed.
set +e
python3 scripts/bootstrap_check.py --state-bucket "$BUCKET" --region "$REGION" \
  --bootstrap-dir "$BOOTSTRAP_DIR"
CHECK=$?
set -e

if [ "$CHECK" -eq 1 ]; then
  echo "Could not determine the current state. Nothing was changed." >&2
  exit 1
fi
if [ "$CHECK" -eq 2 ]; then
  echo >&2
  echo "Refusing to continue: import the resources listed above first." >&2
  echo "This script will not adopt them for you -- taking ownership of who can" >&2
  echo "deploy is a decision, not a step." >&2
  exit 2
fi

echo
echo "=== 2. Plan =============================================================="
terraform -chdir="$BOOTSTRAP_DIR" init -input=false
terraform -chdir="$BOOTSTRAP_DIR" plan -input=false -out=bootstrap.tfplan \
  -var="aws_region=$REGION" \
  -var="github_org=$ORG" \
  -var="github_repo=$REPO" \
  -var="state_bucket_name=$BUCKET"

terraform -chdir="$BOOTSTRAP_DIR" show -json bootstrap.tfplan > "$BOOTSTRAP_DIR/bootstrap.tfplan.json"

echo
echo "=== 3. Guards ============================================================"
# The same guard the infrastructure workflow uses. The state bucket is on its
# protected list, so a plan that would replace it fails here rather than in the
# middle of an apply.
python3 scripts/tf_guard.py "$BOOTSTRAP_DIR/bootstrap.tfplan.json"
python3 scripts/tf_cost_preview.py "$BOOTSTRAP_DIR/bootstrap.tfplan.json"

if [ "$ACTION" = "plan" ]; then
  echo
  echo "Plan only. Re-run with 'apply' when the plan above is what you want."
  exit 0
fi

echo
echo "=== 4. Apply ============================================================="
echo "About to create trust anchors in account $(aws sts get-caller-identity --query Account --output text) / $REGION"
echo "Type the account id to confirm:"
read -r CONFIRM
if [ "$CONFIRM" != "$(aws sts get-caller-identity --query Account --output text)" ]; then
  echo "Account id did not match. Nothing was changed." >&2
  exit 1
fi

terraform -chdir="$BOOTSTRAP_DIR" apply -input=false bootstrap.tfplan

echo
echo "=== 5. GitHub repository variables to set ================================"
terraform -chdir="$BOOTSTRAP_DIR" output
cat <<'NEXT'

Set these as repository VARIABLES (Settings -> Secrets and variables -> Actions
-> Variables). None of them is a secret -- an ARN is not a credential, and
there are no AWS access keys anywhere in this system:

    AWS_REGION                    the region above
    TF_STATE_BUCKET               output state_bucket
    AWS_TERRAFORM_ROLE_ARN        output github_terraform_role_arn
    AWS_TERRAFORM_PLAN_ROLE_ARN   output github_plan_role_arn

Then create a GitHub environment named `paper` with yourself as a required
reviewer. That gate is what makes every infrastructure change and every deploy
a deliberate decision rather than a side effect of a merge.

Then run the `infrastructure` workflow to build the main stack, and set the
four remaining variables from its outputs:

    AWS_DEPLOY_ROLE_ARN  SSM_DEPLOY_DOCUMENT  BOT_INSTANCE_ID  ECR_REPOSITORY

Successful infrastructure deployment does not create an experiment and does not
start paper trading. See docs/aws_deployment.md section 13.
NEXT
