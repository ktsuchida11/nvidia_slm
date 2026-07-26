#!/usr/bin/env bash
# S4 SFT/LoRA — NeMo-RL(examples/run_sft.py) で実行（S5 GRPOとコンテナ・設定様式を統一）。
# make sft から NeMo-RL コンテナ（Makefile: RL_IMG）内で呼ばれる。
# 前提: prep_rl_data.py 実行済み（/data/distilled/rl/ が存在）。
set -euo pipefail

# 設定はSFT_CFGで差し替え可（ループ9: make sft SFT_CFG=/pipeline/s4_sft/sft_finqa.yaml）
CFG="${SFT_CFG:-/pipeline/s4_sft/sft_lora.yaml}"

echo "[S4] preflight (config=$CFG)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || { echo "GPUなし"; exit 1; }
# 設定が参照する学習データの存在を全件確認（旧: sft_analysis固定 → 設定差し替えに追従）
for p in $(grep -oE 'data_path: */[^ ]+' "$CFG" | sed 's/data_path: *//'); do
  test -f "$p" || { echo "[S4] 学習データ未生成: $p — prep_rl_data.py / s2_finqa.py --build を先に実行"; exit 1; }
done

# NeMo-RL リポ: リリースコンテナ同梱の /opt/nemo-rl（構築済みvenv付き）を最優先。
# 無ければ vendor/RL、最後の手段で同バージョンをclone（uvワークスペースのため--recursive必須）
if [ -d /opt/nemo-rl/examples ]; then RL=/opt/nemo-rl;
elif [ -d /pipeline/vendor/RL ]; then RL=/pipeline/vendor/RL;
else RL=/rl; [ -d $RL ] || git clone --depth 1 --recursive --branch "${RL_REF:-v0.6.0}" https://github.com/NVIDIA-NeMo/RL $RL; fi
cd "$RL"

echo "[S4] SFT開始（LoRA, config=$CFG）"
uv run examples/run_sft.py --config "$CFG"

echo "[S4] 完了。次: make eval TAG=sft （base比を確認してからS5へ）"
