variable "project" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }
variable "subnet_id" { type = string }
variable "session_log_group_arn" { type = string }
variable "associate_public_ip" {
  type    = bool
  default = false
}
variable "instance_type" {
  type    = string
  default = "g6e.xlarge"
}
variable "instance_count" {
  type    = number
  default = 1
}
variable "use_spot" {
  type    = bool
  default = true
}
variable "spot_max_price" {
  type    = string
  default = null
}
variable "root_volume_gb" {
  type    = number
  default = 300
}
variable "ckpt_bucket_force_destroy" {
  description = "destroy時にckptバケットを中身ごと消すか（検証用途はtrueが便利）"
  type        = bool
  default     = false
}
