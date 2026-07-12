# 06. Nemotron-Nano-9B-v2-Japanese でパイプラインを回す

本パイプラインの既定生成モデルを **NVIDIA-Nemotron-Nano-9B-v2-Japanese** にする手順と注意。
NVIDIA製＝NeMoスタックとの相性が最も良い。**ただし配信・reasoning・量子化に固有の作法**があるので必ず本書に従う。

## モデル概要
> 補足: より新しい **Nemotron-3 Nano/Super/Ultra**（Mamba-Transformer MoE）も存在するが、
> 日本語特化で商用可・sub-10Bの本命は現状 **Nemotron-Nano-9B-v2-Japanese**。ここではこれを既定にする。
- HF: `nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese`（2026/02/17、約17.8GB bf16）
- **ライセンス: NVIDIA Nemotron Open Model License … 商用利用可**（HFで規約同意=gatedアクセス）
- Nejumi Leaderboard 4 で sub-10B 最高クラス。日本語＋英語＋コード、**128Kコンテキスト**
- アーキ: **Mamba-2 + Transformer ハイブリッド（Nemotron-H）**、**reasoning（thinking）モデル**
- 素性: Nemotron-Nano-9B-v2 を Nemotron-Personas-Japan(CC BY 4.0) 由来データで日本語強化（継続事前学習+SFT）

## ⚠ 重要な作法（ここを外すと動かない/精度が落ちる）
1. **配信は vLLM 必須**。llama.cpp / Ollama / GGUF は **非対応**（Mambaハイブリッド + custom_code のため）。
   → パイプラインの「量子化→GGUF→llama.cpp」経路は Nemotron では使わない。**bf16/fp8 を vLLM で配信**。
2. vLLM 起動に **`--trust-remote-code` と `--mamba_ssm_cache_dtype float32` が必須**（後者が無いと精度低下）。
3. **reasoningがデフォルトON**。構造化出力（S1解析JSON）や採点は **最終回答だけを使う**。実務では次のどちらか:
   - (推奨) vLLMで **reasoning-parser を有効化** → OpenAI応答の `message.content` が最終回答のみになり、S6評価はそのまま通る（`reasoning_content` に思考が分離）。
   - もしくは system prompt で reasoning を無効化（難問でやや精度低下）。
   - 保険として **S6評価は本文中の `<think>...</think>` を自動除去**する実装済み（s6_eval.py strip_reasoning）。
   - GRPO報酬(reward.py)も同様に **reasoningを除いた最終回答**に対して source_exists 等を採点する。
4. OOM時は `--max-num-seqs 64`（さらに下げる）。

## 配信（vLLM・OpenAI互換）
```bash
# 直接
vllm serve nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese \
  --trust-remote-code --mamba_ssm_cache_dtype float32 \
  --max-num-seqs 64 --max-model-len 131072 --port 8000
# → LiteLLM から http://<host>:8002/v1 で参照（compose.gpu.yml の vLLM サービス）
```
ツール呼び出し/推論トレースのパースが要る場合は、モデルカードの reasoning/tool パーサプラグインを併用。

## 64GB GPU での見積もり
- 推論: 重み ~18GB + KV/Mamba状態。64GBに余裕。
- **S4 SFT/LoRA**: NeMo Framework / AutoModel で可（LoRAなら十分収まる）。
- **S5 GRPO**: **NeMo-RLはNemotron Nano(Mambaハイブリッド)のGRPOに対応済み**（NVIDIA公式がNemotron NanoをNeMo-RLで学習・Nano系のGRPO/SFT LoRAレシピが実在）。
  rollout backendはvLLM、単一64GB GPUなら **DTensor backend + LoRA GRPO** が適切。
  → レシピは `examples/configs/recipes/llm/grpo-nanov3-*-lora.yaml`（Nemotron系）や `grpo-qwen3.5-9b-*.yaml` を**雛形**にし、`policy.model_name` を Nemotron-Nano-9B-v2-Japanese に差し替える。
- 報酬(reward.py)は **reasoningを除いた最終回答**に対して source_exists 等を採点するよう前処理を入れる。

## 相性の良い商用可データ（JaFInの代替として推奨）
| データ | ライセンス | 用途 |
|---|---|---|
| Nemotron-Post-Training-Dataset-v2 | 自由に学習・評価可（NVIDIA法務レビュー済・PII/著作権なし）日本語SFT/RL含む | SFT/RL補強 |
| Nemotron-Personas-Japan | **CC BY 4.0** | 合成データの種（ペルソナ）・日本語多様性 |
（詳細は docs/03-accounts-and-datasets.md）

## 量子化（このパイプラインで対応する方法）
配信は vLLM のため、**対応する量子化は vLLM がサポートする方式のみ**。
- **既定は bf16**（量子化なし）。64GBなら 9B は余裕。
- **fp8**：VRAM/スループットを稼ぎたい場合。次のいずれか。
  - オンライン量子化：vLLM 起動に `--quantization fp8`（重みをfp8化）、必要に応じ `--kv-cache-dtype fp8`。
  - fp8チェックポイント：NVIDIAが提供するfp8版があればそれを配信（`--model <...-FP8>`）。
- **mambaフラグは維持**：fp8でも `--mamba_ssm_cache_dtype float32` は付ける（精度維持）。
- ❌ **GGUF / llama.cpp / Ollama 量子化は非対応**（Mambaハイブリッド）。本パイプラインでは使わない。
  GGUF量子化を使いたい場合は対応モデル（Qwen等）に変える必要がある＝別構成。

## このモデルに切り替える設定（どこを変えるか）
| ファイル | 変更 |
|---|---|
| `compose.gpu.yml` | 生成配信を **vLLM（Nemotron・mambaフラグ付き）** に（既定を差し替え済） |
| `litellm_config.yaml` | `nemotron-gen` を vLLM エンドポイントに向ける（設定済） |
| `s4_sft/sft_lora.yaml` | `base: nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese` |
| `s5_rl/grpo_qwen.yaml` | `model_name` を Nemotron に（GRPO対応は要確認・上記） |
| `s6_evaluation/eval.yaml` | `model: nemotron-gen` |
| `.env` | `QWEN_GEN_BASE` → `NEMOTRON_GEN_BASE`（または既存varをvLLM:8002に向ける） |

## 学習の実行（NeMo-RL）
- 公式コンテナ `nvcr.io/nvidia/nemo-rl:v0.6.0`（NGC）を使うか、リポを `--recursive` clone して `uv run`。
- S4 SFT / S5 GRPO はいずれも NeMo-RL の run_sft.py / run_grpo.py で実行可（Nemotron系レシピをbaseに）。
- 単一64GB: `policy.dtensor_cfg.enabled=True` + LoRA + `activation_checkpointing=True`、OOM時 `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:64`。

## 参照
- モデルカード: https://huggingface.co/nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese
- リリースブログ: https://huggingface.co/blog/nvidia/nemotron-nano-9b-v2-japanese
- ライセンス: NVIDIA Nemotron Open Model License（HFの規約に同意して取得）
