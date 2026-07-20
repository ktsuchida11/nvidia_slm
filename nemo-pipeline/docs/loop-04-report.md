# 学習ループ 4周目レポート（2026-07-20）

## 1変更の内容（ループ3で残った sft3 analysis 0.536 < base3 0.607 への対処）

**規約完全化 + ゴールド機械検証**（PR #17, #18, #19）:

- `LABEL_SYS` のスキーマを省略記法 `[..]` から実例に置換（学生の字面模倣による
  語彙外セクター発明への対策）。規約(5) を追記: misc/column/data/announcement は
  セクション種別なので sectors に使わない、範囲外・一覧外商品の話題→overall
- **規約リント** `common/label_lint.py`（`make lint-labels`、API不要）: 規約(1)〜(5)の
  機械検証可能部分（日付規則・overall混在・曜日指定日付・qt整合）を検査
- **違反のみ教師再ラベル** `make relabel-lint`: lint_report.json の違反 item だけ再生成。
  基準日は既存 `meta.label_today` に自動整合
- 基準日を曜日付き `2026-07-19(日曜日)` に変更（`format_today()`、S2/S6/prep_rl共用）:
  教師・学生とも暦計算に弱く「今週火曜日」の曜日ずれゴールドが再ラベルでも再発したため
- 付随バグ修正: `prep_rl_data.py` の基準日既定が実行日 → データの `meta.label_today` に。
  再ラベル日と prep-rl 実行日がずれると相対日付を1日ずれた基準日で学習していた

リント結果: ゴールド誤り計8件を検出・修正（曜日ずれ2・qt誤り3・misc使用1・ほか2）、
**train/valid/heldout 全て違反0件**（教師コスト計 ~$0.08）。
残存する既知の非決定性: 「先週X曜日」はローリング窓と暦週の2解釈が併存（リントは
単日+曜日一致+14日以内のみ検査）。

## SFT実行（g6e.xlarge L40S・NeMo-RL v0.6.0・ハイパラはループ2/3と同一）

- 27 step、final loss 3.31、val_loss 3.38（ループ3: 3.32。プロンプト変更のため厳密比較不可）

## 結果（heldout n=37。ゴールド再定義のため base4 から再測定）

| 指標 | base3 | sft3 | base4 | sft4 | 閾値 |
|---|---|---|---|---|---|
| schema_valid | 0.893 | 0.857 | **1.0** | 0.964 | 1.0 |
| sectors_match | 0.786 | 0.679 | 0.607 | 0.536 | - |
| query_type_match | 0.821 | 0.786 | 0.929 | 0.893 | - |
| date_range_match | 0.679 | 0.679 | 0.75 | 0.75 | - |
| analysis_match | 0.607 | 0.536 | 0.536 | **0.429** | 0.85 ❌ |
| source_exists | 0.889 | 1.0 | 0.889 | 0.778 | 1.0 ❌ |
| citation_format | 0.889 | 1.0 | 0.889 | 0.778 | 0.95 ❌ |

## 所見

1. **狙った箇所は完治**: base4 で schema_valid 1.0（スキーマ実例化の効果、baseですら
   崩れゼロ）、edge・ambiguous カテゴリ analysis_match 1.0（規約(5)の効果。
   ループ3の pred=misc 問題は消滅）
2. **副作用で全体は悪化**: sft4 analysis_match 0.429。失敗16件中13件が
   **sectors=["overall"] の過剰般化** — 「昨日の金スポット」「本日のニッケル」等、
   商品名を commodities には正しく入れながら sectors を overall にしてしまう。
   base4 (0.607→sectors 0.607) の時点で既に発生しており（プロンプトの overall 規約
   3箇所強調が原因）、SFT が増幅（ambiguous/edge の訓練ゴールド100% overall 化）。
   **規約に「商品名→対応セクター」の対抗規則が無いことが根本原因**
3. summary カテゴリで date_range 0/3: 「本日の相場全体を一言で」等に null を出す
   （訓練データの summary系が大半 null → 疑似相関を学習）
4. generation 悪化の内訳: (a) 正答なのに出典タグ欠落1件、(b) unanswerable で
   「レポートに記載がありません」と**正しく拒否したのに 0点** — 蒸留の品質ゲートは
   出典なし拒否を許容する一方、評価は全回答に出典を要求する**教材と評価の非対称**を発見
5. base3→base4 の sectors_match 低下（0.786→0.607）もプロンプト起因で一貫:
   規約強化は ambiguous/edge を直したが single/trend/comparison の特定セクター選択を
   萎縮させた。1変更の効果が正負両方向に出た教科書的な回

## 運用上の教訓

- **S3同期は2系統**: `push-to-s3.sh --with-repo` は repo/ から `nemo-pipeline/data/*` を
  除外し data/distilled/ を別プレフィックスに置く。ノードでは repo 同期と
  `aws s3 sync s3://$CKPT_BUCKET/data/distilled/ data/distilled/` の**両方**が必要
  （今回 data だけ古いまま base4 を測りかける事故。検知は label_sys.txt の内容grep）
- インスタンス停止で `--rm` コンテナ(mlflow)が名前を掴んだまま残ることがある →
  `docker rm -f mlflow && make tracking`
- 環境変数付き make はコピペで改行が入ると値が落ちる → `export` を先に1行で

## 次周（ループ5）候補 — 1変更の原則

主目標は analysis_match 0.429 → 0.85。overall 過剰般化の解消が最優先:

1. **規約(0) 商品→セクター対応表の明示**（推奨・教師コスト0）: LABEL_SYS に
   「ニッケル・アルミ・銅=non_ferrous / 金・銀・パラジウム=precious_metals /
   コーヒー・砂糖・綿花・トウモロコシ・小麦・大豆=agriculture / 原油・WTI=crude_oil /
   鉄鉱石・鉄鋼=steel / 天然ガス・LNG=natural_gas」等の対応表を追記し、
   「overall は商品・セクター言及が無い場合のみ」と優先順位を明確化。
   **ゴールドは既に正しい（教師は対応できている）ため再ラベル不要** —
   prep-rl 再実行 + 再SFT のみ（~$2-3）。失敗13件の大半に直接効く
2. **unanswerable の教材・評価整合**: 蒸留ゲートと同じ「記載がありません+出典なし」
   許容を eval 側にも実装（reward.py の単一概念共有に合わせる）
3. S5 GRPO: sectors の検証可能報酬（gold一致）で overall 逃げを直接罰する
