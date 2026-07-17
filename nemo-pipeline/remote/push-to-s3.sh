#!/usr/bin/env bash
# Mac側で実行: 学習データ・リポジトリを ckpt バケットへ push する
# （GPUノード側の取得手順は /opt/nvidia_slm/README.txt = docs/10-runbook.md Step 5-C）
#
# 使い方:
#   cd nvidia_slm/nemo-pipeline
#   bash remote/push-to-s3.sh              # data/distilled/ のみ（通常はこれで十分）
#   bash remote/push-to-s3.sh --with-repo  # リポジトリ本体も push（GPU側でgit cloneしない場合）
#
# バケット名は infra/gpu-host の terraform output から自動取得。
# terraform 未applyの場合は CKPT_BUCKET 環境変数で指定する。
set -euo pipefail

PIPELINE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$PIPELINE_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra/gpu-host"
PROFILE="${AWS_PROFILE:-gpu-account}"

if [ -z "${CKPT_BUCKET:-}" ]; then
  CKPT_BUCKET="$(terraform -chdir="$INFRA_DIR" output -raw ckpt_bucket 2>/dev/null || true)"
fi
if [ -z "$CKPT_BUCKET" ]; then
  echo "ERROR: バケット名を取得できません。terraform apply 済みか確認するか、" >&2
  echo "       CKPT_BUCKET=<bucket> を指定してください。" >&2
  exit 1
fi
echo "== push to s3://$CKPT_BUCKET (profile: $PROFILE) =="

# --- 学習データ: data/distilled/ 一式（SFT/GRPO用 rl/ と eval用 heldout を含む） ---
aws s3 sync "$PIPELINE_DIR/data/distilled/" "s3://$CKPT_BUCKET/data/distilled/" \
  --profile "$PROFILE" --delete
echo "OK: data/distilled/ -> s3://$CKPT_BUCKET/data/distilled/"

# --- リポジトリ本体（オプション。GPU側で git clone できない場合の代替） ---
if [ "${1:-}" = "--with-repo" ]; then
  aws s3 sync "$REPO_ROOT/" "s3://$CKPT_BUCKET/repo/" \
    --profile "$PROFILE" --delete \
    --exclude ".git/*" \
    --exclude "*/node_modules/*" \
    --exclude "nemo-pipeline/data/*" \
    --exclude "nemo-pipeline/checkpoints/*" \
    --exclude "nemo-pipeline/vendor/*" \
    --exclude "nemo-pipeline/hostpath.mk" \
    --exclude "nemo-pipeline/mlflow/*" \
    --exclude "nemo-pipeline/results/*" \
    --exclude "*/__pycache__/*" \
    --exclude "infra/gpu-host/.terraform/*" \
    --exclude "infra/gpu-host/terraform.tfstate*" \
    --exclude "infra/gpu-host/*.tfvars"
  echo "OK: repo -> s3://$CKPT_BUCKET/repo/"
fi

echo "== done =="
echo "GPUノード側: aws s3 sync s3://\$CKPT_BUCKET/data/distilled/ data/distilled/"
