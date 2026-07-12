#!/usr/bin/env bash
# S4 SFT/LoRA — NGCのNeMo Frameworkコンテナ内で実行（借りGPU）。
# NeMo Framework の finetuning(SFT/PEFT) レシピに sft_lora.yaml の値を反映して実行する。
# 軽量代替: Unsloth(Colab)でも同じ train.jsonl を使える（両路線を同一held-outで比較可能）。
set -euo pipefail
echo "[S4] 実行例(要調整): コンテナ内で公式finetuneレシピに --config /pipeline/s4_sft/sft_lora.yaml を反映"
echo "[S4] 完了後は必ず: make eval TAG=sft  （base との差分を確認してからS5へ）"
exit 1
