variable "project" {
  description = "リソース命名のプレフィックス"
  type        = string
  default     = "nvslm"
}

variable "environment" {
  description = "環境名 (dev/stg/prd等)"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWSリージョン。g6eの提供状況は `aws ec2 describe-instance-type-offerings --filters Name=instance-type,Values=g6e.xlarge` で確認"
  type        = string
  default     = "ap-northeast-1"
}

variable "aws_profile" {
  description = "専用アカウント用のAWSプロファイル名（nullなら環境変数/デフォルト認証）"
  type        = string
  default     = null
}

variable "cidr_block" {
  description = "VPC CIDR"
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "使用するAZ数（public/privateサブネット数）"
  type        = number
  default     = 2
  validation {
    condition     = var.az_count >= 1 && var.az_count <= 3
    error_message = "az_count は 1〜3。"
  }
}

variable "enable_bastion" {
  description = "SSM専用踏み台(t4g.nano)。GPUノードへはSSMで直接入れるため通常は不要（VPC内からのDBアクセス等の用途ができた時のみtrue）"
  type        = bool
  default     = false
}

# ---- GPUノード ---------------------------------------------------------------
variable "gpu_instance_type" {
  description = "GPUインスタンスタイプ（48GB=g6e.xlarge / 4xL40S=g6e.12xlarge）"
  type        = string
  default     = "g6e.xlarge"
}

variable "gpu_use_spot" {
  description = "true=スポット(~1/3コスト・中断あり) / false=オンデマンド"
  type        = bool
  default     = true
}

variable "gpu_spot_max_price" {
  description = "スポット上限価格(USD/h)。null=オンデマンド価格を上限"
  type        = string
  default     = null
}

variable "gpu_subnet_placement" {
  description = <<-EOT
    GPUノードの配置。
    private: mra_terraform同様の閉域配置（外向き通信はNAT GW経由。NATデータ処理課金
             ~$0.062/GB に注意 — NGCイメージ+モデルDLで数十GB/回になり得る）
    public : パブリックIP直付け（inbound全closeのSSM運用なら攻撃面は限定的。NAT課金なし）
  EOT
  type        = string
  default     = "private"
  validation {
    condition     = contains(["private", "public"], var.gpu_subnet_placement)
    error_message = "gpu_subnet_placement は private か public。"
  }
}

variable "gpu_root_volume_gb" {
  description = "GPUノードのrootボリューム(gp3)サイズ。NeMoイメージ+モデル+ckptで余裕を持つ"
  type        = number
  default     = 300
}

variable "gpu_instance_count" {
  description = "GPUノード数（0で全停止=コスト0。使う時だけ1にしてapply）"
  type        = number
  default     = 1
}

variable "ssm_session_retention_in_days" {
  description = "SSMセッションログのCloudWatch Logs保持日数"
  type        = number
  default     = 30
}
