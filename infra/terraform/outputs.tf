output "instance_id" {
  description = "Start a session with: aws ssm start-session --target <this>"
  value       = aws_instance.bot.id
}

output "instance_public_ip" {
  description = "Elastic IP. Nothing listens on it -- the security group has no ingress."
  value       = aws_eip.bot.public_ip
}

output "ecr_repository_url" {
  description = "Push target for the deploy workflow."
  value       = aws_ecr_repository.bot.repository_url
}

output "db_endpoint" {
  description = "Private address. Not reachable from outside the VPC."
  value       = aws_db_instance.main.address
}

output "db_secret_arn" {
  description = <<-EOT
    Secrets Manager ARN of the RDS-managed master password. The value is never
    in Terraform state and never in this repository. Read it with:
      aws secretsmanager get-secret-value --secret-id <this>
  EOT
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "log_group" {
  description = "aws logs tail <this> --follow"
  value       = aws_cloudwatch_log_group.bot.name
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable."
  value       = aws_iam_role.github_app_deploy.arn
}

output "ssm_deploy_document" {
  description = "Set as the SSM_DEPLOY_DOCUMENT repository variable."
  value       = aws_ssm_document.deploy.name
}

output "image_tag_parameter" {
  description = "Which image the host runs. Owned by the deploy workflow, not Terraform."
  value       = aws_ssm_parameter.image_tag.name
}

output "dashboard_url" {
  description = "One screen answering 'is the forward test still running properly?'"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "dashboard_tunnel_command" {
  description = "Reach the bot's own dashboard with no inbound port open."
  value       = "aws ssm start-session --target ${aws_instance.bot.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"8000\"],\"localPortNumber\":[\"8000\"]}'"
}

output "estimated_monthly_cost_usd" {
  description = "On-demand ap-south-1 list prices; see docs/aws_deployment.md for the breakdown."
  value       = "~30 (t4g.small ~12, db.t4g.micro ~13, 40GB gp3 ~4, EIP/logs ~2)"
}
