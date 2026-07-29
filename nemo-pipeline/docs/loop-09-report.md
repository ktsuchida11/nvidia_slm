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

## コスト（計画時点）

実装のみでAPI/GPU支出なし。想定総額 ~$100-170（上記見積り）。
※実機実行の実績は末尾「コスト実績」を参照（超過）。

---

# 実機実行結果と検証ノウハウ（2026-07-27・round-1/round-2）

> このセクションが本ループの中核成果。「何を試し、何がダメで、なぜか、次どうするか」を
> 再利用可能な形で残す。数値は全て実機実測。

## 1. データ工程（完走・実測）

| 段 | 件数 | 備考 |
|---|---|---|
| fetch | 30,000 | ronantakizawa/Finance-Instruct-500k-Japanese（列名 user/assistant） |
| ヒューリスティック通過 | 23,177 | 棄却: q_len 3,205 / not_japanese 1,675 / dup 1,662 / a_len 281 |
| Haiku judge 採用 | 12,747（55%） | ~$8 |
| 数値答え候補 | 5,364（42%） | parse_valued_number（単位/倍率付きのみ） |
| Sonnet verify 検証済 | 2,800 | ~$50 |
| 分割（round-2再配分） | sft_train 9,758 / sft_valid 199 / grpo 2,000 / heldout 300 / fmt 490 | 4者リーク検査パス |

逐次ログ（judge_log/verify_log）のおかげでクレジット切れ・API上限でも**再課金ゼロで再開**できた。

## 2. base 測定（GRPOの前提確認）

- base9（round-1 heldout）: finqa_score **0.7823** / exact 0.6867 / number_found 1.0 / format 0.92
- base9b（round-2 heldout）: finqa_score **0.7885** / exact 0.71 / number_found 1.0 / format 0.9133
- → **base が既に「時々正解」帯域（exact 0.69-0.71）**。GRPOのグループ内分散が期待できる好条件。
  well-tuned な指示追従モデルは FINQA_SYS の「答え:」形式を最初から守れる。

## 3. round-1: SFT の破綻 → データ品質の教訓（最重要）

- sft9（632step・lr5e-5・2ep・単一プロンプト FINQA_CHAT_SYS）: finqa 0.7823→**0.163**、
  既存 analysis_match 0.836→**0.115** の全面崩壊。predは多言語トークンサラダ。
- sft9b（fmt490混合・enable_thinking:false明示・lr1e-5・1ep・checkpoint退避で新規学習）:
  finqa_score **0.29**・format **0.077**。predは**英語CoT("Okay, let's see...")が400トークンで
  切れ「答え:」に未到達**。

**根本原因**: Finance-Instruct-500k-Japanese の回答は**冗長な解説エッセイ**（概念を延々説明＋
「お役に立てれば幸いです！他に質問があればお知らせください。」等のチャット挨拶）で、
簡潔な検証可能回答ではない。これでSFTすると「冗長に説明する」挙動を学習し、簡潔な
numeric-extraction 評価（FINQA_SYS＋答え行＋400トークン）と本質的にミスマッチして悪化する。

### 教訓: SFT / GRPO の使い分けは「タスク × データ」で決まる

| | 使う場面 | 失敗条件 |
|---|---|---|
| SFT（追加学習） | base が**できない**振る舞いを、**その形式のデモ**で教える | データ形式が目標とズレると逆方向へ動かす（＝finqa今回） |
| GRPO（強化学習） | 出力を**プログラム採点**でき、base が**「時々正解」帯域** | 全問同値で報酬分散なし（＝ループ8）／KL無しでドリフト（＝grpo9） |

**本プロジェクト内の対比が実証**:
- **analysis（ループ1-7）**: base が構造化JSONラベルを苦手 → 形式一致デモでSFTが改善
- **finqa（今回）**: base が既に簡潔正答 → 形式不一致の冗長デモでSFTが悪化

→ 汎用QAデータの「品質」は日本語の自然さだけでなく**目標出力形式との一致**が本質。
冗長な解説データは検証可能タスクのSFTには不適。**baseが既に強いタスクはSFTを飛ばしてGRPO直行が筋**。

## 4. round-2: base→GRPO（SFT段を外す）

方針転換: base が強く報酬が検証可能なので、SFTを飛ばし base から直接GRPO
（grpo_finqa.yaml `model_name`→base、grpo 2,000件はSFTと排他の多様な検証可能プロンプト）。

- **帯域診断パス**: step Avg Reward 0.66-0.78（理想0.5-0.9）、baseline_reward の
  pct_0/pct_1/pct_mixed 共存＝グループ内分散あり＝**ループ8に欠けていた学習信号を確認**
- 完走（236step=2ep）: 断片化OOM も思考空振りも無し（PR#40の cpu_offload+expandable_segments、
  enable_thinking:false が有効）
- **grpo9 評価 = フラット（階段出ず）**: finqa_score **0.7601** / exact 0.6767 / format 0.90
  vs base9b 0.7885 / 0.71 / 0.9133。差は n=300 の SE（±0.02-0.026）内＝**統計的に有意な変化なし**

## 5. 深掘り分析（追加GPU課金なし）— GRPO診断の定石

grpo9 の「フラット」が **config弱い / base天井 / drift** のどれかを、既存ファイルだけで切り分けた。

1. **train reward 推移**（`grep "Avg Reward" grpo9.log`）: first30 **0.658** → last30 **0.675**
   （+0.017≈ノイズ）＝GRPOは train でも reward を押し上げていない
2. **item単位 fix/break 差分**（eval_*_details.jsonl を index結合して比較）:
   - base誤答を **21問 fix**（＝**学習可能シグナル実在＝base天井ではない**）
   - base正答を **31問 break**（drift）、score improved 28 < regressed 42
   - **break > fix ＝ KL無しドリフト劣化の signature**

**診断確定**: base天井ではない（21問直せる）。主因は**KL/参照ポリシー無効
（ループ8の64GB RAM制約の名残）によるドリフト** — アンカー不在で fixable を直す一方
base の正解から離れ、差し引きマイナス。lr 3e-7 も低すぎ（train reward 横ばい）。

## 6. round-2 修正（KL有効化再GRPO・本レポートと同PR）

- `skip_reference_policy_logprobs_calculation: false`（参照ポリシー≈18GB pinned CPU、
  384GBホスト g6e.12xlarge なら余裕）
- `loss_fn.reference_policy_kl_penalty: 0.0 → 0.02`（k3・base分布アンカー）
- `optimizer lr: 3e-7 → 1e-6`（更新過小の解消。KLと対で暴れ抑制）
- **コード変更不要**: `run_grpo_finance._maybe_disable_reference_model` は
  「skip=true かつ KL=0」の時のみ init_reference_model=False を注入するため、
  config を false+KL>0 にすれば参照ポリシーが自動で有効化される
- 期待値は控えめ（train reward 横ばい＝上積みの天井が近い可能性）。伸びなければ天井確定として総括。

## 7. 運用ノウハウ（ハマりどころ集・次ループへの申し送り）

- **チェックポイント自動レジューム罠**: `checkpoint_dir`（/ckpt/sft・/ckpt/grpo）に前回の
  `step_*` が残っていると、新規学習のつもりが**レジュームを試みて `gather_object` の
  code-object pickle エラー**（`TypeError: cannot pickle code objects`）で死ぬ。
  → 新規走行前に checkpoint_dir を退避（`mv checkpoints/sft checkpoints/sft-xxx`）。SFT/GRPO共通
- **convert の base抽出バグ（PR #47修正）**: `convert_sft_to_hf.sh` の `grep -m1 model_name` は
  GRPO設定の `policy.draft.model_name: null` を先に拾い base="null" → merge_lora が 401。
  SFTは policy.model_name のみで顕在化せず。null/空を除外して修正
- **API上限の回避（課金ゼロ再ビルド）**: verify need（grpo+heldout+fmt）を既存 verified 件数
  以内に収めると verify_numeric は新規API呼び出しをスキップ（既ログ再利用のみ）。配分変更で $0 再ビルド可
- **eval/serve エンドポイント**: `host.docker.internal` は 127.0.0.1 バインドに届かない。
  llm-gen のブリッジIP（GEN_IP=`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' llm-gen`）
  を `OPENAI_BASE_URL=http://$GEN_IP:8000/v1` に渡す。MLflowも同様に MLF_IP
- **tmux必須・切断耐性**: 学習は tmux 内で。`docker run -d`（serve/llm-gen）はデタッチなので
  SSM切断でも生存。学習は `2>&1 | tee /tmp/xxx.log` でログ保存し、切断後は grep で結果回収
- **per-epoch の step表示リセット**: `Step N/118` は epoch毎にリセット（118→1）。
  グローバルstep（checkpoint `step_236`）とは別物。切断復帰で「step番号が戻った＝再起動」と
  誤認しないこと（container の Up時間・グローバルstep・checkpoint で判断）
- **GRPO診断の3点セット**（②③はGPU課金なし）: ①帯域診断ゲート（step1-3 Avg Reward 0.5-0.9・分散あり）
  ②train reward 推移（first vs last）③item単位 fix/break 差分。②③で
  「config弱い / base天井 / drift」を切り分けられる

## 8. round-2 結果と loop9 最終結論

**grpo9kl（KL有効・KL罰0.02・lr 1e-6・236step）評価（heldout n=300）**:
- finqa_score **0.7898** / exact 0.7133 / number_found 0.9933 / format 0.9167、pass=true（base9b 0.7885 超え）
- item差分 vs base9b: **fixed 28 / broken 27**

### KLの効果は実証された

| run | score vs base9b | fixed | broken | net |
|---|---|---|---|---|
| grpo9（KL無し・lr3e-7） | 0.7601（−0.028） | 21 | 31 | **−10** |
| grpo9kl（KL0.02・lr1e-6） | 0.7898（+0.0013） | 28 | 27 | **+1** |

→ **KL が base正答の破壊を 31→27 に抑え、lr↑で修正を 21→28 に増やし、net −10→+1 に転換**。
診断（「KL無しドリフトが劣化の主因」）は正しかった。KLアンカーは検証可能GRPOで**必須**。

### ただし階段（明確な上積み）は出ず

- +0.0013 は n=300 の SE（±0.02）内 ＝ **統計的に base と同等**
- **fixed 28 ≈ broken 27** ＝ GRPO は正解を入れ替えるだけで系統的改善ではない ＝ **base天井の signature**
- train reward も横ばい（0.65-0.68）

### loop9 最終結論

1. **方法論は完全に機能**: GRPO は健全に回り（帯域診断パス・グループ内分散あり・非暗記・無崩壊）、
   ループ8の構造欠陥3つ（同一プロンプト暗記・全成分二値報酬・狭い帯域）を解消。さらに
   **KLアンカーがドリフトを制御することも実証**（net −10→+1）。
2. **指標ゲインは頭打ち**: base（Nemotron-9B-Japanese）が検証可能な簡潔数値QAで既に強く（exact 0.71）、
   残り29%は 9B の能力限界に近いハード問題。GRPOは「時々正解」を入れ替えるだけで系統的に押し上げられない。
3. **B-lightの前提の再評価**: 「汎用金融QA10k→SFT→GRPOで階段」は、(a)SFTデータが冗長解説で不適（round-1）、
   (b)base が既に強くGRPOの余地が小さい（round-2）、の二重で崩れた。

### 次ループへの示唆（最重要の申し送り）

- **GRPOで明確なゲインを狙うなら、base が苦手な（＝伸びしろのある）タスクを選ぶ**。
  base が既に強いタスクは GRPO の上積みが小さい（＝今回）。「時々正解」帯域にいても、
  fixed≈broken の入れ替えに留まるなら天井。fixed>broken に持っていける難易度設計が要る
- あるいは reward を、base が最適化しきれていない軸（簡潔さ・過程の正しさ・出典忠実性等）へ設計し直す
- SFT を入れるなら、蒸留元は「目標形式に一致する簡潔な検証可能回答」を新規生成する
  （既製の解説データは不適 ＝ round-1 の教訓）
- KL/参照ポリシーは検証可能GRPOで必須（384GBホストなら常時有効化してよい）

## コスト実績（2026-07-27時点）

累計 **~$200消費**（当初見積り $100-170 を超過）。内訳の主因: judge ~$8・verify ~$50、
GPU複数ラウンド（ループ8のOOMデバッグ空振り＋ループ9の SFT 2回＋GRPO）。
round-2 の KL再GRPO は追加 ~7-14h GPU 見込み。教師API課金は round-2 では発生しない
（SFT/GRPO/eval は全てローカル score_finqa で動くため）。
