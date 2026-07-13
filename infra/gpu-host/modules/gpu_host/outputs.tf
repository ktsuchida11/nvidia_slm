output "instance_ids" { value = aws_instance.gpu[*].id }
output "security_group_id" { value = aws_security_group.gpu.id }
output "ckpt_bucket" { value = aws_s3_bucket.ckpt.bucket }
output "ami_id" { value = data.aws_ami.dlami_gpu.id }
output "ami_name" { value = data.aws_ami.dlami_gpu.name }
