#!/usr/bin/env bash
# SFTチェックポイント（DTensor DCP + LoRA）→ HF形式（LoRAマージ済み）
# make convert-sft から NeMo-RL コンテナ内で実行される。
# 引数: ステップディレクトリ（省略時は /ckpt/sft/step_* の最新）
set -euo pipefail

STEP_DIR="${1:-$(ls -d /ckpt/sft/step_* 2>/dev/null | sort -V | tail -1)}"
[ -n "$STEP_DIR" ] && [ -d "$STEP_DIR" ] || { echo "SFTチェックポイントが見つからない: /ckpt/sft/step_*"; exit 1; }
OUT=/ckpt/sft/hf
echo "[convert] input: $STEP_DIR -> $OUT"

rm -rf "$OUT.tmp"
cd /opt/nemo-rl
uv run python examples/converters/convert_dcp_to_hf.py \
  --config "$STEP_DIR/config.yaml" \
  --dcp-ckpt-path "$STEP_DIR/policy/weights" \
  --hf-ckpt-path "$OUT.tmp"
uv run python /pipeline/s4_sft/merge_lora.py --hf-dir "$OUT.tmp"
rm -rf "$OUT"
mv "$OUT.tmp" "$OUT"
echo "[convert] done -> $OUT（次: make serve-sft → make eval TAG=sft）"
