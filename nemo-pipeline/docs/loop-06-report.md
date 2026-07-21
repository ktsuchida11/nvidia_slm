# 学習ループ 6周目レポート（2026-07-21）

## 1変更の内容（sft5 の規約未定義ギャップ2件への対処）

**規約(3)拡張**（PR #23、`common/prompts.py`）:

- 「先月=前月の1日〜末日（暦月。ローリング30日ではない）」を追記
- 「今週=基準日を含む月曜始まりの週の月曜日〜基準日」を追記
- 訓練ゴールドは暦月・月曜始まりで一貫（train/valid 全6件を確認）のため
  **再ラベル不要・教師コスト $0**

付随修正（モデル・データ非接触の評価側バグ = 1変更の枠外）:

- **unanswerable の教材・評価整合**: `reward.py` に `refusal_without_citation` を
  追加し、蒸留ゲート（s2）と評価（s6）で同一述語を共有。unanswerable への
  出典なし拒否を満点扱いに（ループ4所見4の非対称を解消）。grounded_qa は
  カテゴリ限定で拒否逃げは従来通り0点。**loop5 実測 details での事前再集計で
  base5/sft5 とも source/citation 0.889→1.0 になることを GPU 実行前に確認済み**

## SFT実行（g6e.xlarge L40S・NeMo-RL v0.6.0・ハイパラは従来と同一）

- 27 step、final loss 3.59、val_loss 3.70（プロンプト変更のため絶対値は非比較）

## 結果（heldout n=37）

| 指標 | base5 | sft5 | base6 | sft6 | 閾値 |
|---|---|---|---|---|---|
| schema_valid | 0.964 | 1.0 | **1.0** | **1.0** | 1.0 ✅ |
| sectors_match | 0.929 | 0.964 | 0.964 | 0.929 | - |
| query_type_match | 0.929 | 1.0 | 0.964 | 0.964 | - |
| date_range_match | 0.857 | 0.857 | 0.857 | 0.857 | - |
| analysis_match | 0.821 | 0.821 | **0.821** | 0.786 | 0.85 ❌ |
| source_exists | 0.889 | 0.889 | **1.0** | **1.0** | 1.0 ✅ |
| citation_format | 0.889 | 0.889 | **1.0** | **1.0** | 0.95 ✅ |

**4閾値中3つ（schema / source / citation）をクリア**。generation は base・SFT の
両方で満点（grounded_qa 8/8 + unanswerable 1/1）となり完成扱い。

## 所見

1. **評価整合は事前予測どおり完全成功**: source/citation 0.889→1.0。unanswerable への
   正しい拒否が正しく採点されるようになった。generation 側は今後のループでは
   リグレッション監視のみでよい
2. **規約(3)拡張は部分的に成功**: 「先月」は base の時点で完治（base6 失敗リストから
   消滅）。「直近1ヶ月」の境界1日ずれ（base6 で新出）は SFT が修正。
   一方**「今週」は base でも SFT でも 7/12 開始（先週規則の「7日前」との混同）の
   まま未矯正** — 訓練例2件では上書きできず
3. **SFT が analysis で逆効果に転じた（0.821→0.786）**: SFT が直したのは1件
   （直近1ヶ月）に対し、壊したのが2件 — (a) summary+本日→date_range=null の
   疑似相関が1件→2件に増殖（訓練の summary 系ゴールドの大半が null のため）、
   (b) comparison「金と原油は景気後退局面で〜」に sectors=["overall"] 逃げが再発。
   **SFT が訓練分布の疑似相関を増幅する構造はループ3・4・6で3回再現** — 訓練
   データの分布自体を直さない限り SFT は analysis を改善しない段階に入った
4. **プロンプト規約は飽和**: base analysis 0.821 が2周連続。sft6 失敗6件の内訳は
   (i6) comparison overall逃げ / (i12) edge misc復活 / (i16) 曜日計算 7/15≠7/14 /
   (i21,23) summary→null ×2 / (i26) 今週 7/12開始+qt劣化+```jsonフェンス復活。
   曜日計算はループ4から規約明記でも直らず、9B の暦計算能力の限界の可能性が高い

## 運用メモ

- GPUノード起動ルーチンは `remote/node-start.sh` に集約（PR #24。swap再作成+
  S3 2系統同期+mlflow再起動+同期検証。ループ7で実機初検証）。user-data化は
  terraform の user_data 変更=インスタンス再作成で EBS の HFキャッシュ・アーカイブが
  消えるため不採用
- ループ6 ckpt はノード checkpoints/sft/{step_27,hf}、ループ5版は
  /opt/nvidia_slm/loop5-archive/
- 今回のGPUコストは見積どおり ~$2-3

## 次周（ループ7）候補 — 1変更の原則

主目標は analysis_match 0.821（base6）→ 0.85。所見3のとおり SFT の逆効果の根本原因
=訓練分布に踏み込む段階:

1. **的絞りデータ増強（推奨・教師 ~$1）**: 蒸留に不足パターンの訓練例を追加 —
   (a) summary+日付表現（「本日の相場全体を〜」型。現状ほぼ null ゴールドのみで
   疑似相関の源）、(b) 「今週」範囲（現状2件のみ）、(c) 商品名ペアの comparison。
   sft6 失敗6件中4件（i6, i21, i23, i26）の訓練時対策に相当し、SFT の増幅方向を
   反転させる根治策。heldout は不可侵のまま train/valid のみ追加
2. S5 GRPO: date_range の検証可能報酬（reward.py 実装済み）で暦計算・疑似相関を
   直接罰する。曜日計算（i16）に効く可能性がある唯一の手段だが、コスト・
   不安定性リスクは SFT より高い
