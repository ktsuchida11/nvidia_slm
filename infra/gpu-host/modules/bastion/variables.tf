variable "project" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "vpc_cidr" { type = string }
variable "subnet_ids" {
  type = list(string)
  validation {
    condition     = length(var.subnet_ids) > 0
    error_message = "subnet_ids は1つ以上。"
  }
}
variable "endpoint_sg_id" { type = string }
variable "s3_prefix_list_id" { type = string }
variable "session_log_group_arn" { type = string }
variable "instance_type" {
  type    = string
  default = "t4g.nano"
}
