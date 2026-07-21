# 学習ループ 5周目レポート（2026-07-21）

## 1変更の内容（ループ4の sectors=["overall"] 過剰般化への対処）

**規約(0) 商品→セクター対応表 + overall の優先順位化**（PR #21、`common/prompts.py` のみ）:

- **規約(0)**: distilled ゴールド全 split の commodities→sectors 実績から抽出した
  対応表を LABEL_SYS に明示（原油・WTI・ブレント・ガソリン=crude_oil /
  天然ガス・LNG=natural_gas / 石炭=coal / 銅・アルミ・ニッケル・亜鉛・鉛・錫・
  リチウム=non_ferrous / 貴金属=precious_metals / 鉄鉱石・鉄鋼=steel /
  農畜産品=agriculture）+「商品名が出た質問は必ず対応セクターを入れる」
- **規約(1) 優先順位化**: overall は商品名・セクター言及が一切無い場合のみ
  （ループ4は overall 規約の強調3箇所に対し対抗規則ゼロだったのが根本原因）

ゴールドは既に正しかったため**再ラベル不要（教師コスト $0）**。prep-rl 再実行 +
再SFT のみ。基準日=2026-07-19 はデータの `meta.label_today` から自動導出（ループ4の
バグ修正が機能）。

## SFT実行（g6e.xlarge L40S・NeMo-RL v0.6.0・ハイパラはループ2〜4と同一）

- 27 step、final loss 4.13、val_loss 4.16（ループ4: 3.31/3.38。規約(0)追加で
  システムプロンプトが長くなり条件付けが変わったため損失の絶対値は非比較）

## 結果（heldout n=37）

| 指標 | base4 | sft4 | base5 | sft5 | 閾値 |
|---|---|---|---|---|---|
| schema_valid | 1.0 | 0.964 | 0.964 | **1.0** | 1.0 ✅ |
| sectors_match | 0.607 | 0.536 | 0.929 | **0.964** | - |
| query_type_match | 0.929 | 0.893 | 0.929 | **1.0** | - |
| date_range_match | 0.75 | 0.75 | 0.857 | 0.857 | - |
| analysis_match | 0.536 | 0.429 | **0.821** | **0.821** | 0.85 ❌(23/28、あと1件) |
| source_exists | 0.889 | 0.778 | 0.889 | 0.889 | 1.0 ❌ |
| citation_format | 0.889 | 0.778 | 0.889 | 0.889 | 0.95 ❌ |

## 所見

1. **overall 過剰般化はほぼ根絶**: sectors_match 0.536→0.964。sft5 の失敗5件に
   「商品名→overall 逃げ」は 0件。規約(0)は base の時点で効き（0.929）、SFT が
   さらに上積み。analysis_match は 0.429→0.821 とほぼ倍増、閾値 0.85 まで残り1件
2. **sft5 失敗5件の内訳**（課題の質が sectors から date_range に移った）:
   - **規約(3)の未定義ギャップ 2件**: 「先月」→ pred はローリング30日
     (6/19〜7/18) だがゴールドは暦月 (6/1〜6/30)。「今週」(範囲) → pred 7/12
     開始だがゴールドは月曜始まり 7/13。**訓練ゴールドは暦月・月曜始まりで一貫**
     しており（train/valid 全6件確認）、規約(3)に2定義を足すだけで救済可能・
     再ラベル不要
   - 曜日計算誤り 1件: 「今週火曜日」→ 7/15(水)。規約(3)に逆算手順を明記済みでも
     誤る（ループ4から残存。9Bモデルの暦計算能力の限界、プロンプトでは救済困難）
   - summary→date_range=null 疑似相関 1件: 「本日の〜」の 本日 を無視（ループ4
     所見3の残存。sft5 で2件中1件に減少）
   - edge 1件: 架空商品の質問に sectors=["misc"] が復活（規約(5)違反、SFT後も残存）
3. **SFT の効果は整形に集中**: base5→sft5 で schema 1.0（base5 はガベージ出力
   1件あり）・query_type 1.0 に矯正。一方 date_range は 2件改善・2件悪化で
   相殺（うち1件が上記の summary null、1件が先月ローリング化）
4. **generation**: grounded_qa は base5/sft5 とも 8/8 満点。source/citation 0.889 の
   失点は unanswerable 1件のみ — 「レポートに記載がありません」と**正しく拒否して
   出典なしで 0点**（ループ4所見4の教材・評価非対称そのもの）。評価側を蒸留ゲートと
   整合させれば source_exists / citation_format の2閾値は即クリアとなる

## 運用メモ

- `make serve` は Qwen/GGUF 用の代替ターゲット。base 測定は **`make serve-nemotron`**
  （今回誤って serve を起動 → GGUF 無しで即終了、実害なし）
- SSM 再接続のたびに `sudo su - ubuntu` を忘れない（ssm-user のままだと docker
  permission denied / CKPT_BUCKET 未設定）
- ループ5 ckpt はノード checkpoints/sft/{step_27,hf}、ループ4版は
  /opt/nvidia_slm/loop4-archive/

## 次周（ループ6）候補 — 1変更の原則

主目標は analysis_match 0.821 → 0.85（残り1件）+ generation 2閾値:

1. **規約(3)拡張: 「先月=前月1日〜前月末日」「今週=基準日を含む月曜始まりの週の
   月曜〜基準日」を追記**（推奨・教師コスト$0・再ラベル不要）。失敗5件中2件に直接
   効き、analysis 0.893 見込み。付随修正として **unanswerable の教材・評価整合**
   （蒸留ゲートと同じ「記載がありません+出典なし」許容を s6_eval/reward 側に実装。
   モデル・データに触れない評価側バグ修正のため 1変更の枠外扱い）を同梱すると
   4閾値中 schema/source/citation の3つが揃う
2. S5 GRPO: date_range の検証可能報酬で暦計算・疑似相関を直接罰する
   （曜日計算誤りと summary null はプロンプトでは救済困難になりつつある）
