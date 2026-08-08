---
name: nemo-pipeline-runner
description: >
  NVIDIA NeMoパイプライン（nemo-pipeline/）をClaude Codeで自動運転・評価ループさせるスキル。
  loop1〜10の実測知見（手法選択・評価3層・課金ゲート・実機罠・リモートGPU運用）を運用知として内蔵。
  データ準備→S1 curate→(S2 distill)→(S3 DAPT)→S4 SFT→S5 GRPO→S6 eval を Makefile 経由で実行し、
  S6の合否と診断定石に基づいて次の一手を判断する。
  「NeMoパイプラインを回して」「評価ループ」「次のループを設計して」「base測定して」
  「SFT/GRPO/DAPTどれを使うべきか」「ノードで学習して」などで発動。
  コスト・GPU・外部APIを伴う操作は必ず人間の承認を得てから実行する。
---

# NeMo Pipeline Runner — 自動運転と評価ループ

## 前提

- 作業ディレクトリ: `nemo-pipeline/`（`Makefile` があること）。実行は**必ず `make` ターゲット経由**。
- **学習・配信はリモートGPUノード**（AWS spot g6e系・L40S）で行う。ローカル（DevContainer/Mac）は
  実装・テスト・$0配管検証まで。ノードとの同期・起動停止は [references/operations.md](references/operations.md)。
- 参照の正: docs/10-runbook.md（基本手順）、docs/loop-XX-report.md（各ループの実測結論）、
  s6_evaluation/（評価定義）。本スキルの references/ は loop1-10 の蒸留版で、矛盾したら loop report が正。

## ループの型（1周 = 以下を順に）

1. **計画**: 前ループ総括の「次への示唆」から1テーマを選び、docs/loop-XX-plan.md に設計
   （仮説・変更点・評価軸・概算コスト・承認ゲート）を書いて PR → ユーザー承認
2. **手法選択**: SFT / GRPO / DAPT の適用可否を [references/decision-table.md](references/decision-table.md) の
   意思決定表で判断（loop8/9/10 の失敗はすべて「手法とタスク×データ×base強度の不一致」が原因）
3. **実装**: ローカルで $0 実装 + ユニットテスト + dry 配管（デバッグをGPU時間から切り離す）
4. **配管検証（小）→ 本走**: 課金は [references/billing-gates.md](references/billing-gates.md) のゲート運用
   （概算→小規模実測→再見積り→承認）に従う
5. **評価**: [references/evaluation.md](references/evaluation.md) の評価3層＋リーク検査＋統計的判定
6. **総括**: docs/loop-XX-report.md に「何を試し・何がダメで・なぜか・次どうするか」を実測値で記録。
   罠を踏んだら [references/traps.md](references/traps.md) 形式でカタログ化

## 承認ゲート（must）— 実行前に必ずユーザーへ確認

| 操作 | 理由 |
|---|---|
| GPUノードの起動・学習ジョブ（`make dapt` / `make sft` / `make grpo`） | スポット課金（概算を先に提示） |
| `make distill` / `make probe-gen` / judge・verify 等の教師API | API課金＋外部送信 |
| `make caption`（dry以外） | 画像を外部APIへ送信（機微画像は送らない） |
| 本走の続行判断（配管検証の実測後） | 実測レートで再見積りしてから |
| チェックポイント・データの削除 | S3バックアップ確認後のみ |

## フェーズフロー

```
フェーズ0 環境チェック: make -n setup / docker info / ノード稼働状態（不要なら止まっているか）
フェーズ1 $0配管検証:   make curate distill-dry eval-dry（+ 各ループの *-dry）→ pass確認
フェーズ2 データ準備:    make fetch / fetch-edinet-bench / curate-curator（Curator前段）
                        → リーク検査（evaluation.md §リーク検査）を通ってから学習データ化
フェーズ3 base測定:     serve → make eval TAG=base*（合否の物差し。exit 2 は想定内）
フェーズ4 学習:         decision-table.md で手法を選び、billing-gates.md のゲートを経て実行
フェーズ5 評価・診断:    evaluation.md の3層判定 + 診断定石（GPU追加課金なしの深掘りを先に）
フェーズ6 総括レポート:  loop-XX-report.md + メモリ/スキル更新
```

## 判断の要点（詳細は references/）

- **手法選択を間違えると逆効果**: baseが既に強いタスクへのSFTは悪化させ（loop9 round-1）、
  暗記済みデータのGRPOは学習信号ゼロ（loop8）、1epochの生テキストDAPTは指標に効かない（loop10）。
  必ず decision-table.md を先に引く
- **評価は3層で分離**: held-out loss（分布適応）と probe（知識取り出し）と非退行（忘却）は
  それぞれ別の答えを返す（loop10で実証）。1つの指標で合否を語らない
- **リーク検査なしの評価は無効**: loop10 では検査が223文書の実リークを検出した。検査なしなら評価汚染だった
- **差の解釈は SE と比較**: n=300 で SE±0.02。差がSE内なら「同等」であり改善でも退行でもない
- **フラットな結果はまず $0 で診断**: train reward 推移と item単位 fix/break 差分で
  「config弱い / base天井 / drift」を切り分けてから追加課金を提案する

## NeMoコンポーネント消化状況（2026-08 時点）

| コンポーネント | 状態 |
|---|---|
| Curator（言語ID/品質/dedup） | 済（CPU版。PII/GPU dedup はGPU専用と確定） |
| NeMo Framework DAPT（AutoModel経路） | 済（loop10。Mamba+Attentionハイブリッドは Megatron変換不可） |
| NeMo-RL SFT / GRPO | 済（loop1-9） |
| S7 Guardrails（NeMo Guardrails/garak） | **未実機検証**（実装はあり: make guardrails） |
| NeMo Retriever embedding NIM（RAG） | 済（loop12。llama-3.2-nv-embedqa-1b-v2 TRT FP8/L40S・dim2048。probe 0.0402→0.4724 で DAPT知識注入をクローズ）。**rerank NIM は未消化** |

## やらないこと（must not）

- heldout を学習・報酬・プロンプト例・dedup以外の用途に使う／閾値を下げて「合格」させる
- 承認なしの課金・GPU長時間ジョブ・機微データの外部送信／`.env`・秘密情報の表示や書き換え
- 配管検証（dry・小規模）を飛ばした本走／リーク検査を飛ばした学習データ投入
- マージ依頼を全コミットpush前に出す（スタックPR罠: operations.md §PR規律）
