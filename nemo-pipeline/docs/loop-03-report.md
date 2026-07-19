# 学習ループ 3周目レポート（2026-07-19）

## 1変更の内容（ループ2で残った analysis_match 0.393 への対処）

**ラベル規約の決定化 + 再ラベル**（PR #14, #15）:

ループ2データの分析で、ambiguous カテゴリ（analysis の20%）の教師ラベルが二極化
（sectors: overall単独67% / 全列挙33%、date_range: null35% / 期間65%）しており、
exact-match 評価の構造的天井になっていたことを特定。データ増量や GRPO を投入しても
この天井に当たるため、先に規約を決定化した。

- `LABEL_SYS` に決定的規約4条を追記（曖昧→overall単独 / 明示日付なし→null /
  期間解釈の固定 / query_type 定義）。prompts.py 一元管理で蒸留・SFT・eval に一貫反映
- `make relabel`（s2 `--relabel`）: 既存分割の analysis ラベルのみ再生成（$2.7）。
  質問・分割・generation 保持でリークなし。`pre-relabel-backup/` に自動退避
- `meta.label_today` にラベル付け基準日を記録し、s6_eval が評価時に優先使用
  （再ラベル日と評価日のずれで相対日付ゴールドが全滅する事故の予防）
- 付随（観測性・PR #14）: eval に per-item 詳細（`eval_{tag}_details.jsonl`）と
  `by_category` 集計を追加。ループ2で集計値しか残らず内訳分析できなかった教訓

再ラベル結果: train 226/235 変更・valid 27/28・heldout 26/28、失敗0。
QC: ambiguous カテゴリが sectors=[overall] 100% / null 100% / summary 100% に統一。
全セクター列挙（≥5個）は 30件 → 1件。

## SFT実行（g6e.xlarge L40S・NeMo-RL v0.6.0・ハイパラはループ2と同一）

- 27 step（9 step/epoch × 3）、~25s/step、final loss 3.26、val_loss **3.32**（ループ2: 4.35）
- ループ2 ckpt は `/opt/nvidia_slm/loop2-archive/` に退避（自動レジューム事故予防）

## 結果（heldout n=37。ゴールド再定義のため base3 から再測定）

| 指標 | base3 | sft3 | 閾値 | 参考: sft2(旧ゴールド) |
|---|---|---|---|---|
| schema_valid | 0.893 | 0.857 | 1.0 ❌ | 0.786 |
| sectors_match | 0.786 | 0.679 | - | 0.571 |
| query_type_match | 0.821 | 0.786 | - | 0.75 |
| date_range_match | 0.679 | 0.679 | - | 0.50 |
| analysis_match | **0.607** | 0.536 | 0.85 ❌ | 0.393 |
| source_exists | 0.889 | **1.0** | 1.0 **✅初クリア** | 0.889 |
| citation_format | 0.889 | **1.0** | 0.95 ✅ | 1.0 |

## 所見

1. **規約決定化の効果は絶大**: base の analysis_match が 0.214 → 0.607。学習を変えずに
   ラベルを決定化しただけで +0.39 — 規約揺れが天井だった仮説がそのまま裏付けられた。
   ambiguous カテゴリは base ですら 0.833
2. **generation タスクは完成**: source_exists 1.0 / citation_format 1.0 で
   4閾値中2つをクリア（source_exists は初）
3. **新たな可視化: sft3 の analysis (0.536) が base3 (0.607) を下回る**。
   per-item 詳細による失敗13件の内訳:
   - **edge 4件**: 範囲外・実在しない話題（恐竜の骨格・月面不動産等）で gold=overall vs
     pred=misc。規約(1)が範囲外質問をカバーしておらず、訓練データの edge も
     72% overall / 28% その他と揺れが残る
   - **schema 4件**: ```json フェンス(1)、先頭 `(` (1)、**語彙外セクター
     `commodities` の発明(2)** — LABEL_SYS のスキーマ表記 `[..]` の模倣も一因の可能性
   - **「今日/本日」→ null 誤り 4件**: 規約(3)「今日=基準日のみ」の適用漏れ（学生側）
   - **セクター知識不足**: アルミ→agriculture、コーヒー→overall 等の商品→セクター対応ミス
   - **ゴールド側の誤りも2件発見**: 「今週火曜日」で end=+1日（モデルの 7/14-7/14 が正）、
     「一言で表すと」が qt=single（規約(4)違反、正は summary）

## 運用上の教訓

- push-to-s3.sh は**コード・データ変更のたびに再実行**が必要。今回、変更前に push した
  古い S3 状態をノードが sync し、旧コード・旧データで base3 を測りかけた
  （eval ログ冒頭の `基準日(today)=` と `by_category` の有無が新コードの目印）
- ノード作業は SSM 接続後 **必ず `sudo su - ubuntu`**（ssm-user のままでは docker
  permission denied・CKPT_BUCKET/HF_TOKEN 未設定）
- Mac 側で `set -a; source .env` したシェルは AWS プロファイル（gpu-account）を見失う
  ことがある → AWS CLI は素のシェルで実行する
- 結果ファイルはノードから `aws s3 sync results/ s3://$CKPT_BUCKET/results/loopN/` で
  即退避 → Mac で取得（results/ は repo 同期の対象外のため）

## 次周（ループ4）候補 — 1変更の原則

主目標は引き続き analysis_match → 0.85。失敗内訳に基づく候補:

1. **規約の完全化 + ゴールド機械検証**（推奨・最安 ~$1）: 規約(5)「範囲外・実在しない
   話題→overall」を追記、スキーマ表記 `[..]` を実例に置換、教師ゴールドを規約リント
   （qt規則・date整合の自動チェック）にかけて違反のみ再ラベル → 再SFT。
   edge 4件 + gold誤り2件 + schema一部で最大 +0.2 前後の余地
2. **セクター語彙の教材強化**: 商品名→セクター対応（ニッケル/アルミ=non_ferrous、
   コーヒー/砂糖=agriculture 等）を LABEL_SYS への一覧追記 or 蒸留データ増強で教える。
   sectors 起因の失敗 4-5件に効く
3. S5 GRPO: ラベルが決定化された今なら検証可能報酬が機能する。GPU時間増
