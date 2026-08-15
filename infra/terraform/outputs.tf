output "instance_ids" {
  description = "Per stack. Start a session with: aws ssm start-session --target <this>"
  value       = { for k, i in aws_instance.bot : k => i.id }
}

output "instance_public_ips" {
  description = "Elastic IPs. Nothing listens on them -- the security group has no ingress."
  value       = { for k, e in aws_eip.bot : k => e.public_ip }
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

output "log_groups" {
  description = "aws logs tail <this> --follow"
  value       = { for k, g in aws_cloudwatch_log_group.bot : k => g.name }
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable."
  value       = aws_iam_role.github_app_deploy.arn
}

output "ssm_deploy_documents" {
  description = "Set as the SSM_DEPLOY_DOCUMENT_<STACK> repository variables."
  value       = { for k, d in aws_ssm_document.deploy : k => d.name }
}

output "image_tag_parameters" {
  description = "Which image each host runs. Owned by the deploy workflow, not Terraform."
  value       = { for k, p in aws_ssm_parameter.image_tag : k => p.name }
}

output "dashboard_url" {
  description = "One screen answering 'is the forward test still running properly?'"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}

output "dashboard_tunnel_commands" {
  description = <<-EOT
    Reach each bot's own dashboard with no inbound port open. The local ports
    differ so both tunnels can be open at once.
  EOT
  value = {
    for idx, k in sort(keys(aws_instance.bot)) : k =>
    "aws ssm start-session --target ${aws_instance.bot[k].id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"8000\"],\"localPortNumber\":[\"${8000 + idx}\"]}'"
  }
}

output "estimated_monthly_cost_usd" {
  description = "On-demand ap-south-1 list prices; see docs/aws_deployment.md for the breakdown."
  value       = "~45 (2x t4g.small ~24, db.t4g.micro ~13, 60GB gp3 ~6, EIPs/logs ~2)"
}
