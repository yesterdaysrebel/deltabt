# ---------------------------------------------------------------------------
# The daily read-only monitor.
#
# A scheduled GitHub Actions run needs to look at the experiment every morning.
# Neither existing role is right for that:
#
#   deltabt-github-deploy            can manage all infrastructure
#   deltabt-paper-github-app-deploy  can invoke the DEPLOY document
#
# A job that only reports should be able to do neither. So it gets its own
# role, and its SendCommand is scoped to the document below rather than to
# AWS-RunShellScript -- a workflow that can run arbitrary shell on the bot host
# can do anything to the experiment, which is the same objection that moved the
# deploy verification out of the workflow in the first place.
#
# The probe is INLINE here rather than a file on the host. Adding it to
# user-data would change user_data, and `user_data_replace_on_change = true`
# would replace the instance -- ending a running 30-day experiment to install a
# monitoring script.
# ---------------------------------------------------------------------------

resource "aws_ssm_document" "monitor" {
  for_each = local.stacks

  name            = "${local.name}${each.value.suffix}-monitor"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Read-only daily probe of the paper forward test. Changes nothing."
    parameters = {
      Day = {
        type           = "String"
        description    = "UTC date to report on, YYYY-MM-DD."
        allowedPattern = "^\\d{4}-\\d{2}-\\d{2}$"
        default        = "1970-01-01"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "probe"
      inputs = {
        timeoutSeconds = "600"
        runCommand = [
          "set +e",
          "echo '===CONTAINER==='",
          "docker ps --filter name=deltabot --format '{{.Image}}|{{.Status}}'",
          # started= lets the report tell a live fault from the shutdown noise of
          # a container that no longer exists. Without it every restart looks
          # like an incident the next morning.
          "docker inspect deltabot -f 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} restarts={{.RestartCount}} started={{.State.StartedAt}}'",
          "echo \"systemd_restarts=$(systemctl show deltabt -p NRestarts --value)\"",
          "echo '===HEALTHZ==='",
          "curl -sS --max-time 10 http://127.0.0.1:8000/healthz",
          "echo",
          "echo '===READYZ==='",
          "curl -sS --max-time 10 http://127.0.0.1:8000/readyz",
          "echo",
          "echo '===STATUS==='",
          "curl -sS --max-time 10 http://127.0.0.1:8000/api/status",
          "echo",
          "echo '===RISK==='",
          "curl -sS --max-time 10 http://127.0.0.1:8000/api/risk",
          "echo",
          "echo '===POSITIONS==='",
          "curl -sS --max-time 10 http://127.0.0.1:8000/api/positions",
          "echo",
          "echo '===TRADES==='",
          "curl -sS --max-time 10 http://127.0.0.1:8000/api/trades",
          "echo",
          "echo '===EXPERIMENT==='",
          "docker exec deltabot python -m app forward-test status 2>&1 | head -40",
          "echo '===DAILYREPORT==='",
          "docker exec deltabot python -m app forward-test report --day '{{ Day }}' 2>&1 | head -80",
          "echo '===PERSISTENCE==='",
          # Read-only SQL, base64 so no quoting survives a trip through YAML.
          "echo '${base64encode(file("${path.root}/../../deploy/aws/db_probe.py"))}' | base64 -d > /tmp/db_probe.py",
          "docker exec -i deltabot python - < /tmp/db_probe.py 2>&1 | tail -5",
          "echo '===END==='",
        ]
      }
    }]
  })
}

resource "aws_iam_role" "github_monitor" {
  name        = "${local.name}-github-monitor"
  description = "Daily read-only report. Cannot deploy, cannot change infrastructure."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        # A scheduled run fires on the default branch, so the branch subject is
        # the one that matters here. Both spellings, each pinned exactly --
        # see the immutable-subject note in bootstrap/main.tf.
        "ForAnyValue:StringLike" = {
          "token.actions.githubusercontent.com:sub" = local.github_subjects
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_monitor" {
  name = "${local.name}-github-monitor"
  role = aws_iam_role.github_monitor.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadTheInfrastructureItReportsOn"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "ec2:DescribeSecurityGroups",
          "rds:DescribeDBInstances",
          "ecr:DescribeImages",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:GetMetricStatistics",
          "ssm:DescribeInstanceInformation",
        ]
        Resource = "*" # every action here is a read that cannot be resource-scoped
      },
      {
        Sid      = "ReadTheBotLog"
        Effect   = "Allow"
        Action   = ["logs:FilterLogEvents", "logs:DescribeLogStreams", "logs:GetLogEvents"]
        Resource = [for g in aws_cloudwatch_log_group.bot : "${g.arn}:*"]
      },
      {
        Sid    = "RunTheREADONLYProbeDocumentOnly"
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        # NOT AWS-RunShellScript, and NOT the deploy document. This role can
        # run exactly one fixed, read-only script on exactly one instance.
        Resource = concat(
          [for d in aws_ssm_document.monitor : d.arn],
          [for i in aws_instance.bot :
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${i.id}"],
        )
      },
      {
        Sid      = "ReadBackTheResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"]
        Resource = "*"
      },
    ]
  })
}

output "github_monitor_role_arn" {
  description = "Set as the AWS_MONITOR_ROLE_ARN repository variable."
  value       = aws_iam_role.github_monitor.arn
}

output "ssm_monitor_documents" {
  description = "Set as the SSM_MONITOR_DOCUMENT_<STACK> repository variables."
  value       = { for k, d in aws_ssm_document.monitor : k => d.name }
}

moved {
  from = aws_ssm_document.monitor
  to   = aws_ssm_document.monitor["v1"]
}
