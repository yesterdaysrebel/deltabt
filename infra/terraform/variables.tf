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

    BACK TO t4g.small ON 2026-08-19, after a detour. AWS ran out of the whole
    t4g pool in ap-south-1a for over three hours that morning -- t4g.small and
    t4g.MEDIUM failed identically, so it was never about sizing -- and
    m6g.medium was taken purely because it had capacity. That was a workaround
    for an outage, not a decision about what this workload needs, and it cost
    roughly $10/month more for headroom nothing uses.

    The durable fix was a public subnet in a second AZ, not a bigger instance:
    see bot_subnet_index. With somewhere to fall back to, the cheap shape is
    the right default again.

    IT IS PINNED HERE RATHER THAN PASSED AS -var ON PURPOSE. A command-line
    override leaves the committed default disagreeing with reality, and the
    next apply from CI would "correct" it -- replacing the instance and ending
    the experiment as a side effect of a routine plan.
  EOT
  type        = string
  default     = "t4g.small"
}

variable "bot_subnet_index" {
  description = <<-EOT
    Which public subnet -- and therefore which availability zone -- the bot
    runs in. 0 is the first AZ, 1 the second.

    THIS IS THE CAPACITY ESCAPE HATCH. On 2026-08-19 AWS had no t4g capacity in
    the first AZ for over three hours and the bot stayed down, because one
    public subnet existed and a host cannot run in a private one without a NAT.
    Every RunInstances error said the same thing: capacity is available in
    another zone.

    Changing this REPLACES the instance, so it needs allow_instance_replacement
    true for that apply. It does not move the database: RDS is single-AZ with
    its own subnet group, and the bot reaches it across the VPC either way.

    NOW 1 (the second AZ). t4g.small was still unavailable in the first AZ five
    hours after the outage began -- StartInstances itself returned
    InsufficientInstanceCapacity -- and the same shape launched in the second
    in 14 seconds. Committed rather than passed as -var so a CI apply cannot
    quietly move the bot back into the zone that has no capacity.
  EOT
  type        = number
  default     = 1

  validation {
    condition     = var.bot_subnet_index >= 0 && var.bot_subnet_index <= 1
    error_message = "bot_subnet_index must be 0 or 1; only two public subnets exist."
  }
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

    BACK TO FALSE on 2026-08-19. It was flipped true on 2026-08-17 to let a
    user-data change reach the hosts, and then left true through a rollout
    that failed -- so instance termination stayed enabled for two days,
    including through a four-hour outage where a stray apply could have
    destroyed the surviving bot. Flip it deliberately, roll, flip it back.
  EOT
  type        = bool
  default     = false
}

variable "max_hold_seconds" {
  description = <<-EOT
    Close a position that has been open this long, at market, whatever it is
    doing. 0 disables it, and 0 IS THE CURRENT CONFIGURED VALUE -- this
    variable exists so the value can reach the container at all, which it
    previously could not.

    b63e365 shipped the code for a 24-hour time stop and called itself "Apply
    1 of 2". Apply 2 never landed: DELTABOT_MAX_HOLD existed only in the
    settings override table, and appeared in neither user_data.sh.tftpl nor
    the -e list in run.sh, so max_hold_seconds was 0 in every container
    regardless of intent.

    NOW 86400 (24h), because the ATR arm this stack runs is specified with a
    24-hour time exit. It was 0 while the plumbing was staged out.

    IT IS PART OF RiskConfig, SO IT IS PART OF THE RISK HASH. The experiment
    running here records f9a34a4b27a35684, which is that config WITH the 24h
    hold and max_trades_per_day at 20; it was 89f939adcd0a8567 while the cap
    was 6. The same config at hold 0 hashes differently again. So changing
    this value makes
    a running bot raise ConfigurationDrift and refuse to continue -- the
    fail-closed behaviour working, not a fault. Changing it STARTS A NEW
    EXPERIMENT and cannot be applied to one already running.
  EOT
  type        = number
  default     = 86400
}

# --- access ----------------------------------------------------------------

variable "ami_id" {
  description = <<-EOT
    The AMI the bots run on, PINNED.

    data.aws_ami.al2023 uses most_recent = true, so the moment Amazon
    publishes a new al2023-*-arm64 image the lookup moves and `ami` forces
    replacement of every instance. On 2026-08-19 that was already true --
    a plan with NO configuration change wanted to replace all three running
    bots (ami-00b0a08d4568c22e8 -> ami-066a2d1dff4d3bfa5), which would have
    ended three 30-day forward tests as a side effect of an unrelated apply.

    Empty string falls back to the most_recent lookup. Bump this deliberately
    when you intend to re-bootstrap, never by accident.
  EOT
  type        = string
  default     = "ami-00b0a08d4568c22e8"
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
    # V1's rules with max_stop_pct at 10% instead of 5%. Added 2026-08-15
    # because AKEUSD and BEATUSD had 15 setups refused for stop width out of
    # 15 -- see V3_WIDE_STOP in app/config/variants.py for the measurement and
    # for why 10% rather than 25%.
    # Runs the ATR arm, not V3's rules: 2 x ATR(10) stop, a 2R target derived
    # from it, no ADX threshold, and 1m confirmation on Supertrend + Williams
    # %R instead of ADX/DI. The stack keeps the name "v3" because renaming it
    # would destroy and recreate its database, log group and documents; the
    # VARIANT is what selects the rules.
    v3 = { variant = "V4", db_name = "deltabt_v3" }
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
