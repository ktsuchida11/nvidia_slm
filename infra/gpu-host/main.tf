# NeMoパイプライン用 GPU学習ホスト（mra_terraform から network/bastion/SSM を切り出し・縮小）
# 依存の向き: network → vpcendpoint → (bastion, gpu_host)
module "network" {
  source      = "./modules/network"
  project     = var.project
  environment = var.environment
  cidr_block  = var.cidr_block
  az_count    = var.az_count
  # NATが要るのは「GPUをprivate配置」かつ「ノードが1台以上」の時だけ
  # （bastion/VPCエンドポイントはNAT不要。gpu_instance_count=0 ならNAT時間課金も止まる）
  enable_nat = var.gpu_subnet_placement == "private" && var.gpu_instance_count > 0
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
  source = "./modules/gpu_host"
  # user_dataが起動直後に外向き通信する(apt/clone/pull)ため、NATルート整備を待つ
  depends_on            = [module.network]
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
