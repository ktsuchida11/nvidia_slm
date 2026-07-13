variable "project" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "vpc_cidr" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "private_route_tb_ids" { type = list(string) }
variable "vpc_endpoint_az_count" {
  description = "エンドポイントENIを置くAZ数（1で課金最小・mra devと同じ）"
  type        = number
  default     = 1
}
