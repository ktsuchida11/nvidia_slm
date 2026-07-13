variable "project" { type = string }
variable "environment" { type = string }
variable "cidr_block" { type = string }
variable "az_count" {
  type    = number
  default = 2
}
