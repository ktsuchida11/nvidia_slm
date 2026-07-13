# mra_terraform のパターンを踏襲（terraform >=1.12, aws >=6.5 <7, default_tags）
terraform {
  required_version = ">= 1.12.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.5.0, < 7.0.0"
    }
  }

  # 初期はローカルstate。チーム運用に移行する場合は mra_terraform 同様に
  # S3バケットを手動作成 → 以下を有効化して `terraform init -migrate-state`
  # backend "s3" {
  #   bucket       = "<project>-<env>-terraform-state"
  #   key          = "gpu-host/terraform.tfstate"
  #   region       = "ap-northeast-1"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile # 専用アカウントのプロファイル。null なら環境変数等のambient認証

  default_tags {
    tags = {
      Environment = var.environment
      Project     = var.project
    }
  }
}
