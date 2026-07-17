#!/usr/bin/env bash
# S4 SFT/LoRA — NeMo-RL(examples/run_sft.py) で実行（S5 GRPOとコンテナ・設定様式を統一）。
# make sft から NeMo-RL コンテナ（Makefile: RL_IMG）内で呼ばれる。
# 前提: prep_rl_data.py 実行済み（/data/distilled/rl/ が存在）。
set -euo pipefail

echo "[S4] preflight"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || { echo "GPUなし"; exit 1; }
test -f /data/distilled/rl/sft_analysis_train.jsonl || {
  echo "[S4] 学習データ未生成。先に: python /pipeline/s5_rl/prep_rl_data.py --in /data/distilled --out /data/distilled/rl"; exit 1; }

# NeMo-RL リポ（閉域: vendor/RL を優先、無ければコンテナと同バージョンをclone）
if [ -d /pipeline/vendor/RL ]; then RL=/pipeline/vendor/RL;
else RL=/rl; [ -d $RL ] || git clone --depth 1 --branch "${RL_REF:-v0.7.0}" https://github.com/NVIDIA-NeMo/RL $RL; fi
cd "$RL"

echo "[S4] SFT開始（LoRA, config=/pipeline/s4_sft/sft_lora.yaml）"
uv run examples/run_sft.py --config /pipeline/s4_sft/sft_lora.yaml

echo "[S4] 完了。次: make eval TAG=sft （base比を確認してからS5へ）"
