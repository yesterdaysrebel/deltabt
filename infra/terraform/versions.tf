terraform {
  # >= 1.10 for native S3 state locking (use_lockfile). Below that a DynamoDB
  # table is required; see docs/aws_deployment.md.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}
