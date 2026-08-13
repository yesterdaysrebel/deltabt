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
  default     = "BTCUSD,ETHUSD,SOLUSD,XRPUSD"
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
