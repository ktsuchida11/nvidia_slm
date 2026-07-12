# NeMo版 計画書 — Linuxコンテナで貫くLLMパイプライン

> 前提変更版: 「データ収集・管理 → 蒸留・メタデータ → 事前学習 → 事後学習 → 強化学習 → 評価 → ガードレール作成・検証」を、**NVIDIA NeMo製品群**で Linux(コンテナ) 上に構築する。
> 学習実行は **Colab または NVIDIAリソース(Brev/DGX Cloud)/EC2(NGCコンテナ)**。ローカルでは学習しない。
> 不変の原則: **評価ファースト**(指標・閾値・held-outを先に固定しbaseを先に測る) / フォールバック常設 / 実データはダミー・合成で代替(許諾後に差し替え)。

---

## 0. まず正直な整理: NeMoは「二層」ある

| 層 | 中身 | 費用/前提 | 本計画での採用 |
|---|---|---|---|
| **ライブラリ/フレームワーク層** | NeMo Framework, NeMo Curator, NeMo-RL, NeMo Evaluator(SDK), NeMo Guardrails(library) | **無償・Apache 2.0**。Docker/NGCコンテナで動く | ✅ **これを使う** |
| マイクロサービス層 | NeMo Customizer/Evaluator/Guardrails/Data Curator(microservices), NIM | NVIDIA AI Enterprise(有償)・**K8s前提** | ❌ 今回は不採用(将来の商用展開時に載せ替え可能。設定の多くは互換) |

- Guardrailsは「libraryで開発 → microserviceで本番」が公式の移行パス(設定はポータブル)。今回はlibraryで完結させ、**将来K8s化する時にmicroservicesへ昇格**できる形にしておく。
- ⚠ NVIDIA Build(build.nvidia.com)のエンドポイントは**評価・テスト専用で本番禁止**・機密投入禁止。デモ用途に限定する。

## 1. パイプライン全体像(ステージ → NeMo製品 → 実行場所)

```
[S1 収集・管理]──[S2 蒸留・メタデータ]──[S3 事前学習(DAPT)]──[S4 事後学習(SFT)]──[S5 強化学習(GRPO)]──[S6 評価]──[S7 ガードレール]
 NeMo Curator      Curator(SDG)+Claude      NeMo Framework        NeMo Framework       NeMo-RL             NeMo Evaluator   NeMo Guardrails
 (Linuxコンテナ)    (CPUコンテナ+API)        (NGC/GPU: 借り)       (NGC/GPU: 借り)      (NGC/GPU: 借り)      (コンテナ)        (コンテナ+検証)
```

| # | ステージ | 製品/コンテナ | 実行場所 | 入出力(成果物) |
|---|---|---|---|---|
| S1 | データ収集・管理 | **NeMo Curator**(`nvcr.io/nvidia/nemo-curator`) | ローカルLinux/CPUコンテナ(小規模なら十分。大規模はGPU) | 生テキスト(官公庁公開/EDINET(商用可)/公開金融/ダミー問合せ) → クリーニング・**exact/fuzzy重複排除**・品質/ドメイン分類(メタデータ付与)・PII除去 → `data/curated/*.jsonl` |
| S2 | 蒸留・メタデータ | Curatorの合成データ(SDG)モジュール ＋ 教師API(Claude Sonnet) | CPUコンテナ + API | 合成問合せ→教師ラベル(構造化JSON/出典付き回答)。**メタデータ**(難易度/カテゴリ/ライセンス/出所)をスキーマで強制 → `data/distilled/*.jsonl`(train/valid/**held-out**分割) |
| S3 | 事前学習(継続事前学習=DAPT) | **NeMo Framework**(`nvcr.io/nvidia/nemo`) | **借りGPU**(EC2 NGC / Brev)。任意ステージ | 金融コーパスでの軽い継続事前学習。**注意: フル事前学習は予算外**。効果が薄ければスキップ可(評価が判断) |
| S4 | 事後学習(SFT/LoRA) | NeMo Framework(SFT/PEFT) | 借りGPU(1×A100/L40Sで可) | S2の蒸留データでSFT → LoRAアダプタ/HFチェックポイント |
| S5 | 強化学習(GRPO) | **NeMo-RL**(`NVIDIA-NeMo/RL`) | 借りGPU(**下記GPU現実表**) | 検証可能報酬(既存スコア関数流用: スキーマ/sectors/出典/数値grounding)でGRPO |
| S6 | 評価 | **NeMo Evaluator SDK**(100+ベンチ) ＋ 既存`make eval`(アプリ層) | コンテナ(推論はOpenAI互換エンドポイントに向ける) | base vs SFT vs GRPO 横並び。**S0で決めた閾値**で合否判定 |
| S7 | ガードレール作成・検証 | **NeMo Guardrails**(library, 5レール: input/dialog/retrieval/execution/output) ＋ **NemoGuard安全モデル**(Content Safety/Topic Control/Jailbreak/PII) | コンテナ(`nemoguardrails server`)。検証=`nemoguardrails evaluate`+**garak** | `config.yml`+Colangレール → 攻撃カタログでインジェクション成功率/PII漏洩率を計測 |

**S0(最初にやる)**: 評価設計。指標・合格閾値・held-out評価セットをS1の前に固定し、**baseモデルを先に測る**。ここは旧計画と同一(評価ファースト)。

## 2. モデルと学習の現実(GPU見積もり)

- **モデル**: 既定は **NVIDIA-Nemotron-Nano-9B-v2-Japanese**（商用可・NeMo最適・日本語SoTA級／配信はvLLM必須。**GRPOもNeMo-RLが対応済**。詳細 docs/06）。代替に Qwen3.5-4B(解析)/9B(生成)。
- **GPU現実表(目安)**:

| ジョブ | 最小構成 | 推奨 | 備考 |
|---|---|---|---|
| S1 Curator(小規模) | CPUのみ | 1×GPU(分類/埋め込み高速化) | 数万件ならCPUで足りる |
| S3 DAPT(軽量) | 1×A100 80GB | 2〜4×L40S | LoRA/短期。任意 |
| S4 SFT/LoRA 4B | 1×L40S 48GB | 1×A100 80GB | Colabでも可(後述) |
| S4 SFT/LoRA 9B | 1×A100 80GB | 2×L40S | |
| S5 GRPO 4B | 1×A100 80GB | 2×L40S | rollout分メモリ増 |
| S5 GRPO 9B | 2×A100 / **4×L40S(g6e.12xlarge spot ≈$2.5〜4/h)** | 1ノード8GPU | **単GPUは無理筋**。ここがNeMo路線の主コスト |

- **前提更新(ローカル64GB GPU保有時)**: S4/S5の大半はローカルで完結可（4B全工程・9B SFT=快適、9B GRPO=ギリギリ可）。クラウドは「9B GRPOを余裕を持って回す時・ローカル占有を避けたい時」の保険に格下げ。詳細は docs/00-environment.md と runbook Step 5-L。
- **Colabの位置づけ(正直)**: NeMo Framework/NeMo-RLはRay・マルチプロセス前提でColabと相性が悪い。**Colab=小実験(4B SFT等)・配布ノートブック用**、**NeMo正式ルートはEC2(NGCコンテナ)/Brev/DGX Cloud** と割り切る。旧計画のUnsloth+Colabは「軽量代替ルート」として並存(同じデータ・同じ評価で比較可能にしておく)。
- 予算感: S4+S5(4B→9B)で **20〜60 GPU時間 ≈ $60〜240**(spot変動)。S3を足すと+α。旧計画よりやや高いが、パイプラインの学習価値(NeMo習得)を含めて投資判断。

## 3. コンテナ設計(Linux)

- 各ステージ=**ジョブ型コンテナ**(常駐しない)。`Makefile`で `make curate / distill / sft / grpo / eval / guardrails` を統一入口に。
- 常駐するのは **guardrails server** と(既存の)LiteLLM/Langfuse/推論サーバのみ。
- 成果物は `data/ → checkpoints/ → results/` のディレクトリ契約で受け渡し(ステージ間の疎結合)。学習ジョブはS3/GCSにチェックポイント退避(spot中断前提)。
- NGCコンテナはサイズが大きい(20GB級)。**GPUホスト/借りGPU側でpull**し、ローカルはCurator/Evaluator/Guardrailsの軽量系のみ。

## 4. 評価とガードレール検証(合格基準)

- S6: 旧計画の指標を継承 — 解析: `query_analysis_match ≥ base`・スキーマ100%・ループ0% / 生成: `source_exists=100%(ゲート)`・出典≥95%・tone≥3.5。NeMo Evaluatorでベンチ(日本語金融+汎用)、アプリ実測は既存`make eval`。
- S7: レール発火を含む**セキュリティ指標を先に固定** — インジェクション成功率(garak) / PII漏洩率 / 忠実性違反率。`nemoguardrails evaluate`(topical/moderation/fact-check/hallucination) + garak をCIに。before/after(レール無し→有り)を記録。

## 5. リスクと正直な注記

| リスク | 対処 |
|---|---|
| NeMoスタックの学習コスト(Ray/Hydra/Megatron流儀) | まず4Bで全ステージを一周(パイプライン検証)→9Bへ |
| 9B GRPOのGPU要件が旧計画(Unsloth)より重い | 4×L40S spot前提で予算化。軽量代替=Unslothルートを併存 |
| microservices(K8s/有償)との混同 | 本計画はライブラリ層のみ。移行パスは確保 |
| NVIDIA Buildエンドポイントの本番禁止 | デモ限定。本番はセルフホスト(NIMまたはvLLM。Nemotron既定はvLLM) |
| NemoGuard安全モデルのライセンス | NVIDIA community license系。商用条件を導入前に確認 |
| フル事前学習への期待 | 予算外。DAPTに限定し、**評価が改善を示さなければ切る**(評価ファースト) |

## 6. 着手順(チェックリスト)

1. [ ] **S0**: 指標・閾値・held-out確定 → base測定(既存`make eval`+Evaluator)
2. [ ] `templates-nemo/` を配置し `make setup`(ディレクトリ契約とコンテナpull確認)
3. [ ] S1: `make curate`(Curatorで清掃・重複排除・メタデータ付与)
4. [ ] S2: `make distill`(合成+教師ラベル+スキーマ検証+分割)
5. [ ] 借りGPU(EC2 NGC/Brev)を用意し S4: `make sft`(まず4B)
6. [ ] S5: `make grpo`(4Bで報酬・配管検証 → 9B)
7. [ ] S6: `make eval`(base vs SFT vs GRPO、閾値判定)
8. [ ] (任意)S3: DAPTを試し、S6で効果判定。無ければ捨てる
9. [ ] S7: `make guardrails` → `make guardrails-test`(evaluate+garak, before/after)
10. [ ] 合格モデルを vLLM で配信（既定bf16／任意でfp8）→ 既存LiteLLM/アプリ統合(旧計画7章に接続)
