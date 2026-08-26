# ---------------------------------------------------------------------------
# ONE STACK PER CONCURRENT EXPERIMENT.
#
# Everything here is keyed by var.stacks. "v1" is the original host and keeps
# the original resource identities via the `moved` blocks at the bottom of this
# file -- without those, renaming aws_instance.bot to aws_instance.bot["v1"]
# reads to Terraform as destroy-and-create of the running bot.
#
# The instances are separate rather than one host running two containers
# because the two bots must not share a failure: an OOM, a docker restart or a
# host reboot should end one experiment, not both.
# ---------------------------------------------------------------------------

locals {
  # V1 KEEPS ITS ORIGINAL NAMES, AND THAT ASYMMETRY IS DELIBERATE.
  #
  # The obvious scheme gives every stack a /<stack>/ segment. Applied to v1
  # that renames resources which already exist and hold state, and a rename is
  # a destroy:
  #
  #   log group  /deltabt/paper/bot -> /deltabt/paper/v1/bot
  #              would DELETE the run's entire operational history. This is
  #              one of the types scripts/tf_guard.py protects, and it was
  #              caught by planning rather than by reasoning about it.
  #   SSM params /deltabt/paper/image_tag -> /deltabt/paper/v1/image_tag
  #              recreated with value "none", because bot_image_tag defaults
  #              empty and the live value is under ignore_changes. run.sh
  #              reads "none" and exits 90, so the host would come up and
  #              deliberately not start the bot.
  #
  # So the pre-existing stack keeps the paths it was created with, and only
  # NEW stacks get the segment. The name is an identity, not a description.
  legacy_stack = "v1"

  # The v1 stack's db_name is "" in the variable, meaning "whatever the RDS
  # instance was created with". Resolving it here keeps that indirection out
  # of the template and out of every consumer.
  stacks = {
    for k, s in var.stacks : k => merge(s, {
      db_name    = s.db_name != "" ? s.db_name : aws_db_instance.main.db_name
      ssm_prefix = k == local.legacy_stack ? "/deltabt/${var.environment}" : "/deltabt/${var.environment}/${k}"
      log_group  = k == local.legacy_stack ? "/deltabt/${var.environment}/bot" : "/deltabt/${var.environment}/${k}/bot"

      # Suffix for resources whose name is only an identifier (alarms, metric
      # filters, documents). Empty for the legacy stack so its resources keep
      # the names they already have and the plan stays a rename.
      suffix = k == local.legacy_stack ? "" : "-${k}"

      # The metric namespace must be per stack, or the two bots' LogLines
      # merge into one series and the "bot has gone silent" alarm cannot see
      # either of them stop. The legacy stack keeps the bare namespace:
      # moving it would strand its metric history AND, since the silent alarm
      # treats missing data as breaching, fire it until enough new datapoints
      # had accumulated to clear it.
      metric_namespace = k == local.legacy_stack ? "DeltaBt" : "DeltaBt/${k}"
    })
  }
}

# Which image the host should run. The deploy workflow writes this parameter
# and restarts the unit; the instance itself is never rebuilt to ship code.
# That is what makes a deploy take seconds and a rollback take seconds too.
resource "aws_ssm_parameter" "image_tag" {
  for_each = local.stacks

  name  = "${each.value.ssm_prefix}/image_tag"
  type  = "String"
  value = var.bot_image_tag != "" ? var.bot_image_tag : "none"

  lifecycle {
    # After the first deploy this parameter is owned by the deploy workflow,
    # not by Terraform. Without this, every `terraform apply` would silently
    # roll the running bot back to whatever the variable happened to say.
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "image_tag_previous" {
  for_each = local.stacks

  name  = "${each.value.ssm_prefix}/image_tag_previous"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_instance" "bot" {
  for_each = local.stacks

  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public[var.bot_subnet_index].id
  vpc_security_group_ids = [aws_security_group.bot.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # No key_name. There is deliberately no SSH key pair anywhere in this stack:
  # access is via SSM Session Manager, which authenticates with IAM and logs
  # to CloudTrail. A key pair would be a credential to lose.

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only; blocks SSRF credential theft
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2 # containers reach IMDS through one hop
  }

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    aws_region                   = var.aws_region
    ecr_repository_url           = aws_ecr_repository.bot.repository_url
    ecr_repository_name          = aws_ecr_repository.bot.name
    db_host                      = aws_db_instance.main.address
    db_port                      = aws_db_instance.main.port
    db_name                      = each.value.db_name
    db_secret_arn                = aws_db_instance.main.master_user_secret[0].secret_arn
    ssm_image_tag_param          = aws_ssm_parameter.image_tag[each.key].name
    ssm_image_tag_previous_param = aws_ssm_parameter.image_tag_previous[each.key].name
    log_group                    = aws_cloudwatch_log_group.bot[each.key].name
    bot_symbols                  = var.bot_symbols
    bot_variant                  = each.value.variant
    max_open_positions           = var.max_open_positions
    max_drawdown_pct             = var.max_drawdown_pct
    max_daily_loss_pct           = var.max_daily_loss_pct
    max_consecutive_losses       = var.max_consecutive_losses
    max_hold_seconds             = var.max_hold_seconds
    run_sh_b64                   = filebase64("${path.root}/../../deploy/aws/run.sh")
    deploy_sh_b64                = filebase64("${path.root}/../../deploy/aws/deploy.sh")
    cw_agent_b64                 = filebase64("${path.root}/../../deploy/aws/cloudwatch-agent.json")
  })

  # Changing user-data replaces the instance. That is correct -- a host whose
  # bootstrap script has changed but which was never re-bootstrapped is a
  # machine nobody can reproduce.
  user_data_replace_on_change = true

  # An accidental `terraform destroy` during a 30-day run ends the run. The
  # database is separately protected; this protects the bot.
  #
  # IT ALSO BLOCKS REPLACEMENT, WHICH IS WHY IT IS A VARIABLE NOW.
  # The AWS provider does not clear termination protection before destroying an
  # instance -- it fails, and `force_destroy` is the documented escape hatch.
  # Anything that lands in user-data (run.sh, deploy.sh, the symbol list)
  # forces a replacement, so with this hard-coded true those changes could
  # never be applied at all.
  #
  # It cannot be done in ONE apply either: Terraform does not update attributes
  # on a resource it is replacing, so the destroy still fails. Hence a variable
  # and two applies -- this one flips the live attribute and changes nothing
  # else; the next one carries the user-data change.
  disable_api_termination = !var.allow_instance_replacement

  tags = { Name = "${local.name}-${each.key}", Stack = each.key, Variant = each.value.variant }
}

# A stable address, so the dashboard tunnel command in the runbook does not
# change every time the instance is replaced.
resource "aws_eip" "bot" {
  for_each = local.stacks

  instance = aws_instance.bot[each.key].id
  domain   = "vpc"
  tags     = { Name = "${local.name}-${each.key}", Stack = each.key }
}

# ---------------------------------------------------------------------------
# The deploy entry point.
#
# The workflow does not get shell on the host. It can invoke exactly this
# document with exactly one parameter, and the document runs a script that
# lives in the repository under review.
# ---------------------------------------------------------------------------
resource "aws_ssm_document" "deploy" {
  for_each = local.stacks

  name            = "${local.name}${each.value.suffix}-deploy"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Deploy an image tag to the ${each.key} DeltaBot host, verify it, roll back on failure."
    parameters = {
      ImageTag = {
        type           = "String"
        description    = "Immutable image tag (the git SHA) to run."
        allowedPattern = "^[A-Za-z0-9._-]{1,128}$"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "deploy"
      inputs = {
        timeoutSeconds = "2400"
        runCommand     = ["/opt/deltabt/deploy.sh '{{ ImageTag }}'"]
      }
    }]
  })
}

# ---------------------------------------------------------------------------
# STATE MIGRATION. The original single-instance stack becomes stacks["v1"].
#
# These are renames, not replacements. Terraform would otherwise plan a destroy
# of the running bot and its address, and `disable_api_termination` would then
# fail the apply partway through.
#
# The SSM parameter paths gain a /v1/ segment, so those ARE replaced -- a
# parameter is cheap to recreate, but the deploy workflow's `vars` must be
# updated to the new paths in the same change.
# ---------------------------------------------------------------------------
moved {
  from = aws_instance.bot
  to   = aws_instance.bot["v1"]
}

moved {
  from = aws_eip.bot
  to   = aws_eip.bot["v1"]
}

moved {
  from = aws_ssm_document.deploy
  to   = aws_ssm_document.deploy["v1"]
}

moved {
  from = aws_ssm_parameter.image_tag
  to   = aws_ssm_parameter.image_tag["v1"]
}

moved {
  from = aws_ssm_parameter.image_tag_previous
  to   = aws_ssm_parameter.image_tag_previous["v1"]
}
