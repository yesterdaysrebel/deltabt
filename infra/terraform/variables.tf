variable "aws_region" {
  description = "Region for every resource. Prefer one close to the exchange."
  type        = string
  default     = "ap-south-1"
}

variable "environment" {
  description = "Environment name. Part of every resource name."
  type        = string
  default     = "paper"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.environment))
    error_message = "environment must be lowercase alphanumeric with hyphens."
  }
}

variable "github_org" {
  description = "GitHub organisation or user."
  type        = string
}

variable "github_repo" {
  description = "Repository name."
  type        = string
  default     = "deltabt"
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric GitHub owner id. GitHub's immutable OIDC subject pins the owner and
    repository to their numeric ids, which a rename cannot follow. Empty falls
    back to the legacy name-based subject alone. `gh api users/<owner> --jq .id`
  EOT
  type        = string
  default     = ""
}

variable "github_repo_id" {
  description = "Numeric GitHub repository id. `gh api repos/<o>/<r> --jq .id`"
  type        = string
  default     = ""
}

# --- compute ---------------------------------------------------------------

variable "instance_type" {
  description = <<-EOT
    EC2 size. Measured from the running container: ~450 MB RSS steady and
    under 5% CPU, so t4g.small (2 vCPU / 2 GB) has comfortable headroom.
    t4g.micro (1 GB) also runs it but leaves little room for the numba cache
    and a docker build.
  EOT
  type        = string
  default     = "t4g.small"
}

variable "allow_instance_replacement" {
  description = <<-EOT
    Clears EC2 termination protection so a later apply MAY replace a bot host.

    TRUE DURING A ROLLOUT ONLY. Set back to false once the replacement has
    happened; leaving it true means a stray plan can terminate a running
    experiment, which is the thing the protection exists to prevent.

    Sequence, and it genuinely needs two applies:
      1. set true, apply -- updates the live attribute, replaces nothing
      2. apply the user-data change -- the replacement now succeeds
      3. set false again

    Terraform does not update attributes on a resource it is replacing, so
    doing 1 and 2 together leaves the destroy still blocked.
  EOT
  type        = bool
  default     = true
}

variable "root_volume_gb" {
  description = "Root EBS size. Image ~1.2 GB plus capped logs."
  type        = number
  default     = 20
}

# --- database --------------------------------------------------------------

variable "db_instance_class" {
  description = "RDS size. The bot writes a few hundred rows a minute."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "Measured ~40 MB/day at four symbols, so 20 GB is years."
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  description = <<-EOT
    Automated backup retention. The experiment database IS the deliverable of
    a 30-day run, so retention must outlive the run itself.
  EOT
  type        = number
  default     = 35
}

variable "db_deletion_protection" {
  description = "Refuse to delete the database holding the experiment."
  type        = bool
  default     = true
}

# --- container -------------------------------------------------------------

variable "ecr_repository_name" {
  description = "ECR repository for the bot image."
  type        = string
  default     = "deltabt"
}

variable "bot_image_tag" {
  description = <<-EOT
    Image tag to run. ALWAYS an immutable tag -- the git SHA -- never
    "latest". A mutable tag makes "which code produced this dataset"
    unanswerable, which is the one question a forward test must answer.
  EOT
  type        = string
  default     = ""
}

variable "bot_symbols" {
  description = "Frozen universe. Changing it changes the experiment identity."
  type        = string
  default     = "BTCUSD,ETHUSD,SOLUSD,BEATUSD,BANKUSD,AKEUSD"
}

# --- the two concurrent runs -----------------------------------------------

variable "stacks" {
  description = <<-EOT
    One entry per concurrently running experiment. Each gets its own EC2
    instance, its own database on the shared RDS instance, its own log group,
    deploy document, monitor document and alarms.

    A SEPARATE DATABASE PER STACK IS LOAD-BEARING, NOT TIDINESS. Four
    invariants make two experiments in one database impossible:
    ux_forward_test_running allows one RUNNING experiment; ux_positions_open
    _symbol allows one open position per symbol across the whole table;
    strategy_state stores risk state under the single key "risk_state"; and
    load_open_positions() takes no experiment filter, so each bot would
    recover the other's positions. Splitting the database resolves all four
    without touching code that today's audit findings were about.

    "v1" keeps the original database because that is where the open BTCUSD
    position and the run history live.
  EOT
  type = map(object({
    variant = string
    db_name = string
  }))
  default = {
    v1 = { variant = "V1", db_name = "" } # "" means the RDS default database
    v2 = { variant = "V2", db_name = "deltabt_v2" }
  }
}

variable "max_open_positions" {
  description = "Concurrent positions per experiment. Part of the risk hash."
  type        = number
  default     = 6
}

variable "max_drawdown_pct" {
  description = <<-EOT
    Peak-to-trough halt. 1.0 disables it: equity would have to reach zero.
    Disabled for the paper runs by explicit instruction on 2026-08-14. It is
    the only thing that stops losses compounding and MUST be restored before
    anything trades real capital.
  EOT
  type        = number
  default     = 1.0
}

variable "max_consecutive_losses" {
  description = <<-EOT
    Daily circuit breaker; the streak resets on the UTC day roll. 0 disables
    the gate. Note that the risk engine must SKIP the check at 0 rather than
    compare against it, since a fresh state already satisfies `losses >= 0`.
  EOT
  type        = number
  default     = 0
}

# --- access ----------------------------------------------------------------

variable "admin_cidrs" {
  description = <<-EOT
    Optional inbound CIDRs. DEFAULT IS EMPTY AND SHOULD STAY EMPTY: access is
    via SSM Session Manager, which needs no inbound rule at all. Populating
    this opens SSH and is only justified if SSM is genuinely unavailable.
  EOT
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch log retention."
  type        = number
  default     = 90
}

variable "alarm_email" {
  description = "Optional address for alarm notifications. Empty disables SNS."
  type        = string
  default     = ""
}
