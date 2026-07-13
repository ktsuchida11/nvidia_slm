# 02. Makefileの守備範囲 — どこまで環境構築できるか

## 結論
`make` は **「コンテナの起動・ジョブ実行・成果物の受け渡し」までを全自動化**する。
**「ホストOSの準備・アカウント/キー・学習レシピの確定・モデル/イメージの初回取得」は人手**。

## ターゲット別カバレッジ

| ターゲット | 何をするか | 事前に人手で必要 | ネットワーク |
|---|---|---|---|
| `setup` | data/ckpt/resultsのディレクトリ契約作成 | Docker導入 | 不要 |
| `build-tools` | 依存焼き込みツールイメージbuild（閉域準備） | Docker | **要**（1回だけ） |
| `vendor-rl` | NeMo-RLをリポ内に取り込み（閉域準備） | git | **要**（1回だけ） |
| `extract` | PPTX/PDF/DOCX→S1入力（軽量版。図/スキャンはNeMo Retriever: docs/08） | 文書配置 | 不要* |
| `caption` | 図/スキャンをClaude(vision)でキャプション/OCR化→S1入力 | ANTHROPIC_API_KEY・機微画像は送らない判断 | 要(API) |
| `fetch` | HFから金融データ取得(商用可のみ)→S1入力形式へ | HFトークン, **ライセンス確認** | 要 |
| `curate` | S1完全実行（正規化/品質/PII/ライセンス/重複排除） | 生データ配置 | 不要* |
| `distill` / `distill-dry` | S2完全実行 / API無し配管検証 | ANTHROPIC_API_KEY（本番のみ） | 本番=要 / dry=不要* |
| `mlflow` | 実験トラッキングサーバ(:5000, 閉域) | — | 不要*（イメージ取得後） |
| `dapt` / `sft` | S3/S4のコンテナ起動＋導線表示 | **公式レシピ確定→run_*.sh有効化**, NGCログイン, GPU | イメージ取得時のみ |
| `grpo` | S5実行（vendor/RLがあれば閉域可） | GPU, **grpo_qwen.yamlのキーをRL examplesに整合** | vendor済なら不要* |
| `serve-nemotron` | Nemotronを **vLLM**配信(:8002)（既定bf16/任意fp8） | HF_TOKEN(gated同意) | イメージ取得時のみ |
| `serve` | （任意・代替）GGUF対応モデル用 llama.cpp配信 | models/にGGUF配置 | 不要* |
| `eval` / `eval-dry` | S6完全実行（合否exit code） / 配管検証 | serveまたは既存エンドポイント | 不要* |
| `guardrails` / `guardrails-test` | S7サーバ / evaluate+garak検証 | — | 不要*（ツールイメージ使用時） |
| `clean` | 常駐コンテナ停止 | — | 不要 |

\* 「不要」= 閉域準備（下記）完了後。素の`python:3.12-slim`使用時は実行時pipで要ネットワーク。

## make ができないこと（＝人手チェックリスト）
1. ホストOS準備: Docker / NVIDIA Container Toolkit / ドライバ（docs/00-environment.md）
2. アカウント・キー: NGCログイン（user=`$oauthtoken`）/ ANTHROPIC_API_KEY / HFトークン
3. **S3の学習コマンド確定**: run_dapt.sh はNeMo Framework公式レシピを確認して有効化する
   （誤実行防止のため意図的に exit 1 のスタブ）。
   ※ S4は解消済み: NeMo-RL(examples/run_sft.py)ルートで run_sft.sh 実装済み（s4_sft/README）
4. **S5設定キーの整合**: 解消済み。grpo_qwen.yaml は NeMo-RL examples/configs 準拠（2026-07時点）、
   独自報酬は s5_rl/finance_env.py（カスタム環境）+ run_grpo_finance.py（register_env）で組込済み。
   残る実機確認: RL_IMG のタグ / ResponseDataset→metadata の受け渡し（s5_rl/README）
5. モデルの取得: HFからDL（Nemotronはgated規約同意）。**量子化はvLLMのfp8（任意・オンライン/fp8ckpt）**。GGUF化は代替モデル使用時のみ
6. GPUホストの調達（ローカル64GB or EC2/Brev）とチェックポイント退避先(S3等)の用意

## 閉域ネットワークでの準備手順（オンライン環境で1回だけ）
```bash
make build-tools                      # ① 依存焼き込みイメージ（実行時pip排除）
make vendor-rl                        # ② NeMo-RLをリポ内へ
docker pull nvcr.io/nvidia/nemo:25.04 ghcr.io/mlflow/mlflow:latest \
  ghcr.io/ggml-org/llama.cpp:server-cuda   # ③ イメージ群（docker save/loadで持込可）
# ④ モデル(HF)・データセットも同様に取得して持ち込む
```
以後、閉域内では `make <target> PY_IMG=nemo-tools:latest` で実行時ネットワーク不要
（例外: S2本番distillの教師APIのみ外向き。完全閉域なら教師をローカル大型モデルに置換する設計判断が必要）。
