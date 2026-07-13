# mra_terraform modules/vpcendpoint の縮小版:
# - SSM接続の最小要件 ssm/ssmmessages/ec2messages + セッションログ用 logs に絞る
#   (secretsmanager/ecr/ecs は除去。必要になったら services に追加)
# - S3 Gatewayのリージョンハードコードを動的解決に修正
data "aws_region" "current" {}

locals {
  prefix             = "${var.project}-${var.environment}"
  interface_services = ["ssm", "ssmmessages", "ec2messages", "logs"]
  endpoint_subnets   = slice(var.private_subnet_ids, 0, var.vpc_endpoint_az_count)
}

resource "aws_security_group" "endpoint" {
  name        = "${local.prefix}-vpc-endpoint-sg"
  description = "VPC interface endpoints (SSM/logs)"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = { Name = "${local.prefix}-vpc-endpoint-sg" }
}

resource "aws_vpc_endpoint" "interface" {
  count               = length(local.interface_services)
  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.${local.interface_services[count.index]}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.endpoint_subnets
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = true
  tags                = { Name = "${local.prefix}-${local.interface_services[count.index]}-endpoint" }
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.private_route_tb_ids
  tags              = { Name = "${local.prefix}-s3-endpoint" }
}
