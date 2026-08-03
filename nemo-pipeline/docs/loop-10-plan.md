# ループ10 設計書 — Curatorフルデータ基盤 + DAPT（base能力の底上げ）

作成: 2026-07-29（設計フェーズ・課金操作なし）。承認ゲート通過後に実装・実行へ進む。

## 1. 目的と位置づけ

loop9 の最終結論（docs/loop-09-report.md §8）:

- base(Nemotron-Nano-9B-v2-Japanese) は検証可能な簡潔数値QAで既に強く、SFT/GRPO の仕上げ工程では階段が出ない（fixed≈broken の入替に留まる）
- ゲイン幅は「タスク × データ × base強度」で決まる

→ loop10 は仕上げ工程ではなく**上流（データ基盤 + DAPT）で base の素の能力そのものを底上げ**する。
役割分担の再確認: **DAPT=素の能力 / SFT=形式・タスク適応 / GRPO=検証可能軸の仕上げ**。

### 教材としての目標（NeMo 未使用コンポーネントを通す）

| コンポーネント | 未使用機能 | loop10 での消化 |
| --- | --- | --- |
| NeMo Curator | PII検出・秘匿化 / 品質分類 / 言語ID / 大規模dedup | S1 を Curator エンジンで拡張（P2） |
| NeMo Framework (`nvcr.io/nvidia/nemo:25.04`) | 継続事前学習（DAPT） | S3 を初実行（P3/P4）。`run_dapt.sh` の `exit 1` スタブを実レシピ化 |

## 2. 現状の資産と不足

- `s1_curate.py`: 正規化→品質→PII(regex)→ライセンスゲート→exact/fuzzy dedup 実装済み。Curator 委譲は exact dedup のみ
- `s3_pretraining/`: `prep_corpus.py`（S1出力→corpus.jsonl）は動作、**学習レシピ未確定**（`run_dapt.sh` は `exit 1`、`dapt.yaml` は骨子のみ）
- 現 curated.jsonl ≈ 46MB ≈ **1,500万字 — DAPT の意味が出る目安「数千万字〜」に未達** → 大規模収集が必要

## 3. コーパス計画（すべて商用可ライセンスのみ）

| Tier | ソース | ライセンス | 規模見込み | 前提 |
| --- | --- | --- | --- | --- |
| 1（即時） | SakanaAI/EDINET-Bench 全3config（earnings_forecast / fraud_detection / industry_prediction） | PDL 1.0 | 生~113MB → dedup後 3,000〜6,000万字 | なし（HF から即取得可・実測済み） |
| 2（本命） | **EDINET API 直取得**: 直近1〜2年の有価証券報告書・半期報告書全文 | PDL 1.0 | 生数億字 → 品質選別後 **1〜2億字** | **無料APIキー登録（ユーザー作業）** |
| 3（補助） | Wikipedia-ja 金融・経済カテゴリ抽出（izumi-lab/wikipedia-ja 等） | CC BY-SA | +1,000〜3,000万字 | なし |

- 目標コーパス: **合計 1〜2億字（≈0.7〜1.5億トークン）**。Tier 1+3 のみでも配管検証と小規模DAPTは成立（その場合は効果控えめの想定で判定）
- 除外継続: NC/ND データ（JaFIn 等）は S1 ライセンスゲートで遮断

> **P1 実測更新（2026-07-29・PR #51）**: Tier 1 実測は **2,551 文書・9,072万字**（見込み 3〜6千万字を上回り）
> → Tier 1 単独でほぼ目標到達。**Tier 2（EDINET API・要APIキー）は初回 DAPT の必須条件から外し、
> ③knowledge-probe が有望だった場合のスケールアップ手段に降格**。Tier 3 も同様に任意。

## 4. パイプライン設計

### P1 収集（$0・Mac/DooD）

- `fetch_dataset.py` で Tier 1/3、新規 `fetch_edinet.py`（EDINET API v2: 書類一覧→ZIP取得→CSV/XBRL本文抽出）で Tier 2
- 出力は既存 S1 入力形式 `/data/raw/*.jsonl`（`{"text":…, "meta":{"license":…, "source":…}}`）に統一

### P2 キュレーション＝Curator 未使用機能の消化（$0・CPU中心）

> **P2 実装更新（2026-07-29 実測）**: pip 版 Curator は **1.3.0 の新アーキテクチャ（Ray ベース Pipeline/Stage API）**で、
> 設計時に想定した旧 API（`nemo_curator.modules` の ExactDuplicates/PiiModifier 等）は廃止されていた。
> CPU（`nemo-curator[text-cpu]`・arm64）で使えるもの／使えないものを実測で切り分け、以下の構成に確定:
>
> | 機能 | 設計時想定 | 実装（確定） |
> | --- | --- | --- |
> | 言語ID | fastText lid | ✅ Curator `FastTextLangId`（lid.176.ftz 自動取得）— `curator_stage.py` |
> | 品質フィルタ | Curator 品質分類器 | ✅ Curator heuristic（`RepeatedLinesByCharFilter` = 文字ベースで日本語適合）。**`NonAlphaNumericFilter` は英語専用（ソース明記）で日本語が74%非英数扱い→全滅、不採用**。記号過多検査は本段 quality() の Unicode 安全実装が担う。語ベース・torch 分類器も不採用 |
> | PII | Curator PII (Presidio) | ❌ pip 非同梱（GLiNER チュートリアルは GPU 前提）→ **EDINET 実データ校正済みの既存 regex を維持**（公開開示文書で PII リスクは低い） |
> | fuzzy dedup | Curator 委譲 | ❌ GPU 専用（`deduplication-cuda12` extra）→ **自前 minhash をユニバーサルハッシュ+numpy で 64 倍高速化**（Jaccard 推定量は同一・校正済み閾値有効のまま） |
>
> 実行系: `make curate-curator` = Curator 前段（/data/raw → 言語ID+品質 → /data/raw_curator）→ 既存 s1_curate 本段
> （PII regex・ライセンスゲート・exact/fuzzy dedup・メタデータ → /data/curated）。Curator が import 不能な環境では exit 3 で明示区別。

### P3 DAPT 配管検証（GPU小・承認ゲートA）

- `run_dapt.sh` を NeMo 25.04 の継続事前学習レシピで実体化（NeMo 2.0 API。HF→NeMo 変換 or HF直マウントはレシピ確認時に確定）
- Tier 1 コーパスのみ・**50〜100 step** で「データ→学習→ckpt→HF変換→配信→eval」を一気通貫確認
- ここで確定する技術判断: **9B full-param FSDP が 4×L40S(192GB) に載るか**（載れば本走は full。知識注入は LoRA より full が定石。載らなければ LoRA r=64〜128 に fallback し、効果期待値を下げて判定）

### P4 DAPT 本走（GPU・承認ゲートB）

- 全コーパスで 1 epoch（+損失次第で追加）。seq 4096・bf16・MLflow 記録
- 完了後: 変換 → serve → S6 評価（§5）

### P5 評価データ生成（教師API・承認ゲートC・**8/1 の API 上限解除後**）

- held-out にした EDINET 文書（DAPTコーパス**外**）から knowledge-probe QA ~200件を教師生成

## 5. 評価設計（3層・評価ファースト）

| 層 | 物差し | 判定 |
| --- | --- | --- |
| ① 直接効果 | held-out corpus の loss/ppl（DAPT前後） | 低下していなければ即撤収（S3 README の原則どおり「捨てる」） |
| ② 非退行 | 既存 `make eval BASELINE=base8` + `make eval-finqa BASELINE=base9b` | 統計的に非退行（catastrophic forgetting 検査） |
| ③ 階段 | 新 knowledge-probe QA（DAPTコーパス内知識・base は未見） | dapt10 > base が loop10 の「階段」。P5 完了後に判定 |

- **リーク検査必須**: 既存 heldout（finqa 300 / analysis 70+ext）× DAPT コーパスの n-gram overlap 検査を corpus 生成時に組み込む。knowledge-probe の元文書はコーパスから除外（4者排他の運用を踏襲）
- loop9 の教訓の継承: ③は「base が弱い＝伸びしろのある軸」を意図的に作る（コーパス固有知識は base 未見が保証される）

## 6. フェーズ・承認ゲート・コスト概算

| フェーズ | 内容 | コスト概算 | ゲート |
| --- | --- | --- | --- |
| P0 | 本設計書 | $0 | 済 |
| P1 | 収集（EDINET-Bench / EDINET API / Wikipedia） | $0 | EDINET APIキーのみユーザー作業 |
| P2 | Curator 拡張キュレーション | $0（CPU） | — |
| P3 | DAPT 配管検証（GPU 1〜2h） | **$5〜10** | **ゲートA: 実行前に承認** |
| P4 | DAPT 本走（1〜1.5億トークン・1ep 5〜7h 想定 + eval） | **$30〜60** | **ゲートB: P3 実測スループットで再見積り→承認** |
| P5 | knowledge-probe QA 教師生成 ~200件 | **$5〜10** | **ゲートC: 8/1 以降・承認** |
| 合計 | | **$40〜80** | 各ゲートで個別承認 |

- GPU 単価想定: g6e.12xlarge spot ≈ $4/h（実測で補正）。full-param が載らず LoRA になった場合 P4 は $15〜30 に低下
- 累計 ~$200 消費済みのため、**課金操作は必ず直前に実測ベース概算を再提示**する（運用ルール踏襲）

## 7. リスクと撤収基準

- **効果が出ないリスク（最大）**: 有報テキストの知識が評価軸に乗らない可能性。①held-out loss が下がらない→P4 前に撤収、③が base 同等→「9B への DAPT 知識注入はこの規模では不発」を結論として総括（それ自体が教材成果）
- **忘却リスク**: ②非退行で検出。悪化時は LoRA 版 or 低LR再走を 1 回だけ試行し、ダメなら撤収
- **レシピリスク**: NeMo 25.04 の実キーは実機確認まで不明（loop8 の v0.6.0 差分5連発の前例）→ P3 を小さく切ってデバッグをそこに閉じ込める
- **規模リスク**: EDINET API 取得が想定より細い場合は Tier 1+3 で縮小続行し、期待値を明示的に下げる

## 8. ユーザー決定事項（承認待ち）

1. **この設計での進行可否**（特にコーパス Tier 構成と総額 $40〜80 の枠）
2. **EDINET API キーの登録**（無料・Tier 2 の前提。無しなら Tier 1+3 縮小版で進行）
3. **GPUノード停止**: P1/P2 は CPU/Mac で完結するため、稼働中の i-0e2549759e6e56b5c は**即停止推奨**（GRPO成果 checkpoints/grpo/step_236 の S3 退避確認後、Mac から stop-instances）
