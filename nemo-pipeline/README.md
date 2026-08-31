# NeMo LLMパイプライン（金融ドメイン特化・Linuxコンテナ完結）

データ収集 → 蒸留 → (継続事前学習) → SFT → 強化学習(GRPO) → 評価 → ガードレール → **検索拡張(RAG)** を
**NVIDIA NeMoのライブラリ層（無償・Apache 2.0）だけ**で貫くパイプライン。

**15周まわした実測記録つき**（累計 ~$400）。各周の結論は `docs/loop-*-report.md`（**14本**。
loop11 はスキル化のみでレポートを書いていないため欠番）、
踏んだ罠は `claude-skills/nemo-pipeline-runner/references/traps.md` にある。
**最大の発見は「知識注入は DAPT ではなく RAG」**（DAPT 見積り $300-550 に対し RAG 実測 $12 で
probe 0.0402 → 0.4724、rerank 追加で 0.6332）。

## ❓ まず一番の疑問に答える: Kubernetesは要る？

**要りません。本パイプラインの全ステージは Docker（＋GPUステージのみ NVIDIA Container Toolkit）で完結します。**

| やること | 必要なもの | K8s |
|---|---|---|
| S1 収集・管理 / S2 蒸留 / S6 評価 | Docker のみ（CPUで可） | 不要 |
| S3/S4/S5 学習 | Docker + NVIDIA Container Toolkit（借りGPUホスト上） | 不要 |
| S7 ガードレール（library）＋garak検証 | Docker のみ | 不要 |
| S8 検索拡張 RAG（embedding / rerank NIM） | Docker + Container Toolkit | 不要 |
| 推論配信（**vLLM**） | Docker + Container Toolkit | 不要 |
| ―――― ここから下は"将来"の話 ―――― | | |
| NeMo **microservices**（Customizer/Evaluator/Guardrails MS） | NVIDIA AI Enterprise（有償）+ **K8s** | 必要 |
| NIM を本番規模でオーケストレーション | 同上 | 必要 |

つまり **K8sが要るのは「microservices層に載せ替える将来」だけ**。Guardrailsは library の設定が
microservice にそのまま移行できる形式なので、今 library で作ることが将来の布石になります。
初構築は「Docker+Makefileで全部やる」で正解です。

## 🗺 構成図

![パイプライン全体像](docs/architecture-overview.svg)

> 緑タグ＝各ステージで使うNVIDIA NeMoライブラリ。**黒タグ＝NeMo外**（S2 の教師＝Claude API、S6＝自作採点器）。
> 赤破線＝held-out（学習・報酬に使わず評価専用）。図中の数値は `docs/loop-*-report.md` の実測。
> **「使う」＝実行時のモデル使い分け/検索/ルーティングは別アーキ**なので別図に分離: `docs/serving-routing-overview.svg`

![使う（推論構成）](docs/serving-routing-overview.svg)

```
                     ┌──────────────────────── ローカルLinux（CPUコンテナ, K8s不要） ───────────────────────┐
                     │                                                                                      │
  HF/公開データ ──▶ [S1 収集・管理] ──▶ [S2 蒸留・メタデータ] ─────────────┐                                 │
  ダミー問合せ       NeMo Curator流       教師=Claude Sonnet               │ train/valid/【heldout】         │
                     正規化/品質/PII/     スキーマ検証+忠実性ゲート        ▼                                 │
                     ライセンス/重複排除  (source_exists=報酬と同一関数)  /data/distilled                    │
                     └──────────────────────────────────────────────────────────────────────────────────────┘
                                                       │ train/valid のみ（heldoutは渡さない）
                     ┌────────── GPUホスト（AWS g6e spot = L40S 48GB / 実効44GB, Dockerのみ） ──────────┐
                     │   [S3 DAPT(任意)] ─▶ [S4 SFT/LoRA] ─▶ [S5 GRPO]                                  │
                     │    NeMo Framework       NeMo-RL          NeMo-RL / 報酬=common/reward.py         │
                     │    分布適応は効く       形式を教える     KLアンカー必須                          │
                     │    知識注入は効かない   壊しやすい       天井が近い                              │
                     └───────────────────────────────│──────────────────────────────────────────────────┘
                                                     ▼ checkpoints → 配信(bf16)
        ┌────────────────────── S8 検索拡張 RAG（知識はここが担う） ──────────────────────┐
        │ corpus ─▶ [チャンク+埋め込み embedding NIM] ─▶ [検索 top k0=100] ─▶ [rerank NIM] │
        │            NeMo Retriever                                     NeMo Retriever      │
        └───────────────────────────────│──────────────────────────────────────────────────┘
                                        ▼ 文脈
        ┌─────────────────────────── ローカル/GPUホスト ───────────────────────────────┐
        │ [推論配信 vLLM(OpenAI互換)] ◀─── [S6 評価 s6_eval.py（自作採点器）]           │
        │            ▲                     heldout で base vs SFT vs GRPO               │
        │            │                     閾値未達=exit2（前段に戻る）                 │
        │ [S7 NeMo Guardrails server] ◀── 検証: garak + 自作の日本語攻撃セット          │
        └───────────────────────────────────────────────────────────────────────────────┘
```

> **配信は vLLM 固定**。既定の Nemotron-Nano-9B-v2-Japanese は Mamba+Attention ハイブリッドで
> **GGUF 非対応**のため llama.cpp / Ollama には載らない。

原則: **評価ファースト**（S0で指標・閾値・heldoutを固定し、baseを先に測ってから学習）。

## 📁 ディレクトリ（フェーズごと・各READMEあり）

| ディレクトリ | ステージ | 主なファイル |
|---|---|---|
| `common/` | 共有コード | reward.py（検証可能報酬）/ schema.py / prompts.py / finqa.py / label_lint.py |
| `s0_eval_design/` | S0 評価設計 | thresholds（eval.yaml に集約）と base 測定の手順 |
| `s1_curation/` | S1 収集・管理 | s1_curate.py / fetch_dataset.py / fetch_edinet_bench.py / curator_stage.py / convert_docs.py / caption_figures.py / data_viewer.py |
| `s2_distillation/` | S2 蒸留 | s2_distill.py（`--dry-run` あり）/ s2_finqa.py |
| `s3_pretraining/` | S3 DAPT | run_dapt.sh / dapt.yaml / dapt_train.py / prep_corpus.py（**loop10 で実走済み**） |
| `s4_sft/` | S4 SFT/LoRA | run_sft.sh / sft_lora.yaml / sft_finqa.yaml / sft_grounded.yaml / merge_lora.py（**loop1-13 で実走済み**） |
| `s5_rl/` | S5 GRPO | grpo_qwen.yaml / grpo_analysis.yaml / grpo_finqa.yaml / finance_env.py / prep_rl_data.py / run_grpo_finance.py |
| `s6_evaluation/` | S6 評価 | s6_eval.py（合否 exit code）/ probe_qa.py / eval.yaml / eval_finqa.yaml |
| `s7_guardrails/` | S7 ガードレール | config/（config.yml + rails.co）/ check_rails.py / **stub_llm.py + wait_http.py（`guardrails-dry` の実体・GPU不要）** / llm_proxy.py / garak_rest.json / render_config.py / make_garak_cfg.py / inspect_garak.py / diag_rails.py |
| **`s8_retrieval/`** | **S8 検索拡張 RAG** | build_index.py / embedder.py / retrieve.py / reranker.py / recall_eval.py / chunker.py / build_grounded_sft.py |
| `tests/` | ユニット・回帰テスト | 11ファイル。`for t in tests/test_*.py; do python3 "$t"; done` で全部走る（pytest 不要） |
| `docs/` | 文書 | 00-environment / 10-runbook / **loop-*-report 14本（実測の出典・loop11 は欠番）** / 34-38（勉強会の資料一式） |

## 🖥 GPU の実績と VRAM

**15周すべて AWS g6e spot（L40S 48GB / 実効44GB）で回した。** 実測:

| 構成 | 9B LoRA seq2048 | 9B LoRA seq4096 |
| --- | --- | --- |
| 44GB×1 | ✅ 実測済み（loop1-7: 25s/step） | ❌ **OOM 実測**（43.5GiB使用 + 1.9GiB要求 = 約45.4GB 必要） |
| 44GB×2 | ✅ | ✅ `tensor_parallel_size: 2` + `use_triton: false` + `dropout: 0.0`（loop13） |

ローカル 64GB GPU がある場合の想定手順（Route L）は `docs/10-runbook.md` Step 5-L と
`docs/00-environment.md` にあるが、**この構成では未検証**。使うなら 1 回実走して確かめること。
推論ホスト兼用時は学習中に推論を止める運用にする。

## 🔒 クローズネットワーク対応
実験トラッキングは **MLflow(セルフホスト)+TensorBoard+Langfuse** の3点で外部SaaSゼロ（W&B不使用）。
MLflowはNeMo-RL/NeMo Frameworkが公式対応。唯一外に出るのはS2蒸留の教師API（対処はdocs/00-environment.md）。

## 🚀 クイックスタート

```bash
make setup
docker pull python:3.12-bookworm                       # 1.5GB。イメージのビルドは不要
printf 'PY_IMG := python:3.12-bookworm\n' > hostpath.mk # 既定の slim は arm64 Mac で動かない

make curate-demo distill-dry eval-dry                  # API・GPU無しで配管を検証（数分）
```

`"pass": true` が出れば配管は OK。以降は `docs/10-runbook.md`（E2E ランブック）へ。

> **`make curate` ではなく `make curate-demo`**。`curate` は `data/raw/` の**実データ全量**を
> 対象にする。デモ・演習は `data/demo_raw/` → `data/demo_curated/` に隔離されている。
> `*-dry` も同様に `_dry` 付きの別ディレクトリに出るので、実データを壊さない
> （`tests/test_dry_targets_isolated.py` が機械的に保証している）。

## 📚 公式マニュアル（リンクは変わることがあるため一次情報を優先）

| 対象 | リンク |
|---|---|
| NeMo Framework | https://docs.nvidia.com/nemo-framework/user-guide/latest/ |
| NeMo Curator | https://github.com/NVIDIA-NeMo/Curator / https://docs.nvidia.com/nemo/curator/ |
| NeMo-RL | https://github.com/NVIDIA-NeMo/RL / https://docs.nvidia.com/nemo/rl/ |
| NeMo Evaluator（**本パイプラインでは未使用**・下記注記） | https://github.com/NVIDIA-NeMo/Eval / https://docs.nvidia.com/nemo/evaluator/ |
| NeMo Retriever（S8 RAG の embedding / rerank NIM） | https://docs.nvidia.com/nim/ |
| NeMo Guardrails | https://github.com/NVIDIA-NeMo/Guardrails / https://docs.nvidia.com/nemo/guardrails/ |
| NGCカタログ/APIキー | https://catalog.ngc.nvidia.com / https://ngc.nvidia.com/setup |
| garak（レッドチーミング） | https://github.com/NVIDIA/garak |
| MLflow（実験トラッキング・閉域） | https://mlflow.org/docs/latest/ |
| NVIDIA Container Toolkit | https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/ |

> **S6 は NeMo Evaluator ではなく自作採点器**（`s6_evaluation/s6_eval.py`）で15周まわした。
> 合否閾値・リーク検査・SE 判定を自前で握る必要があったため。当初計画は `docs/01-plan.md`。

**既定モデル= NVIDIA-Nemotron-Nano-9B-v2-Japanese（商用可・vLLM配信）→ `docs/06-nemotron-model.md`**

**必要なアカウント一覧・データセットのライセンス（商用可否）→ `docs/03-accounts-and-datasets.md`**

次に読む: `docs/00-environment.md`（アカウント・APIキー）→ **`docs/02-makefile-coverage.md`（makeの守備範囲・閉域準備）** → `docs/10-runbook.md`（E2E手順）
