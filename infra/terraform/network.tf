# NO NAT GATEWAY. It is the single most expensive thing a small stack can
# accidentally buy (~$32/month plus data processing) and this design does not
# need one:
#
#   * the BOT sits in a PUBLIC subnet with a public IP. It needs outbound
#     internet for the Delta websocket, ECR and SSM; an internet gateway
#     provides that for free. It has NO inbound rules at all.
#   * the DATABASE sits in PRIVATE subnets with no route to the internet and
#     no public IP. It is reachable only from the bot's security group.
#
# "Public subnet" describes routing, not exposure. Exposure is decided by the
# security group, and the bot's has zero ingress.

locals {
  name = "deltabt-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = local.azs[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name}-public" }
}

# RDS requires a subnet group spanning at least two availability zones even
# for a single-AZ instance, so there are two private subnets. They cost
# nothing; only the database instance does.
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.42.${count.index + 10}.0/24"
  availability_zone = local.azs[count.index]
  tags              = { Name = "${local.name}-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# The private subnets deliberately get no route table of their own: they fall
# back to the VPC's main route table, which has only the local route. No path
# to the internet, in or out.

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "bot" {
  name        = "${local.name}-bot"
  description = "Bot host. Egress only unless admin_cidrs is set."
  vpc_id      = aws_vpc.main.id

  # Outbound for: the Delta websocket and REST, ECR image pulls, SSM, and
  # CloudWatch. SSM Session Manager is entirely outbound, which is why no
  # inbound rule and no bastion is needed.
  egress {
    description = "all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-bot" }
}

# Present only if someone deliberately populates admin_cidrs. Empty by default,
# so by default this creates nothing and port 22 stays shut.
resource "aws_security_group_rule" "bot_ssh" {
  count             = length(var.admin_cidrs) > 0 ? 1 : 0
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  cidr_blocks       = var.admin_cidrs
  security_group_id = aws_security_group.bot.id
  description       = "SSH -- only because admin_cidrs was set; SSM needs none"
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "PostgreSQL. Reachable only from the bot security group."
  vpc_id      = aws_vpc.main.id

  # Source is the bot's SECURITY GROUP, not a CIDR. The rule stays correct if
  # the instance is replaced and its address changes.
  ingress {
    description     = "PostgreSQL from the bot only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.bot.id]
  }

  tags = { Name = "${local.name}-db" }
}
