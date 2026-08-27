#!/usr/bin/env bash
# ノード側: ループ中に修正PRがマージされたときの「リポジトリだけ」再同期(ubuntuユーザーで実行)。
#   Mac側:  bash remote/push-to-s3.sh --with-repo
#   ノード側: bash /opt/nvidia_slm/nvidia_slm/nemo-pipeline/remote/sync-repo.sh [確認パターン]
#
# node-start.sh との違い: swap再作成・data同期・mlflow再起動をしない（実行中の配信を触らない）。
# ノードは git remote を持たず S3 経由で配布するため、`git pull` ではなくこれを使う。
set -euo pipefail

: "${CKPT_BUCKET:?CKPT_BUCKET が未設定です。sudo su - ubuntu してから実行してください}"

REPO_DIR=/opt/nvidia_slm/nvidia_slm
PIPE_DIR=$REPO_DIR/nemo-pipeline

# aws s3 sync はスキップ警告だけでも rc=2 を返す。実エラー(rc=1等)のみ中断する（node-start.sh と同じ）
aws s3 sync "s3://$CKPT_BUCKET/repo/" "$REPO_DIR/" || [ $? -eq 2 ]
echo "OK: s3://$CKPT_BUCKET/repo/ -> $REPO_DIR"

# 届いたかの確認。引数があれば Makefile 内の出現数を数える（例: guardrails-dry-rails）
if [ -n "${1:-}" ]; then
  echo "== 同期検証: '$1' の出現数 =="
  grep -c -- "$1" "$PIPE_DIR/Makefile" || echo "0 (未着。Mac側の push-to-s3.sh --with-repo を確認)"
fi
echo "== 直近更新ファイル(上位5件) =="
find "$PIPE_DIR" -type f -newermt '-1 day' -not -path '*/data/*' -not -path '*/checkpoints/*' \
  -not -path '*/results/*' -not -path '*/__pycache__/*' -printf '%T+ %p\n' 2>/dev/null |
  sort -r | head -5
