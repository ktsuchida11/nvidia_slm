# S4 事後学習 SFT/LoRA
S2のtrain/validでSFT。**実装済み: NeMo-RL（examples/run_sft.py）で実行**（S5とコンテナ・設定様式を統一。LoRA/MLflowロガー公式対応）。
設定= `sft_lora.yaml`（キー構造は NVIDIA-NeMo/RL examples/configs/sft.yaml 準拠・2026-07時点）。

手順:
1. `make prep-rl` — distilled → ResponseDataset形式へ変換（プロンプトは s6_eval と同一描画）
2. `make sft` — NeMo-RLコンテナ（Makefile: `RL_IMG`。タグはNGCカタログで実機確認）で実行
3. **終わったら即 `make eval TAG=sft`**（base比を確認してからS5へ）

実行環境: 48GB GPU×1で 4B/9B とも SFT/LoRA 可（9B ~24-32GB）。同じ `make sft` でローカル/借りGPU両対応。
軽量代替: Unsloth(Colab)でも同じデータが使える（同一heldoutで両路線比較可）。K8s不要。
（旧路線の NeMo Framework finetuneレシピでも可 — その場合は `NEMO_IMG` で旧コンテナを指定し run_sft.sh を差し替え）
