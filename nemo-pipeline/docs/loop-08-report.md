# 学習ループ 8 中間レポート（2026-07-22・GRPO実行は大型ノード待ちで中断）

## ループ8の設計（1変更 = S5 GRPO を analysis タスクへ拡張）

ループ7の結論「残failure 5件中3件が date_range 起因で、SFTの模倣学習では上書きできない」
を受け、date_range を最重み(0.5)とする検証可能報酬の GRPO を実装した（PR #29）。
併せてユーザー指摘2件（閾値0.85の根拠なし・少データ暗記）へ対応した（PR #30）。

## 完了した成果

### 1. 物差し直し（PR #30）

- **絶対閾値0.85を廃止**し、設計原典 docs/01-plan.md の相対基準
  「analysis_match ≥ ベースライン実測（非退行）」へ復帰。`make eval BASELINE=<tag>`。
  0.85は導出記録がなく、n=28では1件=3.6pt・95%CI[0.64,0.92]で判別不能だった
- **評価母数を28→61件（analysis）に倍増**: `make eval-augment` で評価専用
  heldout_ext.jsonl +33件（heldout本体は不可侵・全splitと重複排除・リントゲート）
- **base8 = 0.8361**（n=70、sft7モデルの新物差し実測。grpo8 の非退行ライン）。
  新評価セットは弱点を明瞭に露出: trend の dr 0.571 / edge 0.6 / comparison 0.714
  （※旧ノードEBS消失のため results/eval_base8.json は会話記録から復元。集計値は原本同一）

### 2. GRPO訓練データの蒸留増強（PR #30、教師~$1）

- dr困難パターン: weekday_range 14件（従来2件でGRPOが正解をサンプル不能）/ relative_days 7件
- 多様性底上げ: general_* 37件（既存カテゴリ比率。266プロンプト反復の暗記緩和）
- → train analysis 316件 / valid 39件。リーク0検査済み・dedup参照にheldout_extを追加

### 3. GRPO実装と v0.6.0 実機適合（PR #29, #31〜#37）

| PR | 修正 | 教訓 |
|---|---|---|
| #29 | reward_analysis（0.2schema+0.15sectors+0.15qt+**0.5dr**+penalty）・環境振り分け・grpo_analysis.yaml | 一致判定はs6_evalと同一関数系 |
| #31 | config約40キー補完 + **data.train.output_key: ground_truth** | v0.6.0のResponseDatasetはoutput_keyの中身をassistant枠に入れ、math_hf_data_processorがmetadata['ground_truth']へ渡す（★実機確認事項の解） |
| #32 | finance_envにglobal_post_process_and_metrics実装 | v0.6.0の抽象メソッド。未実装だとray Actor化でTypeError |
| #33 | ACTOR_ENVIRONMENT_REGISTRYへPY_EXECUTABLES.SYSTEM登録 | カスタムActorはPython実行環境の登録必須 |
| #34 | gpu_memory_utilization 0.4→0.6 | 0.4では9B重みでKVブロック確保不能。colocatedはsleepで学習中退避 |
| #35 | make grpoにRAY_memory_monitor_refresh_ms=0 | sftで既知の対策がgrpoに欠けていた |
| #36 | **参照ポリシー非保持の注入**（KL罰=0+skipフラグ時のみ）+ shm 32g→16g + Rayストア比率0.15 | 参照ポリシー=pinned CPU 18GB。Rayオブジェクトストアはshm内で空きRAMの3割(27GB実測)まで膨張 |
| #37 | merge_loraがconfig.jsonにtorch_dtype補完 | 欠落するとfp32実体化（serve-sftも実はfp32配信だった） |

## 中断の理由: 単一L40Sでの colocated 9B GRPO は v0.6.0 の仕様外（実証）

refit（policy→vLLM重み転送）時に以下が同一GPU(44.4GB)へ同居する必要がある:

- policy の **fp32マスター重み 33.3GB** — `nemo_rl/models/automodel/setup.py` に
  `torch_dtype=torch.float32  # Always load in float32 for master weights` と
  ハードコードされており、config では変更不可（bf16化は学習数値精度上も不適: lr 3e-7 の
  更新はbf16分解能未満で消失する）
- vLLM 側の受信用確保 + 残留 ~10.5GB + 最大テンソル変換の過渡 1.09GB

→ **~1〜7GB 恒常的に不足**。eager化・shm/バッチ縮小・参照ポリシー除去・torch_dtype修正を
すべて適用しても3回同一のOOM（33.31GBで固定）。NVIDIA公式の8B設定は8×80GB前提。
CPU側も同様に、vLLM sleep(level=1固定)のpinned 18GBと重み展開でRAM 32GB（g6e.xlarge）は
即死・64GB（g6e.2xlarge、本ループで移行）でもGPU壁の手前まで、が実測。

**未使用の対抗カード（案B・精度影響なしだが仕組み介入リスクあり）**: ①dtensor_cfg.cpu_offload:
true ②vLLM sleep level=1→2 の1行パッチ（bind-mountで注入可） ③転送ジェネレータの
デバイス1行パッチ。ユーザー判断で凍結し、正攻法（マルチGPU）を選択。

## 決定と次アクション

1. **g6e.12xlarge（L40S×4）で非colocated GRPO** — 分離すればfp32マスター問題ごと消える。
   スポットクォータ「All G and VT Spot Instance Requests」8→48 vCPU の引き上げ申請が必要（数日）
2. クォータ承認後（2026-07-23確認・適用値48）: tfvars変更→ノード構築→base8は
   results/eval_base8.json（復元済み）をS3経由でノードresults/へ配置→
   `make grpo GRPO_CFG=grpo_analysis.yaml`→`make eval TAG=grpo8 BASELINE=base8`。
   4GPU設定は調整済み: vLLM専用2GPU(TP=2)+学習2GPU(DP=2)の非colocated
   （DP=3は128バッチが割り切れないため2/2対称分割。grpo_analysis.yaml★コメント参照）。
   shmは64gへ拡大（Rayストア0.15×384GB≈55GBを/dev/shm内に収める）
3. 合否 = analysis_match ≥ 0.8361（非退行）+ schema/source 1.0・citation ≥0.95

## コスト

教師 ~$1（eval増強+訓練増強）/ GPU ~$10-12（2ノード・デバッグ含む2日）
