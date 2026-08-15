resource "aws_cloudwatch_log_group" "bot" {
  for_each = local.stacks

  name              = each.value.log_group
  retention_in_days = var.log_retention_days
}

moved {
  from = aws_cloudwatch_log_group.bot
  to   = aws_cloudwatch_log_group.bot["v1"]
}

# Notifications are optional because they need an address, which is a decision
# rather than a default. With no address the alarms still exist and are still
# visible in the console -- they simply do not page anyone.
resource "aws_sns_topic" "alarms" {
  count = var.alarm_email != "" ? 1 : 0
  name  = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "alarms_email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alarm_actions = var.alarm_email != "" ? [aws_sns_topic.alarms[0].arn] : []
}

# ---------------------------------------------------------------------------
# Metrics derived from the bot's own structured logs.
# ---------------------------------------------------------------------------

# THE MOST IMPORTANT ALARM IN THIS FILE.
#
# Every other signal here reports a bot that is running badly. This one reports
# a bot that has stopped saying anything at all -- which is the failure mode
# that actually cost us a run: the process alive, the socket open, and the
# evaluation loop dead inside its own error handler. Error-count alarms are
# silent for exactly that failure, because a dead loop logs no errors.
resource "aws_cloudwatch_log_metric_filter" "heartbeat" {
  for_each = local.stacks

  name           = "${local.name}${each.value.suffix}-log-lines"
  log_group_name = aws_cloudwatch_log_group.bot[each.key].name
  pattern        = "{ $.level = * }"

  metric_transformation {
    name      = "LogLines"
    namespace = each.value.metric_namespace
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "silent" {
  for_each = local.stacks

  alarm_name          = "${local.name}${each.value.suffix}-bot-silent"
  alarm_description   = "The bot has logged nothing for 15 minutes. It evaluates every symbol every bar, so silence means it is not running."
  namespace           = each.value.metric_namespace
  metric_name         = "LogLines"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"

  # No datapoints at all IS the alarm condition. The default ("missing") would
  # leave this alarm permanently green for a bot that never starts.
  treat_missing_data = "breaching"

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

resource "aws_cloudwatch_log_metric_filter" "critical" {
  for_each = local.stacks

  name           = "${local.name}${each.value.suffix}-critical"
  log_group_name = aws_cloudwatch_log_group.bot[each.key].name
  pattern        = "{ $.level = \"CRITICAL\" }"

  metric_transformation {
    name          = "CriticalEvents"
    namespace     = each.value.metric_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "critical" {
  for_each = local.stacks

  alarm_name          = "${local.name}${each.value.suffix}-critical-events"
  alarm_description   = "CRITICAL logged. In this codebase that means configuration drift, a lost advisory lock, or a refusal to start."
  namespace           = each.value.metric_namespace
  metric_name         = "CriticalEvents"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_log_metric_filter" "errors" {
  for_each = local.stacks

  name           = "${local.name}${each.value.suffix}-errors"
  log_group_name = aws_cloudwatch_log_group.bot[each.key].name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name          = "ErrorEvents"
    namespace     = each.value.metric_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  for_each = local.stacks

  alarm_name          = "${local.name}${each.value.suffix}-error-rate"
  alarm_description   = "Sustained errors. A handful during a reconnect is normal; 20 in five minutes is not."
  namespace           = each.value.metric_namespace
  metric_name         = "ErrorEvents"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 20
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# Restart counting. The bot logs "starting" exactly once per process start, so
# counting that line counts restarts -- including ones systemd performed while
# nobody was watching. A bot that restarts every few minutes reports healthy
# between restarts and would otherwise be invisible.
resource "aws_cloudwatch_log_metric_filter" "starts" {
  for_each = local.stacks

  name           = "${local.name}${each.value.suffix}-starts"
  log_group_name = aws_cloudwatch_log_group.bot[each.key].name
  pattern        = "{ $.message = \"starting\" }"

  metric_transformation {
    name          = "BotStarts"
    namespace     = each.value.metric_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "restart_loop" {
  for_each = local.stacks

  alarm_name          = "${local.name}${each.value.suffix}-restart-loop"
  alarm_description   = "The bot started more than three times in half an hour. A deploy is one; four is a crash loop."
  namespace           = each.value.metric_namespace
  metric_name         = "BotStarts"
  statistic           = "Sum"
  period              = 1800
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# ---------------------------------------------------------------------------
# Host and database
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "instance_status" {
  for_each = local.stacks

  alarm_name          = "${local.name}${each.value.suffix}-instance-status"
  alarm_description   = "EC2 or hypervisor status check failing."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.bot[each.key].id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "disk" {
  for_each = local.stacks

  alarm_name          = "${local.name}${each.value.suffix}-disk"
  alarm_description   = "Root volume filling. Docker images and logs are the usual cause."
  namespace           = "DeltaBt"
  metric_name         = "DiskUsedPercent"
  dimensions          = { InstanceId = aws_instance.bot[each.key].id }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "memory" {
  for_each = local.stacks

  alarm_name          = "${local.name}${each.value.suffix}-memory"
  alarm_description   = "Host memory pressure. Steady state is ~450 MB, so this is not normal."
  namespace           = "DeltaBt"
  metric_name         = "MemoryUsedPercent"
  dimensions          = { InstanceId = aws_instance.bot[each.key].id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 90
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# NOTE ON THE DIMENSION BELOW, because it was wrong and silent.
#
# `aws_db_instance.main.id` is the DbiResourceId (db-XVL3ETI3UJ3P4AOS23YJB63UEM),
# NOT the identifier. CloudWatch publishes AWS/RDS metrics under
# DBInstanceIdentifier = deltabt-paper, so an alarm built from `.id` watches a
# dimension that has never had a datapoint.
#
# The failure was asymmetric and the quiet half was the dangerous one:
# db-no-connections treats missing data as breaching, so it sat in ALARM and
# was noticed; db-cpu and db-storage treat it as NOT breaching, so they sat in
# OK and would never have fired -- a filling disk or a pegged CPU during a
# 30-day run would have raised nothing at all.
#
# Use `.identifier`. tests/live/test_deployment_safety.py asserts it, and
# scripts/aws_preflight.py now checks at runtime that every alarm's configured
# dimensions actually resolve to a metric that has data.
resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${local.name}-db-storage"
  alarm_description   = "Database free storage below 2 GB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 2 * 1024 * 1024 * 1024
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# "Database unavailable" has no metric of its own. This is the closest honest
# proxy: the bot holds a connection pool open continuously, so zero connections
# means either the database is gone or the bot is. Both need attention, and
# between this and the silence alarm the two cases are distinguishable.
resource "aws_cloudwatch_metric_alarm" "db_unavailable" {
  alarm_name          = "${local.name}-db-no-connections"
  alarm_description   = "No connections to the database for 15 minutes. Either PostgreSQL is unavailable or the bot is not running."
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${local.name}-db-cpu"
  alarm_description   = "Database CPU sustained high. The bot writes a few hundred rows a minute, so this indicates a query problem."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

# ---------------------------------------------------------------------------
# One screen to answer "is the forward test still running properly?"
#
# One dashboard, not one per stack: the question is about the experiment as a
# whole, and two dashboards would mean noticing a dead bot depends on which tab
# you happened to open. Each stack contributes its own row of widgets; the
# database is shared and appears once.
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name

  dashboard_body = jsonencode({
    widgets = concat(
      flatten([
        for idx, k in sort(keys(local.stacks)) : [
          {
            type = "metric", x = 0, y = idx * 6, width = 12, height = 6
            properties = {
              title  = "${k} (${local.stacks[k].variant}) output -- silence means it is not running"
              region = var.aws_region
              period = 300
              stat   = "Sum"
              metrics = [
                ["DeltaBt/${k}", "LogLines"],
                [".", "ErrorEvents"],
                [".", "CriticalEvents"],
              ]
            }
          },
          {
            type = "metric", x = 12, y = idx * 6, width = 12, height = 6
            properties = {
              title  = "${k} host"
              region = var.aws_region
              period = 300
              metrics = [
                ["DeltaBt", "MemoryUsedPercent", "InstanceId", aws_instance.bot[k].id],
                [".", "DiskUsedPercent", ".", "."],
                ["AWS/EC2", "CPUUtilization", ".", "."],
              ]
            }
          },
        ]
      ]),
      [
        {
          type = "metric", x = 0, y = length(local.stacks) * 6, width = 12, height = 6
          properties = {
            title  = "Database (shared)"
            region = var.aws_region
            period = 300
            metrics = [
              ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.identifier],
              [".", "DatabaseConnections", ".", "."],
              [".", "FreeStorageSpace", ".", "."],
            ]
          }
        },
        {
          type = "log", x = 12, y = length(local.stacks) * 6, width = 12, height = 6
          properties = {
            title  = "Recent incidents, both runs"
            region = var.aws_region
            query = join(" | ", concat(
              [join(", ", [for g in aws_cloudwatch_log_group.bot : "SOURCE '${g.name}'"])],
              ["fields @timestamp, level, logger, message",
                "filter level in ['ERROR','CRITICAL']",
              "sort @timestamp desc", "limit 50"],
            ))
            view = "table"
          }
        },
      ],
    )
  })
}

# ---------------------------------------------------------------------------
# STATE MIGRATION for the metric filters and alarms.
#
# The legacy stack keeps every one of these names (suffix is ""), so without
# these blocks Terraform plans a delete of the unkeyed resource and a create of
# the keyed one AT THE SAME NAME. PutMetricAlarm and PutMetricFilter are
# upserts, so whether that ends with an alarm or with nothing depends on the
# order Terraform happens to choose. A rename has no such ambiguity.
# ---------------------------------------------------------------------------

moved {
  from = aws_cloudwatch_log_metric_filter.heartbeat
  to   = aws_cloudwatch_log_metric_filter.heartbeat["v1"]
}

moved {
  from = aws_cloudwatch_log_metric_filter.critical
  to   = aws_cloudwatch_log_metric_filter.critical["v1"]
}

moved {
  from = aws_cloudwatch_log_metric_filter.errors
  to   = aws_cloudwatch_log_metric_filter.errors["v1"]
}

moved {
  from = aws_cloudwatch_log_metric_filter.starts
  to   = aws_cloudwatch_log_metric_filter.starts["v1"]
}

moved {
  from = aws_cloudwatch_metric_alarm.silent
  to   = aws_cloudwatch_metric_alarm.silent["v1"]
}

moved {
  from = aws_cloudwatch_metric_alarm.critical
  to   = aws_cloudwatch_metric_alarm.critical["v1"]
}

moved {
  from = aws_cloudwatch_metric_alarm.errors
  to   = aws_cloudwatch_metric_alarm.errors["v1"]
}

moved {
  from = aws_cloudwatch_metric_alarm.restart_loop
  to   = aws_cloudwatch_metric_alarm.restart_loop["v1"]
}

moved {
  from = aws_cloudwatch_metric_alarm.instance_status
  to   = aws_cloudwatch_metric_alarm.instance_status["v1"]
}

moved {
  from = aws_cloudwatch_metric_alarm.disk
  to   = aws_cloudwatch_metric_alarm.disk["v1"]
}

moved {
  from = aws_cloudwatch_metric_alarm.memory
  to   = aws_cloudwatch_metric_alarm.memory["v1"]
}
