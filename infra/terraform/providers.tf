provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "deltabt"
      Environment = var.environment
      ManagedBy   = "terraform"
      Purpose     = "paper-trading-forward-test"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Amazon Linux 2023 on arm64: the SSM agent is preinstalled, and Graviton is
# roughly 20% cheaper than x86 for the same work.
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}
