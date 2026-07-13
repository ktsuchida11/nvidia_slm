# GPU学習ホスト用 Terraform（mra_terraform からの切り出し・縮小版）

[mra_terraform](https://github.com/ktsuchida11/mra_terraform) の **network / bastion / SSM(vpcendpoint)** を
NeMoパイプラインのGPU学習用に切り出し、**専用AWSアカウント**でg6e系EC2を立てる最小構成。

## mra_terraform からの主な変更点

| 対象 | 変更 |
|---|---|
| network | databaseサブネット・fck-nat・VPC Flow Log を除去。NAT Gateway固定（単一）。AZハードコード→データソース動的解決 |
| vpcendpoint | ssm/ssmmessages/ec2messages + logs に縮小（secretsmanager/ecr/ecs除去）。S3 Gatewayのリージョンハードコード修正 |
| bastion | **既定off**（SSMはGPUノードへ直接接続できるため踏み台不要。VPC内DBアクセス等の用途ができた時のみ`enable_bastion=true`）。有効時もAurora/ClickHouse配線は除去、inbound無し・IMDSv2は踏襲 |
| ssm(ルート) | セッション監査ログ（Session Document+CloudWatch）をbastionモジュールから独立させ、**GPUノードへのセッションも監査対象**に |
| gpu_host | **新規**。DLAMI(ドライバ/Docker/Toolkit済) + spot/オンデマンド切替 + ckpt退避S3 + SSM管理 |
| backend | 初期はローカルstate（S3移行手順は providers.tf コメント） |

## 使い方

```bash
cd infra/gpu-host
cp terraform.tfvars.example terraform.tfvars   # 値を編集（tfvarsはコミットしない）

# 専用アカウントの認証（例: SSO）
aws configure sso --profile gpu-account   # または環境変数で

# g6eの提供確認（リージョン選定）
aws ec2 describe-instance-type-offerings --region ap-northeast-1 \
  --filters Name=instance-type,Values=g6e.xlarge --profile gpu-account

terraform init
terraform plan
terraform apply
```

## 接続（SSHポート開放なし・SSMのみ）

```bash
# apply後の output に接続コマンドが出る:
aws ssm start-session --target <gpu-instance-id> \
  --document-name nvslm-dev-SessionManagerRunShell --region ap-northeast-1 --profile gpu-account

# ファイル転送はS3経由（SGは何もinboundを開けない）:
aws s3 cp data/distilled/rl/ s3://<ckpt_bucket>/data/rl/ --recursive   # ローカル側
aws s3 sync s3://$CKPT_BUCKET/data/rl/ data/distilled/rl/              # GPUノード側
```

> scp/rsyncを使いたい場合は `aws ssm start-session --document-name AWS-StartSSHSession` による
> SSH over SSM（`~/.ssh/config` の ProxyCommand設定）でも可。

## GPUノードでの学習手順

ノード上の `/opt/nvidia_slm/README.txt` に手順を配置済み（user_dataで生成）:
リポclone → `remote/setup-gpu-host.sh` → `docker login nvcr.io` → データ配置 → `make sft`

## コスト管理（重要）

| リソース | 目安 | 止め方 |
|---|---|---|
| g6e.xlarge spot | ~$0.6-0.9/h | **`gpu_instance_count = 0` にして apply**（EBSごと消える。ckptはS3退避が前提） |
| NAT Gateway | ~$0.062/h + **$0.062/GB処理** | イメージDL(数十GB/回)が多い場合 `gpu_subnet_placement="public"` でNAT課金回避 |
| VPC Interface Endpoint ×4 | ~$0.056/h (1AZ) | 常設コスト。許容できない場合はpublic配置+endpoint削除の構成変更 |
| bastion t4g.nano | ~$0.005/h | 既定off（`enable_bastion = false`） |

**使い終わったら**: 学習ジョブ完走→ckptをS3へ→`gpu_instance_count=0`でapply、
完全撤収は `terraform destroy`（ckptバケットは `force_destroy=false` のため中身があると残る=安全側）。

## セキュリティ

- 全EC2: inboundルールなし（SSM経由のみ）・IMDSv2必須・EBS暗号化
- SSMセッションはCloudWatch Logsに監査ログ（保持30日、mra踏襲）
- GPUノードのegressは全開（NGC/HF/GitHubからの取得のため）。閉域要件が強い場合は
  mra_terraform 本体の vpcendpoint(ecr等)+ドメイン制限付きプロキシの構成を検討
