resource "aws_ecr_repository" "bot" {
  name                 = var.ecr_repository_name
  force_delete         = false
  image_tag_mutability = "IMMUTABLE"

  # Immutable tags matter more here than usual: every image is tagged with its
  # git SHA and the forward-test record ties results to that SHA. If a tag
  # could be overwritten, "which code produced this dataset" would have no
  # reliable answer.

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "bot" {
  repository = aws_ecr_repository.bot.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 20 images; a rollback never needs more."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}
