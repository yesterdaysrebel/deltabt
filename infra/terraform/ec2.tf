# Which image the host should run. The deploy workflow writes this parameter
# and restarts the unit; the instance itself is never rebuilt to ship code.
# That is what makes a deploy take seconds and a rollback take seconds too.
resource "aws_ssm_parameter" "image_tag" {
  name  = "/deltabt/${var.environment}/image_tag"
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
  name  = "/deltabt/${var.environment}/image_tag_previous"
  type  = "String"
  value = "none"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_instance" "bot" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
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
    db_name                      = aws_db_instance.main.db_name
    db_secret_arn                = aws_db_instance.main.master_user_secret[0].secret_arn
    ssm_image_tag_param          = aws_ssm_parameter.image_tag.name
    ssm_image_tag_previous_param = aws_ssm_parameter.image_tag_previous.name
    log_group                    = aws_cloudwatch_log_group.bot.name
    bot_symbols                  = var.bot_symbols
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
  disable_api_termination = true

  tags = { Name = local.name }
}

# A stable address, so the dashboard tunnel command in the runbook does not
# change every time the instance is replaced.
resource "aws_eip" "bot" {
  instance = aws_instance.bot.id
  domain   = "vpc"
  tags     = { Name = local.name }
}

# ---------------------------------------------------------------------------
# The deploy entry point.
#
# The workflow does not get shell on the host. It can invoke exactly this
# document with exactly one parameter, and the document runs a script that
# lives in the repository under review.
# ---------------------------------------------------------------------------
resource "aws_ssm_document" "deploy" {
  name            = "${local.name}-deploy"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Deploy an image tag to the DeltaBot host, verify it, roll back on failure."
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
