# Remote state. State must never live only on a CI runner: a runner is
# destroyed after every job, and losing state means Terraform no longer knows
# what it manages.
#
# The bucket is created by infra/terraform/bootstrap and its name is supplied
# at init time, so this repository hardcodes no account-specific value:
#
#   terraform init \
#     -backend-config="bucket=$TF_STATE_BUCKET" \
#     -backend-config="region=$AWS_REGION"
#
# `use_lockfile` uses S3 conditional writes for locking (Terraform >= 1.10),
# so no DynamoDB table is needed.
terraform {
  backend "s3" {
    key          = "deltabt/paper/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
