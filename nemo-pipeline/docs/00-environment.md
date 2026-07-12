# 00. 環境構築 — 必要なアカウント・APIキー・ログイン

## 必要なアカウントとキー（どこで・何に使うか）

| # | サービス | 取得場所 | 使う場所 | 環境変数/ログイン | 費用 |
|---|---|---|---|---|---|
| 1 | **NGC**（NVIDIAコンテナ） | https://ngc.nvidia.com/setup → 「Generate API Key」 | S3/S4/S5のNeMoコンテナpull | `docker login nvcr.io`（**user=`$oauthtoken`**, pass=APIキー） | 無料 |
| 2 | **Anthropic API** | https://console.anthropic.com → API Keys | S2 蒸留（教師=Claude） | `ANTHROPIC_API_KEY` | 従量（蒸留$40〜120目安） |
| — | **データセット** | 商用可のみ使用（JaFInはNCで不可）。詳細 → docs/03-accounts-and-datasets.md | | | |
| 3 | **Hugging Face** | https://huggingface.co/settings/tokens (Read) | S1 データ取得 / モデルDL | `HF_TOKEN`（`huggingface-cli login`） | 無料 |
| 4 | **AWS**（借りGPU） | IAMユーザ + EC2権限 | S3/S4/S5（g6e系 spot） | `aws configure`（アクセスキー） | 従量（学習$60〜240目安） |
| 4' | （代替）**Colab Pro** | Googleアカウント | 軽量実験（Unsloth代替ルート） | ブラウザログイン | ~$10-50/月 |
| 4''| （代替）**NVIDIA Brev / DGX Cloud** | https://brev.nvidia.com | S3/S4/S5 | ブラウザ+CLI | 従量 |
| 5 | （任意）Langfuse | セルフホスト（**構築手順**: local-rag-llm/templates/README「監視環境の構築」） | 監視・トレース | `LANGFUSE_PUBLIC_KEY/SECRET_KEY` | 無料(自前) |
| 6 | ~~W&B~~ → **MLflow(セルフホスト)** | 本パイプライン同梱（`make tracking`） | 学習曲線・実験比較（S3-S5） | `MLFLOW_TRACKING_URI`（既定 :5000） | 無料・**完全閉域** |

## 実験トラッキング（クローズドネットワーク前提）

W&Bはクラウド送信が前提のため**不採用**。閉域での選択肢と結論:

| 候補 | 閉域適性 | NeMo対応 | 位置づけ |
|---|---|---|---|
| **MLflow（自前コンテナ）** | ◎ 完全閉域 | **NeMo-RL/Framework両方が公式ロガー対応**（設定1行） | **本命**。`make mlflow` で:5000に常駐、UIで実験比較・ハイパラ・GPUメトリクス |
| TensorBoard | ◎ ファイルベースで完全オフライン | 両方対応 | 併用推奨（サーバすら不要な最軽量。/results/tb を `tensorboard --logdir`） |
| Langfuse（既存） | ◎ セルフホスト | 学習ロガーとしては非対応 | **役割が違う**: 学習曲線ではなく推論トレース/評価スコア用。S6評価結果やS7レール発火の記録は引き続きLangfuseへ（分業） |
| AWS: SageMaker managed MLflow | ○ VPC内利用 | MLflow互換なので可 | 学習をAWS内(EC2)で完結させ、AWS管理のトラッキングサーバが欲しい場合の代替。閉域・ローカル学習(Route L)には自前MLflowの方が単純確実 |
| ClearML/Aim等 | ○ セルフホスト可 | ClearMLはFramework対応 | 好みで。標準はMLflowに固定 |

**使い方**: 学習前に `make mlflow` → `make sft` / `make grpo`（MakefileがMLFLOW_TRACKING_URIを自動注入）→ http://localhost:5000 で確認。データは `results/mlruns/` に永続化（バックアップ対象）。

**Makefileでどこまで自動化されるか**は `docs/02-makefile-coverage.md` を参照（閉域準備の手順もそこに集約）。

**秘匿情報の置き方**: 平文`.env`をリポジトリに置かない。ローカルは環境変数 or SOPS+age、
GPUホストへは実行時に `-e ANTHROPIC_API_KEY` 等で注入（Makefileがそうなっている）。

## 🔒 クローズネットワーク構成（実験トラッキングと外部依存の整理）

### トラッキングは3点セット・全て閉域内（外部SaaSゼロ）

| ツール | 役割 | 根拠・接続 |
|---|---|---|
| **MLflow(セルフホスト)** | S3-S5の学習メトリクス・実験比較（W&B代替の主役） | **NeMo-RL/NeMo Frameworkが公式対応**（NeMo-RL: `logger.mlflow_enabled`+`tracking_uri` / Framework: `create_mlflow_logger`）。`make tracking`で:5000に常駐（SQLite+ローカルartifacts） |
| **TensorBoard** | 保険（サーバ不要・ローカルファイル直読み） | NVIDIAの流儀のデフォルト。`make tensorboard`(:6006) |
| **Langfuse(既存)** | S6評価スコア・S7ガードレール発火・推論トレース | 学習曲線トラッカーではなくLLM可観測性。セルフホストで閉域OK |

- **NVIDIA純正のW&B相当ホスティングサービスは存在しない**。NVIDIAの公式路線が「TensorBoard/MLflowへのネイティブ統合」そのもの → MLflowが正規ルート。
- AWSで学習する場合のみ **SageMaker managed MLflow**（VPC内・PrivateLink）も選べるが、オンプレ閉域＋可搬性ならセルフホストMLflow一択（EC2でもコンテナごと動く）。
- W&B自体のセルフホスト(W&B Server)は商用有償ライセンスのため不採用。

### 閉域で動くもの / 外に出る必要があるもの（正直な整理）

| 通信 | 閉域可否 | 対処 |
|---|---|---|
| 学習・評価・トラッキング・ガードレール・garak | ◎ 完全閉域 | そのまま |
| NGC/DockerHubのイメージpull | △ 初回のみ要 | 接続可能な場所で `docker pull` → `docker save/load` で持ち込み |
| HFモデル・データセットDL | △ 初回のみ要 | 同上（`HF_HUB_OFFLINE=1`+事前キャッシュ持ち込み） |
| **S2蒸留の教師API(Claude)** | ✕ 要外部 | 唯一の例外。(a)踏み台/egressプロキシ経由で実施 (b)接続可能環境で蒸留だけ済ませ **train/valid/heldout を持ち込む**（推奨: データは往路のみ・機密は送らない設計済み） |

## ソフトウェア前提（Linux）

```bash
# 1) Docker（未導入なら）
curl -fsSL https://get.docker.com | sh
# 2) NVIDIA Container Toolkit（GPUステージを動かすホストのみ）
#    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
# 3) 動作確認
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
# 4) NGCログイン（S3/S4/S5用）
docker login nvcr.io   # Username: $oauthtoken / Password: <NGC APIキー>
```

## K8sについて（結論の再掲）

- 本パイプラインは**K8s不要**。全ステージ=Dockerコンテナ（ジョブ型）+ Makefile。
- K8sが必要になるのは **NeMo microservices**（NVIDIA AI Enterprise, 有償）へ載せ替える時だけ。
  その時もGuardrails設定などはそのまま移行できる（libraryとmicroserviceは設定互換）。
- 借りGPU（EC2）も「Ubuntu + Docker + Container Toolkit」の素のVMでよい。EKSは不要。

## 🖥 ローカルGPU(64GB VRAM)を使う場合 — Route L

手元に64GB VRAMのGPUがあるなら、**学習の大半をローカルで完結**できる（前提の更新:
「学習=クラウドのみ」は64GB級ローカルGPUが無い場合の規則。ある場合はローカル優先が合理的）。

**準備**（借りGPUと同じ。K8s不要）:
```bash
# NVIDIAドライバ + Docker + NVIDIA Container Toolkit + NGCログイン（本書冒頭の手順どおり）
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi   # 64GBが見えること
```

**64GBでの現実表**:

| ジョブ | 64GBローカル | 目安VRAM(bf16/LoRA) | 備考 |
|---|---|---|---|
| S4 SFT/LoRA 4B | ◎ 余裕 | ~12-16GB | |
| S5 GRPO 4B | ◎ 余裕 | ~20-30GB | 配管・報酬検証はまずこれ |
| S4 SFT/LoRA 9B | ◎ 快適 | ~24-32GB | seq 4kでも収まる |
| **S5 GRPO 9B** | **△ ギリギリ** | ~50-60GB | 学習系+生成系が同居するため。下の絞りノブ必須 |
| S3 DAPT 9B (LoRA) | ○ | ~30GB | |
| 推論配信 4B+9B+Guard同居 | ◎ | ~15-25GB(fp8) | |

**9B GRPOをギリギリで通す絞りノブ**（OOMしたら上から順に）:
1. `num_generations: 8→4`（group縮小） 2. `max_new_tokens: 512→384` 3. シーケンス長 4096→2048
4. LoRA必須（full FTにしない）+ activation checkpointing 5. 生成エンジンのメモリ割当を下げる
   （NeMo-RLのcolocated設定/`gpu_memory_utilization`相当を0.4程度に） 6. それでもOOM →
   **Unslothルート**（同じtrain.jsonl/報酬で、8-9B GRPOを~20-25GBに収める最適化）or 借りGPU(4×L40S)。

**運用上の注意**:
- このGPUが**本番推論ホストを兼ねる場合、学習中は推論が劣化/停止**する。学習は夜間バッチにするか、
  学習前に `docker compose -f compose.gpu.yml stop` で推論サービスを止め、終了後に再開する。
- 長時間ジョブは `tmux`/`nohup` + 定期チェックポイント（電源・熱にも注意。`nvidia-smi -l 10` で監視）。
- ローカルの利点: 限界費用≈電気代のみ / spot中断なし / **学習データが外に出ない**（蒸留APIは除く）。

## 借りGPUホストの標準構成（S3/S4/S5用 — ローカル64GBが無い場合・9B GRPOの保険）

| 用途 | インスタンス | 目安 |
|---|---|---|
| 4B SFT/GRPO・配管検証 | g6e.xlarge（L40S 48GB ×1, spot） | ~$0.6-0.9/h |
| 9B SFT | g6e.xlarge〜A100 ×1 | |
| 9B GRPO | **g6e.12xlarge（L40S ×4, spot）** | ~$2.5-4/h |

セットアップは上記1)〜4)＋このリポを`git clone`または`scp`で持ち込み、同じ`make`を叩くだけ。
チェックポイントはS3等へ退避（spot中断前提）。**使い終わったら必ずインスタンス停止**。
