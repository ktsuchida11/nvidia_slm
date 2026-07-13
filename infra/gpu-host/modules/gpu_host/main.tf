# GPU学習ノード（新規モジュール。IAM/SG/IMDSv2のパターンは mra bastion を踏襲）
# - AMI: AWS Deep Learning Base OSS GPU AMI (Ubuntu 22.04, x86_64)
#   → NVIDIAドライバ / Docker / NVIDIA Container Toolkit プリインストール
#     （nemo-pipeline/remote/setup-gpu-host.sh の手動手順が userdata 相当で済む）
# - spot/オンデマンドを use_spot で切替
# - SSM管理（inbound無し）。チェックポイント退避用S3へのRW権限つき
data "aws_ami" "dlami_gpu" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

locals {
  prefix = "${var.project}-${var.environment}"
}

# ---- チェックポイント退避用S3（spot中断前提の運用: docs/10-runbook.md） -------
resource "aws_s3_bucket" "ckpt" {
  bucket_prefix = "${local.prefix}-gpu-ckpt-"
  force_destroy = var.ckpt_bucket_force_destroy
  tags          = { Name = "${local.prefix}-gpu-ckpt" }
}

resource "aws_s3_bucket_public_access_block" "ckpt" {
  bucket                  = aws_s3_bucket.ckpt.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ckpt" {
  bucket = aws_s3_bucket.ckpt.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ---- IAM ---------------------------------------------------------------------
resource "aws_iam_role" "gpu" {
  name = "${local.prefix}-gpu-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "gpu_ssm" {
  role       = aws_iam_role.gpu.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "gpu_session_logging" {
  name = "${local.prefix}-gpu-session-logging"
  role = aws_iam_role.gpu.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogGroups", "logs:DescribeLogStreams"]
      Resource = "${var.session_log_group_arn}:*"
    }]
  })
}

resource "aws_iam_role_policy" "gpu_ckpt_rw" {
  name = "${local.prefix}-gpu-ckpt-rw"
  role = aws_iam_role.gpu.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.ckpt.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.ckpt.arn}/*"
      }
    ]
  })
}

resource "aws_iam_instance_profile" "gpu" {
  name = "${local.prefix}-gpu-profile"
  role = aws_iam_role.gpu.name
}

# ---- SG（inbound無し。egressは用途ポートのみに制限） ---------------------------
resource "aws_security_group" "gpu" {
  name        = "${local.prefix}-gpu-sg"
  description = "GPU training node (SSM only, no inbound, restricted egress)"
  vpc_id      = var.vpc_id
  tags        = { Name = "${local.prefix}-gpu-sg" }
}

resource "aws_vpc_security_group_egress_rule" "gpu_https" {
  security_group_id = aws_security_group.gpu.id
  description       = "HTTPS: NGC/HF/GitHub/PyPI/SSM/S3"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "gpu_http" {
  security_group_id = aws_security_group.gpu.id
  description       = "HTTP: Ubuntu apt repositories"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "gpu_dns_udp" {
  security_group_id = aws_security_group.gpu.id
  description       = "DNS to VPC resolver"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "udp"
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "gpu_dns_tcp" {
  security_group_id = aws_security_group.gpu.id
  description       = "DNS to VPC resolver (TCP)"
  from_port         = 53
  to_port           = 53
  ip_protocol       = "tcp"
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "gpu_ntp" {
  security_group_id = aws_security_group.gpu.id
  description       = "Amazon Time Sync"
  from_port         = 123
  to_port           = 123
  ip_protocol       = "udp"
  cidr_ipv4         = "169.254.169.123/32"
}

# ---- EC2 -----------------------------------------------------------------------
resource "aws_instance" "gpu" {
  count                       = var.instance_count
  ami                         = data.aws_ami.dlami_gpu.id
  instance_type               = var.instance_type
  subnet_id                   = var.subnet_id
  associate_public_ip_address = var.associate_public_ip
  vpc_security_group_ids      = [aws_security_group.gpu.id]
  iam_instance_profile        = aws_iam_instance_profile.gpu.name
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    ckpt_bucket = aws_s3_bucket.ckpt.bucket
  })

  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
      spot_options {
        max_price                      = var.spot_max_price
        spot_instance_type             = "persistent"
        instance_interruption_behavior = "stop" # 中断時は停止（EBS保持→再開でckptから継続）
      }
    }
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2必須
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    throughput  = 250
    encrypted   = true
  }

  tags = { Name = "${local.prefix}-gpu-${count.index}" }
}
