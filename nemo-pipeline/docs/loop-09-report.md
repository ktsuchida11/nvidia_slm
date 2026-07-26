# 学習ループ 9 計画・実装レポート（2026-07-26・タスク転換 B-light: 金融QA + 検証可能GRPO）

## ループ8 GRPO失敗の構造分析（本ループの出発点）

GRPOの学習信号はグループ内報酬分散のみ（8生成の報酬が全て同値なら advantage=0 で勾配ゼロ）。
思考モードバグ（PR #40 で修正済み）とは独立に、修正しても学習が空振る構造欠陥が3つあった:

1. **GRPO訓練セット = SFT訓練セット（完全同一）**: grpo_analysis_train.jsonl の316件は
   sft_analysis_train.jsonl と同一プロンプト。SFTで3epoch模倣学習済みの問題の再出題であり、
   大半が「8/8正解（暗記済み）」帯域 → advantage 0 が構造的に保証されていた
2. **報酬が全成分二値 + schemaゲート**（common/reward.py）: sectors/query_type/date_range
   すべて完全一致の0/1で部分点なし。date_range は1日ズレでも0点 → 「惜しい」生成に信号が
   流れず、グループ内分散は完全な正解/不正解の反転時にしか生じない
3. **タスクの決定性**: analysis は「決まった正解の再生」でSFT向き。「時々正解」帯域は
   base8実測の弱カテゴリ（trend dr 0.571 / edge 0.6 / comparison 0.714）に限られ狭い

→ 教訓: データ量「だけ」の問題ではない。(a)未見プロンプト (b)連続部分点報酬
(c)帯域を狙った出題 の3点セットが必要。

## ループ9の決定（ユーザー選択: パターンB-light）

検討3パターン（A=タスク維持・帯域修正 ~$100 / B-light=金融QA1万件 ~$100-170 /
C=フル規模15万件 ~$2,500-4,000）から **B-light** を採用。
Finance-Instruct-500k-Japanese（HF: ronantakizawa/…、Apache 2.0商用可・日本語金融QA 51.8万件）
から1万件でSFT、プログラム検証可能な数値QA 2,500件でGRPOを回し、
**SFT→GRPOで順に精度が上がる階段** を中コストで実証する。

見積り: API+GPU ~$100-170・約1週間（実装2-3日が支配的）。
- データ: fetch無料 + Haiku品質judge ~$10 + Sonnet数値verify ~$50-80
- SFT 10,385件×2ep ≈ 650step ≈ 4.5h（1×L40S実測25s/step）→ $15-25
- GRPO 2,500件×2ep ≈ 314step ≈ 10-21h（~4分/step推定）→ $40-85

## 実装（PR 3本・全てローカル検証済み）

| PR | 内容 | 検証 |
|---|---|---|
| A: feat/loop9-finqa-data | common/finqa.py（日本語金融数値の抽出: 兆/億/百万/万・▲△・複合表記・% + **相対誤差の連続減衰スコア**）、s2_finqa.py（--filter 品質選別 / --build 教師verify+3分割+**リーク検査**）、fetch --keep-fields | 合成200件でdry配管完走・リーク0 |
| B: feat/loop9-finqa-reward | reward_finance_qa、finance_env振り分け（gtの finqa キー）、s6_eval task=finqa（finqa_score/exact/number_found/format）、eval_finqa.yaml | ユニットテスト13件PASS・finqa dry評価満点・既存eval-dry回帰なし(n=70満点) |
| C: feat/loop9-finqa-train | sft_finqa.yaml（finqa 1万+既存385のマルチタスク）、grpo_finqa.yaml（実機検証済み4GPU非colocated設定を継承）、run_sft.sh SFT_CFG対応、Makefileターゲット | make -n / YAML lint |

設計上の要点:
- **学習と評価の整合**: GRPO と eval-finqa は同一の FINQA_SYS・同一スコアラ(score_finqa)。
  ループ8の「訓練だけ思考モードON」非対称の再発防止
- **3セット排他**: SFT / GRPO / heldout_finqa は正規化質問文でリーク検査（違反=ビルド失敗）
- **既存タスク維持**: SFTに既存 analysis/generation 385件を混ぜ、make eval（n=70）非退行を併走確認
- **judge/verifyの逐次ログ**: 中断・再実行時に既判定を再利用（API課金保護）

## ノード実行手順（次回GPU起動時）

```bash
# 0) 前提: PR 3本マージ済み + Macで push-to-s3.sh --with-repo 済み。ノードでrepo/data両系統sync
# 1) データ取得（ノードは外部NW可。~30分）
make fetch REPO=ronantakizawa/Finance-Instruct-500k-Japanese LICENSE=apache-2.0 LIMIT=30000 FIELDS=user,assistant
#    ⚠ 実カラム名はHFカードで確認して FIELDS を合わせる（user,assistant想定・要実機確認）
# 2) 品質選別+構築（要ANTHROPIC_API_KEY。judge ~$10 + verify ~$50-80）
make finqa-dry                 # 配管検証(無料)
make finqa-filter              # → data/finqa/filtered.jsonl (目標14,000)
make finqa-build               # → sft/grpo/heldout + リーク検査 + finqa_stats.json
# 3) base9測定（loop7 SFT成果物を退避してから）
make serve-nemotron && OPENAI_BASE_URL=http://$GEN_IP:8000/v1 EVAL_CHAT_KWARGS='{"enable_thinking": false}' make eval-finqa TAG=base9
# 4) SFT（既存step_*退避後。~4.5h）→ 評価2本
MLFLOW_URI=http://$MLF_IP:5000 make sft SFT_CFG=/pipeline/s4_sft/sft_finqa.yaml
make convert-sft && make serve-sft
make eval-finqa TAG=sft9 BASELINE=base9     # 階段1段目
make eval TAG=sft9 BASELINE=base8           # 既存タスク非退行
# 5) GRPO — ★帯域診断ゲート(must): grpo_finqa.yaml の max_num_steps を 3 にして起動し、
#    step1 Avg Reward 0.5〜0.9 / frac_full_reward<0.7 / frac_no_reward<0.5 を確認。
#    外れたらGPU停止してデータ難易度を再調整（0.0x台=形式未習得か全滅、0.95超=暗記済み）
MLFLOW_URI=http://$MLF_IP:5000 make grpo GRPO_CFG=grpo_finqa.yaml
# 6) 合格後 max_num_steps: 320 に戻して本走（10-21h、save_period 20でspot中断レジューム可）
# 7) make convert-grpo && make serve-grpo
#    make eval-finqa TAG=grpo9 BASELINE=sft9  # 階段2段目（本ループの主検証）
#    make eval TAG=grpo9 BASELINE=base8       # 既存タスク非退行
```

合否 = finqa_score で base9 < sft9 < grpo9 の単調改善 + 既存 eval n=70 非退行
（analysis_match ≥ 0.8361）。

## リスクと未確認事項（実機で要確認）

- Finance-Instruct-500k-Japanese の実カラム名（fetch の FIELDS）とライセンス表記のカード最終確認
- 機械翻訳品質: judge通過率が低い場合 LIMIT を上げて再fetch（30k→50k）
- verify歩留まり: 数値答え候補が2,800件(GRPO+heldout)に届かない場合は
  EDINET-Bench数値からの教師生成を追加（s2_finqa.py拡張・未実装の予備カード）
- GRPO step時間 ~4分は推定（ループ8観測ベース）。max_new_tokens 400・seq 2048 は同一

## コスト（本セッション時点）

実装のみでAPI/GPU支出なし。想定総額 ~$100-170（上記見積り）。
