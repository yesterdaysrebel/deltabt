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
    minimum_rr                   = var.minimum_rr
    cooldown_after_trade_seconds = var.cooldown_after_trade_seconds
    cooldown_after_loss_seconds  = var.cooldown_after_loss_seconds
    max_hold_seconds             = var.max_hold_seconds
    exit_on_wpr_band_exit        = var.exit_on_wpr_band_exit ? 1 : 0
    wpr_exit_long_level          = var.wpr_exit_long_level
    wpr_exit_short_level         = var.wpr_exit_short_level
    run_sh_b64                   = base64gzip(file("${path.root}/../../deploy/aws/run.sh"))
    deploy_sh_b64                = base64gzip(file("${path.root}/../../deploy/aws/deploy.sh"))
    # NOTHING ELSE MAY BE EMBEDDED HERE without removing something first.
    # user_data has a 16,384-byte hard cap and the rendered template passes
    # its budget check with NINE bytes to spare (tests/live/test_user_data_size).
    # That is why the experiment lifecycle is an SSM document above -- document
    # content lives in AWS -- and why the database is created by the
    # infrastructure workflow, which owns the database anyway.
    cw_agent_b64 = base64gzip(file("${path.root}/../../deploy/aws/cloudwatch-agent.json"))
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
# ---------------------------------------------------------------------------
# THE EXPERIMENT LIFECYCLE, AS A DOCUMENT RATHER THAN A HOST SCRIPT.
#
# Retiring a run and registering its successor has to happen on the host, in
# the bot's exact environment. The obvious home is deploy.sh -- but user_data
# has a 16,384-byte hard cap and the rendered template already passes its
# budget check with NINE bytes to spare (tests/live/test_user_data_size.py).
# Anything added to a shipped script fails the apply.
#
# An SSM document's content lives in AWS, not in user_data, so it costs the
# template nothing. It also keeps the deploy role's boundary intact: the role
# may invoke NAMED documents, and this is a second named document rather than
# a grant of arbitrary shell.
#
# ORDER, WHICH IS NOT ADJUSTABLE. An experiment records the git_sha it began
# on and bind_experiment() refuses a container with a different one, so the
# old run must be stopped BEFORE the new image rolls; otherwise every deploy
# during a run trips a drift refusal, /readyz never passes, and deploy.sh
# rolls back a perfectly good image. Registering needs the service STOPPED:
# preflight's advisory-lock check fails while the bot holds the lock, and
# `forward-test start` refuses unless preflight passes. Hence stop -> roll ->
# (stop service, preflight, start, restart).
#
# WHY NOT run.sh FOR THE ONE-OFF COMMANDS: it ends in
# `docker run --name deltabot` with no "$@", so it ignores its arguments and
# starts a SECOND bot, collides on the name and restarts the unit -- silently,
# with plausible output and exit 0. The env below must MATCH the bot's, not
# approximate it: config_hash and risk_hash are computed from these variables,
# so defaults would register an experiment describing a rule nobody is running.
# ---------------------------------------------------------------------------
resource "aws_ssm_document" "experiment" {
  for_each = local.stacks

  name            = "${local.name}${each.value.suffix}-experiment"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Stop or start the forward-test experiment on the ${each.key} DeltaBot host."
    parameters = {
      Action = {
        type          = "String"
        description   = "stop | start"
        allowedValues = ["stop", "start"]
      }
      ExperimentId = {
        type           = "String"
        description    = "Experiment id to register. Ignored by 'stop'."
        default        = "none"
        allowedPattern = "^(none|[A-Za-z0-9._-]{1,128})$"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "experiment"
      inputs = {
        timeoutSeconds = "1800"
        runCommand = [<<-SH
          set -euo pipefail
          set -a; . /opt/deltabt/env; set +a
          TAG="$(aws ssm get-parameter --region "$AWS_REGION" --name "$SSM_IMAGE_TAG_PARAM" --query Parameter.Value --output text)"
          cli() {
            docker run --rm --env-file /run/deltabt/env \
              -e "DELTABOT_SYMBOLS=$DELTABOT_SYMBOLS" \
              -e "DELTABOT_VARIANT=$${DELTABOT_VARIANT:-V1}" \
              -e "DELTABOT_MAX_OPEN=$${DELTABOT_MAX_OPEN:-1}" \
              -e "DELTABOT_MAX_DRAWDOWN=$${DELTABOT_MAX_DRAWDOWN:-0.10}" \
              -e "DELTABOT_MAX_DAILY_LOSS=$${DELTABOT_MAX_DAILY_LOSS:-0.02}" \
              -e "DELTABOT_MAX_CONSEC_LOSSES=$${DELTABOT_MAX_CONSEC_LOSSES:-3}" \
              -e "DELTABOT_MAX_HOLD=$${DELTABOT_MAX_HOLD:-0}" \
              -e "DELTABOT_WPR_BAND_EXIT=$${DELTABOT_WPR_BAND_EXIT:-0}" \
              -e "DELTABOT_WPR_EXIT_LONG=$${DELTABOT_WPR_EXIT_LONG:--80}" \
              -e "DELTABOT_WPR_EXIT_SHORT=$${DELTABOT_WPR_EXIT_SHORT:--20}" \
              -e "DELTABOT_MIN_RR=$${DELTABOT_MIN_RR:-2.0}" \
              -e "DELTABOT_COOLDOWN_AFTER_TRADE=$${DELTABOT_COOLDOWN_AFTER_TRADE:-900}" \
              -e "DELTABOT_COOLDOWN_AFTER_LOSS=$${DELTABOT_COOLDOWN_AFTER_LOSS:-3600}" \
              -e TZ=UTC -e PYTHONUNBUFFERED=1 \
              "$${ECR_REPOSITORY_URL}:$${TAG}" "$@"
          }
          case "{{ Action }}" in
            stop)
              # A STACK'S FIRST ROLL HAS NOTHING TO RETIRE, AND THAT MUST NOT
              # BE AN ERROR. The deploy retires BEFORE it rolls, so on a host
              # Terraform has just created this runs before anything has ever
              # started: /run/deltabt/env is written by run.sh and does not
              # exist yet, the image tag is still "none", and the CLI would
              # exit 1 with "no experiment is RUNNING" even if both did. Under
              # `set -e` each of those fails the step, the deploy aborts, and
              # the new arm never starts -- which is how v4's first roll died
              # on 2026-08-20 and what cost an outage on 2026-09-04.
              #
              # Each guard below is a precondition only a never-deployed host
              # can fail. A genuine retire failure still fails the step.
              if [ ! -f /run/deltabt/env ]; then
                echo "[experiment] no container has ever run here; nothing to retire"
                exit 0
              fi
              if [ -z "$${TAG:-}" ] || [ "$TAG" = "none" ]; then
                echo "[experiment] no image deployed yet; nothing to retire"
                exit 0
              fi
              echo "[experiment] retiring any running experiment"
              # --reason IS REQUIRED by the CLI and its absence is not a
              # parse-time error anyone sees until the document runs: the
              # first real pipeline roll failed here with "the following
              # arguments are required: --reason", after the host had already
              # been replaced. The reason is recorded on the experiment row,
              # so it should say what superseded the run.
              set +e
              out="$(cli forward-test stop --reason "superseded by a new deploy (run {{ ExperimentId }})" 2>&1)"
              rc=$?
              set -e
              echo "$out"
              # "nothing was running" is the ONE non-zero exit that is not a
              # problem, and it is matched on the CLI's own words rather than
              # on the exit code, which it shares with every real failure.
              # tests/live/test_experiment_document_commands.py pins that this
              # string is still what app/cli.py prints.
              if [ "$rc" -ne 0 ]; then
                case "$out" in
                  *"no experiment is RUNNING"*)
                    echo "[experiment] nothing was running; continuing" ;;
                  *)
                    echo "[experiment] retire FAILED"; exit "$rc" ;;
                esac
              fi
              ;;
            start)
              ID="{{ ExperimentId }}"
              if [ "$ID" = "none" ]; then echo "[experiment] id 'none': nothing to start"; exit 0; fi
              echo "[experiment] registering $ID"
              systemctl stop deltabt.service
              sleep 5
              if ! cli forward-test preflight; then
                echo "[experiment] PREFLIGHT FAILED -- not starting"
                systemctl start deltabt.service || true
                exit 1
              fi
              if ! cli forward-test start --experiment-id "$ID" --days 30; then
                echo "[experiment] start FAILED"
                systemctl start deltabt.service || true
                exit 1
              fi
              systemctl start deltabt.service
              for _ in $(seq 1 90); do
                if curl -fsS --max-time 5 http://127.0.0.1:8000/readyz >/dev/null 2>&1; then
                  echo "[experiment] $ID running and the bot is ready"; exit 0
                fi
                sleep 10
              done
              echo "[experiment] the bot did not become ready after registering $ID"
              exit 1
              ;;
          esac
        SH
        ]
      }
    }]
  })
}

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
