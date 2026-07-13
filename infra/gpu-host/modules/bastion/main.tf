# mra_terraform modules/bastion の縮小版:
# - Aurora/ClickHouse/SecretsManagerの配線を除去（GPU学習用途では不要）
# - inbound無し=SSM経由のみ・IMDSv2必須・カスタムSession Document・セッションログ は踏襲
# - KMSはAWSマネージドキーにフォールバック（kmsモジュール非依存）
data "aws_ami" "al2023_arm64" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-kernel-*-arm64"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
}

locals {
  prefix = "${var.project}-${var.environment}"
}

# ---- IAM -------------------------------------------------------------------
resource "aws_iam_role" "bastion" {
  name = "${local.prefix}-bastion-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "bastion_ssm" {
  role       = aws_iam_role.bastion.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "bastion_session_logging" {
  name = "${local.prefix}-bastion-session-logging"
  role = aws_iam_role.bastion.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"]
      Resource = "${aws_cloudwatch_log_group.ssm_session.arn}:*"
    }]
  })
}

resource "aws_iam_instance_profile" "bastion" {
  name = "${local.prefix}-bastion-profile"
  role = aws_iam_role.bastion.name
}

# ---- SG（inbound無し。egressはSSM系エンドポイント+S3+DNSのみ） ---------------
resource "aws_security_group" "bastion" {
  name        = "${local.prefix}-bastion-sg"
  description = "Bastion (SSM only, no inbound)"
  vpc_id      = var.vpc_id
  tags        = { Name = "${local.prefix}-bastion-sg" }
}

resource "aws_vpc_security_group_egress_rule" "bastion_to_endpoints" {
  security_group_id            = aws_security_group.bastion.id
  description                  = "HTTPS to SSM/logs endpoints"
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
  referenced_security_group_id = var.endpoint_sg_id
}

resource "aws_vpc_security_group_egress_rule" "bastion_to_s3" {
  security_group_id = aws_security_group.bastion.id
  description       = "HTTPS to S3 gateway endpoint"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  prefix_list_id    = var.s3_prefix_list_id
}

resource "aws_vpc_security_group_egress_rule" "bastion_dns_udp" {
  security_group_id = aws_security_group.bastion.id
  description       = "DNS"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  cidr_ipv4         = var.vpc_cidr
}

# ---- EC2 ---------------------------------------------------------------------
resource "aws_instance" "bastion" {
  ami                    = data.aws_ami.al2023_arm64.id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.bastion.id]
  iam_instance_profile   = aws_iam_instance_profile.bastion.name

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2必須
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }

  tags = { Name = "${local.prefix}-bastion" }
}
