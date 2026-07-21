#!/usr/bin/env bash
# SFT/GRPOチェックポイント（PEFT LoRAアダプタ）→ HF形式（LoRAマージ済み）
# make convert-sft / convert-grpo から NeMo-RL コンテナ内で実行される。
# 引数: ステップディレクトリ（省略時は $CKPT_ROOT/step_* の最新。CKPT_ROOT既定=/ckpt/sft）
set -euo pipefail

CKPT_ROOT="${CKPT_ROOT:-/ckpt/sft}"
STEP_DIR="${1:-$(ls -d "$CKPT_ROOT"/step_* 2>/dev/null | sort -V | tail -1)}"
[ -n "$STEP_DIR" ] && [ -d "$STEP_DIR" ] || { echo "チェックポイントが見つからない: $CKPT_ROOT/step_*"; exit 1; }
ADAPTER="$STEP_DIR/policy/weights/model"
test -f "$ADAPTER/adapter_model.safetensors" || { echo "アダプタが見つからない: $ADAPTER"; exit 1; }

# config.yaml から policy.model_name を取得（model_name キーは policy 配下にのみ出現）
BASE=$(grep -m1 'model_name:' "$STEP_DIR/config.yaml" | sed -e 's/.*model_name:[[:space:]]*//' -e "s/['\"]//g")
[ -n "$BASE" ] || { echo "config.yaml から model_name を取得できない"; exit 1; }
OUT="$CKPT_ROOT/hf"
echo "[convert] adapter: $ADAPTER / base: $BASE -> $OUT"

rm -rf "$OUT.tmp"
cd /opt/nemo-rl
uv run python /pipeline/s4_sft/merge_lora.py \
  --adapter "$ADAPTER" \
  --base "$BASE" \
  --tokenizer "$STEP_DIR/policy/tokenizer" \
  --out "$OUT.tmp"
rm -rf "$OUT"
mv "$OUT.tmp" "$OUT"
echo "[convert] done -> $OUT（次: make serve-sft/serve-grpo → make eval TAG=...）"
