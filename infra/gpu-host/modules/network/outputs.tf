output "vpc_id" { value = aws_vpc.this.id }
output "vpc_cidr" { value = aws_vpc.this.cidr_block }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "private_route_tb_ids" { value = aws_route_table.private[*].id }
output "nat_gateway_ip" { value = var.enable_nat ? aws_eip.nat[0].public_ip : null }
