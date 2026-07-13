# NeMoパイプライン用 GPU学習ホスト（mra_terraform から network/bastion/SSM を切り出し・縮小）
# 依存の向き: network → vpcendpoint → (bastion, gpu_host)
module "network" {
  source      = "./modules/network"
  project     = var.project
  environment = var.environment
  cidr_block  = var.cidr_block
  az_count    = var.az_count
  # NATが要るのはGPUをprivate配置して外向き通信させる時だけ
  # （bastion/VPCエンドポイントはNAT不要。public配置ならIGW直で$45/月+データ課金を節約）
  enable_nat = var.gpu_subnet_placement == "private"
}

module "vpcendpoint" {
  source               = "./modules/vpcendpoint"
  project              = var.project
  environment          = var.environment
  vpc_id               = module.network.vpc_id
  vpc_cidr             = module.network.vpc_cidr
  private_subnet_ids   = module.network.private_subnet_ids
  private_route_tb_ids = module.network.private_route_tb_ids
}

# 任意（既定off）: SSMはGPUノードへ直接接続できるため踏み台は通常不要。
# VPC内からのDBアクセス等の用途ができた時だけ有効化する（mra_terraformの本来の用途）。
module "bastion" {
  count                 = var.enable_bastion ? 1 : 0
  source                = "./modules/bastion"
  project               = var.project
  environment           = var.environment
  vpc_id                = module.network.vpc_id
  vpc_cidr              = module.network.vpc_cidr
  subnet_ids            = module.network.private_subnet_ids
  endpoint_sg_id        = module.vpcendpoint.endpoint_sg_id
  s3_prefix_list_id     = module.vpcendpoint.s3_prefix_list_id
  session_log_group_arn = aws_cloudwatch_log_group.ssm_session.arn
}

module "gpu_host" {
  source                = "./modules/gpu_host"
  project               = var.project
  environment           = var.environment
  vpc_id                = module.network.vpc_id
  vpc_cidr              = module.network.vpc_cidr
  subnet_id             = var.gpu_subnet_placement == "public" ? module.network.public_subnet_ids[0] : module.network.private_subnet_ids[0]
  associate_public_ip   = var.gpu_subnet_placement == "public"
  instance_type         = var.gpu_instance_type
  instance_count        = var.gpu_instance_count
  use_spot              = var.gpu_use_spot
  spot_max_price        = var.gpu_spot_max_price
  root_volume_gb        = var.gpu_root_volume_gb
  session_log_group_arn = aws_cloudwatch_log_group.ssm_session.arn
}
