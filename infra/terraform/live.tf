# ---------------------------------------------------------------------------
# LIVE TRADING. Everything that can reach an exchange lives in this ONE file.
#
# WHY IT IS ALL HERE AND NOT SPREAD ACROSS variables.tf AND iam.tf
#
# tests/live/test_deployment_safety.py scans the deployment surface and asserts
# that NO exchange credential is named anywhere in it. That check is what makes
# app/safety.py's claim true of the whole artifact rather than only of the
# Python -- "an IAM policy granting access to a new secret, an `-e API_KEY=` in
# a `docker run`" are exactly the places a capability would appear.
#
# A live bot needs exactly those things. Scattering them through the shared
# files would have meant relaxing that scan over infra/ generally, which is how
# a boundary becomes a suggestion. Instead this file is the one declared
# exception, and the scan still holds -- unchanged -- over everything else.
#
# NOTHING HERE IS CREATED BY DEFAULT. var.live_stacks is empty, so every
# for_each is empty and `terraform plan` reports no additions until somebody
# deliberately adds an entry.
# ---------------------------------------------------------------------------

variable "live_stacks" {
  description = <<-EOT
    Live-trading stacks. Same shape as `stacks`, EMPTY BY DEFAULT.

    A live stack differs from a paper one in three ways, and each is why it is
    a separate map rather than a flag on the existing entries:

      * IT RUNS A DIFFERENT IMAGE. deploy/docker/Dockerfile.live carries the
        `live` package; the paper image deliberately does not, because
        app/safety.py's boundary is the ABSENCE of order-placement capability
        rather than a runtime toggle.

      * IT BOOTS WITH A DIFFERENT SCRIPT. deploy/aws/run_live.sh, because
        run.sh is embedded in the paper hosts' user_data and
        `user_data_replace_on_change = true` -- editing it replaces a running
        bot mid-experiment, through a feed gap, which for a bot that simulates
        stops from ticks means stop evaluations that did not happen.

      * IT NEEDS EXCHANGE CREDENTIALS, which Terraform must never see.

    ALONGSIDE, NOT INSTEAD. A live stack does not replace a paper one: the
    paper run is the only thing estimating whether the arm has an edge, and
    ending it to rehearse the machinery would spend the evidence to run the
    dress rehearsal.
  EOT
  type = map(object({
    variant = string
    db_name = string
  }))
  default = {}
}

variable "live_credential_secret_arn" {
  description = <<-EOT
    Secrets Manager ARN holding the venue credentials, as
    {"api_key": "...", "api_secret": "..."}.

    THE ARN, NEVER THE VALUE. Anything passed as a Terraform variable is
    written to state in plaintext -- which is precisely why the database has
    RDS generate its own credential into Secrets Manager rather than having
    Terraform generate one. Create the secret out of band, put its ARN here,
    and the host reads it at boot.

    Empty means the grant below covers no resources at all, which is the
    correct state until a live stack exists.
  EOT
  type        = string
  default     = ""
}

variable "live_venue" {
  description = "testnet | mainnet. Reaching mainnet must be a deliberate edit."
  type        = string
  default     = "testnet"

  validation {
    condition     = contains(["testnet", "mainnet"], var.live_venue)
    error_message = "live_venue must be exactly \"testnet\" or \"mainnet\"."
  }
}

# Where a live host finds the ARN. run_live.sh derives this parameter's name
# from the image-tag parameter's prefix rather than reading it from user_data,
# because adding a variable to that template changes the rendered bytes for
# PAPER hosts and replaces their instances.
resource "aws_ssm_parameter" "live_credential_arn" {
  for_each = { for k, s in local.stacks : k => s if s.live }

  name  = "${each.value.ssm_prefix}/delta_secret_arn"
  type  = "String"
  value = var.live_credential_secret_arn != "" ? var.live_credential_secret_arn : "none"
}

# A SEPARATE POLICY, NOT A STATEMENT IN THE SHARED ONE. Attached to the same
# instance role, created only when a secret is configured, and scoped to that
# one secret: the role can read the credential it trades with and no other
# secret in the account. `count` rather than a conditional Resource list, so
# that with nothing configured the policy does not exist at all.
resource "aws_iam_role_policy" "live_credentials" {
  count = var.live_credential_secret_arn != "" ? 1 : 0

  name = "${local.name}-live-credentials"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadTheVenueCredentialAndNothingElse"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.live_credential_secret_arn
    }]
  })
}
