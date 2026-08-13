resource "aws_cloudwatch_log_group" "bot" {
  name              = "/deltabt/${var.environment}/bot"
  retention_in_days = var.log_retention_days
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
  name           = "${local.name}-log-lines"
  log_group_name = aws_cloudwatch_log_group.bot.name
  pattern        = "{ $.level = * }"

  metric_transformation {
    name      = "LogLines"
    namespace = "DeltaBt"
    value     = "1"
    unit      = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "silent" {
  alarm_name          = "${local.name}-bot-silent"
  alarm_description   = "The bot has logged nothing for 15 minutes. It evaluates every symbol every bar, so silence means it is not running."
  namespace           = "DeltaBt"
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
  name           = "${local.name}-critical"
  log_group_name = aws_cloudwatch_log_group.bot.name
  pattern        = "{ $.level = \"CRITICAL\" }"

  metric_transformation {
    name          = "CriticalEvents"
    namespace     = "DeltaBt"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "critical" {
  alarm_name          = "${local.name}-critical-events"
  alarm_description   = "CRITICAL logged. In this codebase that means configuration drift, a lost advisory lock, or a refusal to start."
  namespace           = "DeltaBt"
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
  name           = "${local.name}-errors"
  log_group_name = aws_cloudwatch_log_group.bot.name
  pattern        = "{ $.level = \"ERROR\" }"

  metric_transformation {
    name          = "ErrorEvents"
    namespace     = "DeltaBt"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${local.name}-error-rate"
  alarm_description   = "Sustained errors. A handful during a reconnect is normal; 20 in five minutes is not."
  namespace           = "DeltaBt"
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
  name           = "${local.name}-starts"
  log_group_name = aws_cloudwatch_log_group.bot.name
  pattern        = "{ $.message = \"starting\" }"

  metric_transformation {
    name          = "BotStarts"
    namespace     = "DeltaBt"
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "restart_loop" {
  alarm_name          = "${local.name}-restart-loop"
  alarm_description   = "The bot started more than three times in half an hour. A deploy is one; four is a crash loop."
  namespace           = "DeltaBt"
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
  alarm_name          = "${local.name}-instance-status"
  alarm_description   = "EC2 or hypervisor status check failing."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.bot.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "disk" {
  alarm_name          = "${local.name}-disk"
  alarm_description   = "Root volume filling. Docker images and logs are the usual cause."
  namespace           = "DeltaBt"
  metric_name         = "DiskUsedPercent"
  dimensions          = { InstanceId = aws_instance.bot.id }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "memory" {
  alarm_name          = "${local.name}-memory"
  alarm_description   = "Host memory pressure. Steady state is ~450 MB, so this is not normal."
  namespace           = "DeltaBt"
  metric_name         = "MemoryUsedPercent"
  dimensions          = { InstanceId = aws_instance.bot.id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 90
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${local.name}-db-storage"
  alarm_description   = "Database free storage below 2 GB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.id }
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
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.id }
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
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.id }
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
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Bot output (silence means it is not running)"
          region = var.aws_region
          period = 300
          stat   = "Sum"
          metrics = [
            ["DeltaBt", "LogLines"],
            [".", "ErrorEvents"],
            [".", "CriticalEvents"],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Host"
          region = var.aws_region
          period = 300
          metrics = [
            ["DeltaBt", "MemoryUsedPercent", "InstanceId", aws_instance.bot.id],
            [".", "DiskUsedPercent", ".", "."],
            ["AWS/EC2", "CPUUtilization", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "Database"
          region = var.aws_region
          period = 300
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.id],
            [".", "DatabaseConnections", ".", "."],
            [".", "FreeStorageSpace", ".", "."],
          ]
        }
      },
      {
        type = "log", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Recent incidents"
          region = var.aws_region
          query  = "SOURCE '${aws_cloudwatch_log_group.bot.name}' | fields @timestamp, level, logger, message | filter level in ['ERROR','CRITICAL'] | sort @timestamp desc | limit 50"
          view   = "table"
        }
      },
    ]
  })
}
