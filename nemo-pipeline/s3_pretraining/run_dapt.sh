#!/usr/bin/env bash
# S3 継続事前学習(DAPT) — loop10で実体化。効果はS6のbefore/afterで判定（評価ファースト）。
# 前提: /data/pretrain/corpus_{train,val,probe}.jsonl（無ければここで生成）・GPUノード(nemo:25.04)。
set -euo pipefail

echo "[S3-1/3] コーパス準備（リーク検査+train/val/probe 3分割）"
if [ ! -s /data/pretrain/corpus_train.jsonl ] || [ "${DAPT_REPREP:-0}" = "1" ]; then
  python3 /pipeline/s3_pretraining/prep_corpus.py --in /data/curated/curated.jsonl --out /data/pretrain
else
  echo "  corpus_train.jsonl あり → 再生成スキップ（強制は DAPT_REPREP=1）"
fi

echo "[S3-2/3] 学習実行（NeMo 2.3 AutoModel + FSDP2）"
# nemo:25.04 の transformers が NemotronH 系(Nemotron-Nano-9B-v2)未対応の場合の逃げ道
if [ "${DAPT_PIP_UPGRADE:-0}" = "1" ]; then
  pip install -q -U "transformers>=4.53" datasets
fi
# nemo:25.04 に mlflow は非同梱（実機確認）。MLflow 追跡を使う場合のみ軽量版を入れる
if [ -n "${MLFLOW_TRACKING_URI:-}" ]; then
  python3 -c "import mlflow" 2>/dev/null || pip install -q mlflow-skinny || \
    echo "⚠ mlflow インストール失敗 — 追跡なしで続行（dapt_train.py 側でスキップ）"
fi
python3 - <<'EOF'
from transformers import AutoConfig
import transformers
cfg = AutoConfig.from_pretrained("nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese", trust_remote_code=True)
print(f"transformers={transformers.__version__} / model_type={cfg.model_type} → 読込OK")
EOF
GPUS="${DAPT_GPUS:-4}"
CFG="${DAPT_CFG:-/pipeline/s3_pretraining/dapt.yaml}"
torchrun --nproc-per-node="$GPUS" /pipeline/s3_pretraining/dapt_train.py --config "$CFG"

echo "[S3-3/3] 完了。次の手順:"
echo "  1) held-out loss: 学習ログの val_loss（corpus_val）を before/after 比較"
echo "  2) ckpt(/ckpt/dapt) を HF 形式で配信 → make eval TAG=dapt10 BASELINE=base8 /"
echo "     make eval-finqa TAG=dapt10 BASELINE=base9b（非退行）"
echo "  3) 改善が無ければこのステージは捨てる（評価ファースト）"
