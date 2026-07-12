---
name: nemo-pipeline-runner
description: >
  NVIDIA NeMoパイプライン（local-llm-workshop/nemo-pipeline）の一連の流れを
  Claude Codeで自動運転・評価ループさせるスキル。環境確認→データ準備(extract/caption/fetch)→
  S1 curate→S2 distill→(S3 DAPT)→S4 SFT→S5 GRPO→S6 eval→S7 guardrails を Makefile 経由で実行し、
  S6の合否(exit code)に基づいて前段へ戻る改善ループを回す。
  「NeMoパイプラインを一周して」「評価ループを回して」「パイプライン実行」「SFTまで進めて」
  「base測定して」「ガードレール検証して」「make evalの結果を見て次を判断して」などで発動。
  コスト・GPU・外部APIを伴う操作は必ず人間の承認を得てから実行する。
---

# NeMo Pipeline Runner — 自動運転と評価ループ

## 前提
- 作業ディレクトリ: `nemo-pipeline/`（`Makefile` があること）。無ければ探索して `cd`。
- 実行は**必ず `make` ターゲット経由**（直接dockerコマンドを組み立てない）。
- 参照: docs/10-runbook.md（手順の正）, docs/02-makefile-coverage.md（makeの守備範囲）, s6_evaluation/eval.yaml（閾値）。

## 承認ゲート（must）— 以下は実行前に必ずユーザーへ確認
| 操作 | 理由 |
|---|---|
| `make distill`（dry以外） | Anthropic API課金（概算コストを先に提示） |
| `make caption`（dry以外） | 画像を外部APIへ送信（**機微画像は送らない**: docs/05決定表）＋課金 |
| `make dapt` / `make sft` / `make grpo` | GPU長時間占有（推論ホスト兼用なら推論停止を伴う） |
| `make serve-nemotron` 初回 | ~18GBのモデルDL（HF gated同意が前提） |
| Docker network / .env の変更 | 環境破壊防止 |

## フェーズ0: 環境チェック（毎回最初に）
```bash
make -n setup >/dev/null && echo OK   # Makefile健全性
docker info >/dev/null 2>&1 || echo "Docker未起動"
nvidia-smi >/dev/null 2>&1 && echo "GPUあり" || echo "GPUなし(学習系はスキップ)"
test -n "$ANTHROPIC_API_KEY" && echo "APIキーあり" || echo "APIキーなし(distill/captionはdryのみ)"
make setup
```
GPU/キーの有無で到達可能フェーズを宣言してから進む（無いものは飛ばす。エラーで止まらない）。

## フェーズ1: 配管検証（無料・数分・常に実行）
```bash
make curate distill-dry eval-dry
```
- eval-dry の `"pass": true` を確認。falseなら**ここで停止して原因調査**（配管が壊れている）。

## フェーズ2: データ準備（必要時）
- 文書がある: `make extract IN=<dir>` → 図/スキャンが `needs_extraction` に隔離されたら
  （承認の上）`make caption IN=<dir>` か NeMo Retriever を提案。
- HFデータ: `make fetch REPO=<id> LICENSE=<カード記載>`（**商用可のみ**。NC/NDは弾かれるのが正常）。
- `data/curated/stats.json` の kept / rejected 内訳を要約して報告。

## フェーズ3: 蒸留 → base測定
```bash
# 承認後、まず小さく
make distill N_ANALYSIS=100 N_GEN=30
make serve-nemotron   # (GPUあり時)
OPENAI_BASE_URL=http://localhost:8002/v1 make eval TAG=base
```
- base の exit 2（閾値未達）は**想定内**。results/eval_base.json を「超えるべき線」として記録。

## フェーズ4: 学習ループ（評価駆動・最大3周）
```
loop:
  (承認) make sft → make eval TAG=sft
  改善判定: results/eval_{base,sft}.json を比較
  ├─ 全閾値クリア(exit 0) → GRPOへ or 完了
  ├─ 改善したが未達 → 下の分岐表で1手だけ変えて再学習（同時に2つ変えない）
  └─ 改善なし(2回連続) → 停止してレポート（人間の判断を仰ぐ）
  (承認) make grpo → make eval TAG=grpo → 同様に判定
```
**分岐表（未達メトリクス → 次の一手）**
| メトリクス | 疑う場所 | 次の一手 |
|---|---|---|
| schema_valid 低 | 出力形式の学習不足 | analysisデータ増量 / プロンプト整合(common/prompts) |
| analysis_match 低 | カテゴリ偏り・ラベル品質 | S2の層別バランス見直し・N_ANALYSIS増 |
| source_exists 低 | 忠実性 | S5報酬の重み / S2忠実性ゲート閾値 / 生成データ増 |
| citation_format 低 | 形式報酬 | penalty調整・few-shot追加 |
| 学習が発散/OOM | リソース | docs/06の絞りノブを上から順に1つずつ |

**ループ規則（must）**: held-outを学習に使わない／1周で変えるのは1要素／各周の判断を loop_report に記録。

## フェーズ5: ガードレール検証
```bash
make guardrails && make guardrails-test
```
- results/garak* の promptinject 成功率を before/after で報告（TS-07/08）。

## フェーズ6: レポート（毎回最後に）
`results/loop_report.md` に追記:
- 実行したターゲットと結果（eval_*.json の表・MLflow http://localhost:5000 の run 名）
- 各判断の理由（分岐表のどれを引いたか）／次アクション提案／かかった概算コスト

## 失敗時の定型対処
| 症状 | 対処 |
|---|---|
| nvcr.io pull 401 | `docker login nvcr.io`（user=`$oauthtoken`, pass=NGC APIキー） |
| vLLM起動失敗(Nemotron) | イメージ版をモデルカード記載版へピン（docs/06） |
| OOM | docs/06 絞りノブ①→⑤を1つずつ。`PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:64` |
| eval接続失敗 | `OPENAI_BASE_URL` とサーバ起動(healthcheck)を確認 |
| distillでrejects多発 | data/distilled/stats.json の理由別内訳→スキーマ/忠実性のどちらかを特定 |

## やらないこと（must not）
- heldout.jsonl を学習・報酬・プロンプト例に使う／閾値(eval.yaml)を勝手に下げて"合格"させる
- 承認なしの課金・GPU長時間ジョブ・機微画像のAPI送信／`.env`・秘密情報の表示や書き換え
