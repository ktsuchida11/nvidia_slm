# 学習ループ 2周目レポート（2026-07-19）

## 1変更の内容（ループ1で検出した教師データ欠陥への対処）

**生成タスクの質問をチャンク起点に変更**（PR #10）:

- `CHUNK_QUERY_SYS` 追加: 質問を市況系テンプレではなく、チャンク（EDINET開示資料）の
  実記載（事業内容・沿革・リスク・業績）から教師が生成
- 80% grounded_qa（実引用付き回答）+ 20% unanswerable（正しい「記載なし」応答の見本）
- 回答可能性ゲート: 質問種別と実回答の不整合を棄却
- 引用ラベルを `SakanaAI/EDINET-Bench#<idx>` で文書ごとに一意化
  （従来は全件同一ラベルで source_exists が形骸化していた）
- チャンク k=3×1200字 → k=2×700字（SFT seq2560 に全量が収まる）

付随変更: SFTハイパラ調整（PR #12）: lr 1e-4→5e-5 / seq 2048→2560 / epoch 2→3 /
val_period 50→10。ANSWER_SYS ペルソナのドメイン中立化（prompts.py 一元管理で
蒸留・SFT・eval に一貫反映）。

## 再蒸留の結果（$6・claude-sonnet-4-6 via LiteLLM）

- total 378 / train 304（ループ1比3.7倍）/ valid 37 / heldout 37、リーク0
- generation: grounded_qa 80件（**記載なし混入0件**。ループ1は17/17件が記載なし）
  + unanswerable 7件。rejects: source_exists 13件（引用ゲートが機能）
- SFT用: analysis 235 + generation 69 = 304件

## SFT実行（g6e.xlarge L40S・NeMo-RL v0.6.0）

- 27 step（9 step/epoch × 3）、~25s/step、OOMなし（seq2560はL40Sで問題なし）
- loss 終盤 ~4.1、val_loss **4.35**
- チェックポイント `step_27` → `make convert-sft`（121モジュールマージ）→ `make serve-sft`

## 結果（新heldout n=37。heldoutが再生成されたため base も再測定）

| 指標 | base2 | sft2 | Δ | 閾値 |
|---|---|---|---|---|
| schema_valid | 0.75 | 0.786 | +0.04 | 1.0 ❌ |
| sectors_match | 0.464 | 0.571 | +0.11 | - |
| query_type_match | 0.714 | 0.75 | +0.04 | - |
| date_range_match | 0.429 | 0.50 | +0.07 | - |
| analysis_match | 0.214 | **0.393** | **+0.18** | 0.85 ❌ |
| source_exists | 0.667 | **0.889** | **+0.22** | 1.0 ❌ |
| citation_format | 0.778 | **1.0** | **+0.22** | 0.95 ✅ |

## 所見

1. **全7指標が改善**。ループ1のような退行（generation崩壊）なし。教師データ修正の効果が
   そのまま学習に反映された
2. **citation_format 1.0 で初の閾値クリア**。引用形式は完全に習得
3. base の generation 指標が旧heldoutの「満点」から 0.67/0.78 に低下 =
   旧満点がハルシネーション由来だったことの裏付け（質問が回答可能になり実力値が出た）
4. 残る課題は **analysis_match 0.393**（閾値0.85）と schema_valid / source_exists の
   仕上げ

## 運用上の教訓

- ノードへのrepo同期に `--delete` は使わない（S3側に無い checkpoints/ をローカル削除
  しようとする。今回はroot所有のPermission deniedで偶然無事）
- 旧チェックポイントが `checkpoint_dir` に残っていると自動レジュームが走り、
  異なる構成のオプティマイザ読込でクラッシュ（`cannot pickle code objects`）。
  新規学習前に `step_*` を退避すること（今回: /opt/nvidia_slm/loop1-archive/）
- NVMe上のswapはstop/startで消える → 再作成必要（fallocate 32G → mkswap → swapon）
- LiteLLM経由の蒸留はDooDのネットワーク解決に注意: 「litellm」という名前は複数compose
  プロジェクトに存在しうる。`DOCKER_NET := nvidia_slm_default`（hostpath.mk）で
  自プロジェクトのゲートウェイに接続する

## 次周（ループ3）候補 — 1変更の原則

主目標は analysis_match 0.393 → 0.85。候補（いずれか1つ）:

1. **analysis 蒸留データの増量**（235→600件級）+ エポック調整。
   最も確実だが $10級の蒸留コスト
2. **S5 GRPO の投入**: analysis_match は検証可能報酬（schema/sectors/date_range一致）が
   既に実装済みで、SFT済みモデルからの強化に適合。GPU時間は増える
3. schema_valid の失敗事例分析 → プロンプト/データの的絞り修正（コスト最小・効果限定的）

判定材料として、まず results/eval_*.json の失敗ケース内訳（どの質問カテゴリで
analysis_match を落としているか）を確認してから選ぶこと。
