# ループ10 レポート — Curatorデータ基盤 + full-param DAPT（総括）

期間: 2026-07-29〜08-08。計画: docs/loop-10-plan.md（PR #50）。実装: PR #51〜#57。

## 1. 問いと答え

**問い**: loop9で確定した「base天井」を、上流（大規模データ基盤 + DAPT）で上げられるか。

**答え**: **この規模（8千万字・1epoch）では上がらない。** full-param DAPTはドメイン分布への適応
（held-out ppl −10.6%）を確実に起こすが、①事実知識は各事実1回の露出では重みから取り出せる形で
定着せず（closed-book probe: base と完全同点）、②生テキスト学習の代償として指示追従・形式遵守が
一律に侵食される（既存2評価とも退行）。

| 判定軸 | 物差し | 結果 |
| --- | --- | --- |
| ① ドメイン吸収 | corpus_val（学習外25文書）loss | ✅ **0.971→0.860（ppl 2.64→2.36, −10.6%）** |
| ② 非退行（忘却検査） | analysis: base8=0.8361 / finqa: base9b=0.7885 | ❌ **0.7213（−0.115）/ 0.7479（−0.041）**。schema 0.934・format 0.883も低下 |
| ③ 知識の取り出し | closed-book probe QA 199問（probe種文書は学習コーパス内） | ➖ **dapt10 0.0402 = base10 0.0402（同一8問のみ正解・dapt10固有の正解ゼロ。pred相違56問=配信切替は検証済み）** |

## 2. 各フェーズの実績

- **P1 収集**: EDINET-Bench全3config×全split→平文化・doc_id一意化・横断dedup（PR #51）。
  **2,551文書・9,072万字**（見込み3〜6千万字を上回りTier2=EDINET API直取得は不要化）
- **P2 キュレーション**: NeMo Curator 1.3.0（pip text-cpu・Rayベース新API）で言語ID+品質フィルタ前段
  + 自前minhashの64倍高速化（PR #52）。**curated 2,530文書・8,924万字**、PII 89件、fuzzy重複22
- **P3 配管検証**: NeMo 2.3 AutoModel経路の実レシピ化+リーク検査付き3分割（PR #53）。
  **リーク検査が223文書の実リークを検出**（既存heldoutがEDINETチャンク由来のため。検査なしなら評価汚染だった）。
  スループット実測: full-param 1,076tps / LoRA r64 2,548tps（4×L40S・seq4096）
- **P4 本走**: full-param 530step=1epoch・17.9h完走（ゲートB承認・実費~$75）。train loss 0.98→0.84-0.89
- **P5 knowledge-probe**: probe種文書50件（学習コーパス**内**）から教師生成199問（~$3）→
  closed-book評価はローカル採点（数値=相対誤差1%・文字列=正規化包含）

## 3. なぜ③が同点なのか（設計への含意）

1epoch = **各事実の露出は1回**。LLMの事実定着には「同一事実の多様な言い換えでの複数回露出」が
必要というのが通説で、今回の結果はそれを9B・日本語・実データで裏付けた。①のppl改善は
「文体・語彙・数値パターンへの分布適応」であり「検索可能な知識の書き込み」ではない — この2つを
区別して測れる評価設計（val loss と probe の分離）にした価値が出た。

**次にDAPTで知識注入を狙うなら**: (a) 多epoch + 事実のパラフレーズ増幅（教師で言い換え合成）、
(b) DAPT後にSFTを重ねて形式を回復（②の退行は標準パイプライン DAPT→SFT→RL の裏返し）、
(c) ただし費用対効果はRAG（検索で文書を渡す）と要比較 — closed-book知識が本当に必要かの問いに戻る。

## 4. 実機の罠カタログ（nemo:25.04 / NeMo 2.3 AutoModel / vLLM）

| # | 罠 | 対処（コミット済み） |
| --- | --- | --- |
| 1 | nemo:25.04にmlflow非同梱→MLFlowLogger生成で全rank死 | mlflow-skinny導入+try/except（PR #54） |
| 2 | transformers 4.51のNemotronHはFA2未対応・sdpaも未宣言→eager落ち | eager受容（attention層は少数派。P3実測で影響込みのtps取得） |
| 3 | NeMo LoRA既定target_modulesはMegatron層名→HF NemotronHに0マッチ→「optimizer with params」死 | `target_modules=['*_proj']`明示（PR #55） |
| 4 | LoRA+grad ckpt非互換（automodel経路はenable_input_require_grads不呼→勾配パス切れ） | LoRA時grad ckpt自動無効化（PR #55） |
| 5 | mlflow 3.xがMetric.stepにint強制、Lightning経由でfloatが届き死 | ロガーでintキャスト（PR #56） |
| 6 | **NeMo保存ckptのconfig.jsonにFSDPラッパー名が混入**（"FSDPNemotronHForCausalLM"）→vLLM起動拒否 | `sed`でNemotronHForCausalLMへ修正。カスタム.py/chat_templateも非同梱な点に注意 |
| 7 | vllm/vllm-openai:v0.25.0-cu129-ubuntu2404はビルド不良（torchcodecがlibnvrtc.so.13要求で即死） | 配信は実績ある`latest`（digest ffb2d59b）を使用 |

補足知見: ckptは`<name>/hf_weights/`に**HF形式で直接保存**される（変換工程不要）。
学習中のvalidationはNeMo finetuneでは走らない→held-out lossはオフライン測定が正解（§1①の手法）。

## 5. 運用の学び

- `make dapt ... | tee` は **Ctrl-Cでコンテナが死なずGPU占有が残る** → 停止は
  `docker rm -f $(docker ps -qf ancestor=nvcr.io/nvidia/nemo:25.04)`
- NCCL watchdogタイムアウト（rank0が10分停止）が1回発生 → dmesg Xidなし・再走で再発せず=一過性。
  **長走前にディスク余裕必須**（ckpt 50GB。ディスクフルはハング→NCCLタイムアウトの原因になる）
- nemo:25.04は展開~60GB。旧イメージ・旧ckptの整理で142GB解放してから導入
- **スタックPR罠が2度目の再発**（PR #54が1コミット目のみでマージされ3コミット宙吊り→PR #55で回収）。
  以後「マージ依頼は全コミットpush完了後」を徹底
- heredocのターミナル貼り付けは行頭文字欠け事故あり → `cat > file` + `py_compile`検証の2段構え

## 6. コスト実績（loop10）

GPU（P3計測+P4本走18h+評価~4h ≈ 24h×$4）≈ **$95** + 教師API（probe生成）≈ **$3**。
P0-P2は$0。ゲート見積り（P3 $5-10 / P4 $65-80 / P5 $5-10）に対しP4がデバッグ再走込みでやや超過。

## 7. 最終ゴール（自律ループのスキル化）への含意

- **「タスク×データ×base強度」の運用知に追加**: DAPTは「分布適応」と「知識注入」を区別して設計する。
  1epochの生テキストDAPTで指標ゲインを期待してはならない。指標に効かせるには
  多epoch×パラフレーズ or DAPT→SFT連結が前提で、その前にRAGとの費用対効果比較を挟むこと
- **評価3層（held-out loss / 非退行 / probe）+ リーク検査**は、DAPT系工程の合否判定の定石として
  スキルに組み込む価値が確認できた（①〜③がそれぞれ別の答えを返した＝分離した意味があった）
- NeMoコンポーネント消化状況: Curator（言語ID/品質/dedup=済、PII/GPU dedup=CPU環境では不可と確定）、
  NeMo Framework DAPT=済（AutoModel経路）。未消化はS7 Guardrails / NeMo Retriever
