output "vpc_id" { value = module.network.vpc_id }
output "nat_gateway_ip" { value = module.network.nat_gateway_ip }

output "gpu_instance_ids" { value = module.gpu_host.instance_ids }
output "gpu_ami" { value = module.gpu_host.ami_name }
output "ckpt_bucket" { value = module.gpu_host.ckpt_bucket }

output "bastion_instance_id" {
  value = var.enable_bastion ? module.bastion[0].bastion_instance_id : null
}

output "connect_gpu" {
  description = "GPUノードへのSSM接続コマンド（監査ログ付きセッション）"
  value = length(module.gpu_host.instance_ids) > 0 ? format(
    "aws ssm start-session --target %s --document-name %s --region %s",
    module.gpu_host.instance_ids[0],
    aws_ssm_document.session_manager_run_shell.name,
    var.region,
  ) : "gpu_instance_count=0（ノード停止中）"
}
