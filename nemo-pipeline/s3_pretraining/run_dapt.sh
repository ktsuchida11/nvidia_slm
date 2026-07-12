#!/usr/bin/env bash
# S3 継続事前学習(DAPT) — 任意ステージ。効果はS6のbefore/afterで判定（評価ファースト）。
# ここでは (1)データ準備は実際に実行 し、(2)学習コマンドは公式レシピ確認後に有効化する。
set -euo pipefail

echo "[S3-1/3] データ準備: S1出力(curated.jsonl) → 事前学習コーパス(/data/pretrain/corpus.jsonl)"
python3 /pipeline/s3_pretraining/prep_corpus.py --in /data/curated/curated.jsonl --out /data/pretrain

echo "[S3-2/3] 学習実行（要: 公式レシピでキー確定後に有効化）"
echo "  NeMo Framework の継続事前学習レシピに /pipeline/s3_pretraining/dapt.yaml を反映して実行:"
echo "  例) torchrun --nproc_per_node=1 <recipe>.py --config /pipeline/s3_pretraining/dapt.yaml"
echo "  64GB目安: 4B=フル/LoRA可、9B=LoRA-DAPT(シーケンス2048・micro-batch 1)"
echo "[S3-3/3] 学習後は必ず: make eval TAG=dapt （base比で改善が無ければこのステージは捨てる）"
exit 1  # ← レシピ確定後にこの行を削除して有効化
