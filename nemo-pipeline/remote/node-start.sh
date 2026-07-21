#!/usr/bin/env bash
# GPUノード起動後の毎回ルーチンを1コマンド化(ubuntuユーザーで実行):
#   swap再作成 → S3の2系統同期(repo/ + data/distilled/) → mlflow再起動 → 同期検証
# 使い方: SSM接続 → sudo su - ubuntu →
#   bash /opt/nvidia_slm/nvidia_slm/nemo-pipeline/remote/node-start.sh
#
# user-dataにしない理由: terraformでuser_dataを変えるとインスタンス再作成になり
# EBS上のHFキャッシュ・loopNアーカイブが消える。スクリプトならrepo同期で配布・PR管理できる
set -euo pipefail

# ssm-userのまま実行するとCKPT_BUCKET未設定+docker permission deniedになる
: "${CKPT_BUCKET:?CKPT_BUCKET が未設定です。sudo su - ubuntu してから実行してください}"

REPO_DIR=/opt/nvidia_slm/nvidia_slm
PIPE_DIR=$REPO_DIR/nemo-pipeline

# swapはNVMe上のためstop/startで消える。有効なら再作成しない
if ! swapon --show=NAME --noheadings | grep -q '^/opt/dlami/nvme/swapfile$'; then
  sudo fallocate -l 32G /opt/dlami/nvme/swapfile
  sudo chmod 600 /opt/dlami/nvme/swapfile
  sudo mkswap /opt/dlami/nvme/swapfile
  sudo swapon /opt/dlami/nvme/swapfile
fi
swapon --show

# 2系統同期。--delete は使わない(S3に無い checkpoints/ 等をローカル削除してしまう)
aws s3 sync "s3://$CKPT_BUCKET/repo/" "$REPO_DIR/"
aws s3 sync "s3://$CKPT_BUCKET/data/distilled/" "$PIPE_DIR/data/distilled/"

# stop/startで--rmコンテナ(mlflow)が名前を掴んだまま残ることがある
docker rm -f mlflow 2>/dev/null || true
make -C "$PIPE_DIR" tracking

echo "== 同期検証: label_sys.txt 冒頭(基準日と規約が新版か目視確認) =="
head -c 300 "$PIPE_DIR/data/distilled/rl/prompts/label_sys.txt"; echo
echo
echo "OK: cd $PIPE_DIR して作業を続行。新規SFT前は旧 step_* の退避を忘れずに:"
echo "  sudo mkdir -p /opt/nvidia_slm/loopN-archive && sudo mv $PIPE_DIR/checkpoints/sft/{step_*,hf} /opt/nvidia_slm/loopN-archive/"
