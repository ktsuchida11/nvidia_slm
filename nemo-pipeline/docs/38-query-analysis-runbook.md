# 38. クエリ解析モデルを nemo-pipeline で作る Runbook

対象アプリ `local-rag-llm` の **Phase 1（クエリ解析のローカル化）** を、Unsloth ではなく
本パイプラインで実施するための手順書。

---

## 0. なぜこの文書があるか

設計書 `local-rag-llm-implementation-design.md` §6.3 は学習ツールを
**「Unsloth（単機最速・GRPO・Colab配布）。NeMo-RL はデッキ上の『DC規模版』紹介に留める」**
としている。これは 2026-07 時点の判断で、その後 nemo-pipeline が15周回った結果と食い違う。

**食い違いの中身**: nemo-pipeline の `analysis` タスクは、設計書のクエリ解析タスクと**同じもの**である。

| | 設計書 §6.1 のクエリ解析JSON | nemo-pipeline `data/distilled/train.jsonl` |
| --- | --- | --- |
| 出力スキーマ | sectors / date_range / content_types / commodities / query_type / semantic_query / needs_overall_context / response_mode | **同じ8フィールド** |
| 評価指標 | `qa_sectors_match` / `qa_query_type_match` / `qa_date_range_match` / `query_analysis_match` / スキーマ妥当 | `sectors_match` / `query_type_match` / `date_range_match` / `analysis_match` / `schema_valid`（`s6_evaluation/eval.yaml` に実装済み） |
| 学習データ | 「合成問い合わせ → Sonnet でラベル付け・数千件」（未着手） | **train 316件 / valid 39件 / heldout 28件が既にある**（teacher = claude-sonnet） |

つまり **Phase 1 の学習パイプラインは新規に作る必要がなく、既にある**。
加えて nemo-pipeline 側には Unsloth 経路では効かない資産がある:

- 15周ぶんの罠カタログ（`claude-skills/nemo-pipeline-runner/references/traps.md`）
- 課金ゲート運用（概算 → 小規模実測 → 再見積り → 承認）
- リーク検査（loop10 で実リーク223文書を検出した実績）
- 評価3層分離と SE による判定

**判断**: Unsloth 経路は採らず、本パイプラインで作る。設計書 §6.3 は更新が必要。

---

## 1. 最初に読むべき結論 — たぶん学習は要らない

**`analysis_match` は base（未学習の Nemotron-Nano-9B-v2-Japanese）で既に 0.836 出ている**
（loop9 の baseline 実測）。設計書 §5.3 の Phase 1 合格ラインは `query_analysis_match ≥ 0.85`。

> **差は 0.014。n=70 では1件が約1.4pt なので、この差は測定誤差と区別がつかない。**

さらに loop9 round-1 では、**別目的の SFT の巻き添えで analysis が 0.836 → 0.115 に崩壊**している。
このタスクは「base が既に強く、下手に学習すると壊れる」典型で、
`references/decision-table.md` が「SFT を当てるな」と言う条件に合致する。

したがって **Step 0（base 測定）で要件を満たすなら、Phase 1 は配信設定だけで完了する**。
学習に進むのは Step 0 の結果が足りなかったときだけ。**この順序を飛ばさないこと。**

---

## 2. 着手前に決める3つ

| # | 論点 | 選択肢 | 推奨 |
| --- | --- | --- | --- |
| 1 | base モデル | ① Nemotron-Nano-9B（実績あり・重い） ② 4B級（設計の意図・未検証） | **まず ①で測る**。既存資産がそのまま効く。レイテンシが要件に合わなければ②を別途検討 |
| 2 | 合格ライン | ① 設計書 §5.3（analysis_match ≥ 0.85 等の絶対値） ② nemo-pipeline の `eval.yaml`（`analysis_match` は**相対＝base非退行**、0.85 は参考値に降格） | **先に決めて文書に固定する**。ループ8で「n=28 では1件=3.6ptで判別不能」として絶対閾値を降格した経緯がある |
| 3 | 学習データ | ① 既存 `data/distilled` の analysis 316件 ② 新規蒸留で増やす | Step 0 が不合格だったときに判断する。**先に増やさない** |

> 論点2を曖昧にしたまま進めると「合格したのかしていないのか」が言えなくなる。
> **合格ラインは測る前に紙に書く。**

---

## 3. 手順

### Step 0 — base を測る（必須・ここで終わる可能性が高い）

GPU ノードを起動してモデルを配信し、**学習せずに**評価する。

```bash
# ノード側
make serve-nemotron                  # vLLM で base を配信（要 HF_TOKEN）
make eval TAG=base_analysis          # exit 0=合格 / 2=閾値未達（未達は想定内）
```

**思考モードを必ず切る**（loop8・loop15 で2回踏んでいる）:

```bash
export EVAL_CHAT_KWARGS='{"enable_thinking": false}'
```

**見る数字**: `analysis_match` / `sectors_match` / `query_type_match` / `date_range_match` / `schema_valid`

**判定**:

- **要件を満たす** → **ここで Phase 1 は終わり**。Step 6（配信と LiteLLM 登録）へ飛ぶ。学習しない
- 満たさない → どの指標が落ちているかを見て Step 1 へ。`schema_valid` が落ちているなら形式の問題で
  SFT が効く。`sectors_match` が落ちているなら辞書・名寄せの問題で、学習ではなく前処理の問題かもしれない

### Step 1 — データ確認とリーク検査（$0）

```bash
python3 - <<'PY'
import json, collections
c = collections.Counter()
for split in ("train", "valid", "heldout"):
    for line in open(f"data/distilled/{split}.jsonl"):
        r = json.loads(line)
        c[(split, r.get("meta", {}).get("task"))] += 1
print(c)
PY
```

期待値（2026-08 時点）: train analysis 316 / valid 39 / **heldout 28**。

**リーク検査は必須**（`references/evaluation.md`）。heldout の入力が train / valid に無いことを確認する。
loop10 では検査が223文書の実リークを検出した。**検査を飛ばした評価は無効**。

### Step 2 — 学習データを足すか決める（課金あり・承認ゲート）

Step 0 で足りなかった指標に対して、その指標を直すデータだけを足す。全部増やさない。

```bash
make distill N_ANALYSIS=<件数>       # 教師API課金。概算を提示して承認を得てから
```

> **やらないこと**: heldout / probe を学習に混ぜる。閾値を下げて「合格」にする。

### Step 3 — SFT（課金あり・承認ゲート）

```bash
make prep-rl                          # distilled → NeMo-RL 形式
make sft                              # LoRA SFT
make convert-sft                      # アダプタをマージして HF 形式へ
```

**学習前に `checkpoints/sft` を退避する**。残っていると新規学習のつもりが自動レジュームを試みて
`TypeError: cannot pickle code objects` で死ぬ（loop9 の罠）。

seq2048 なら L40S 1枚で回る（loop1-7 実績・25s/step）。seq4096 は 44GB×1 で OOM するので
2枚 + `tensor_parallel_size: 2` + `use_triton: false` + `dropout: 0.0`（loop13 実測）。

### Step 4 — 評価（必ず base と同一条件で）

```bash
make serve-sft
make eval TAG=sft_analysis BASELINE=base_analysis
```

**base は同じ run 条件で測り直したものを使う**。評価セットが世代交代しているときに
古い baseline と比べると母数不一致で拒否される（loop13 の罠）。

差の解釈は SE と比べる。n=70 で1件は約1.4pt。**SE 内の差は「改善」ではなく「同等」**。

### Step 5 — GRPO（任意・Step 4 で頭打ちなら）

報酬は設計書 §6.3 の式（既存スコア関数の流用）をそのまま使える。

**必須の条件2つ**（loop8・loop9 の失敗から）:

- **GRPO の訓練セットを SFT と同一にしない**。同一だとグループ内 advantage が 0 になり学習信号が消える
- **KL アンカーを入れる**。KL 無しは net −10（fixed 21 / broken 31）、KL 0.02 + lr 1e-6 で net +1

なお loop9 round-2 の +0.0013 は n=300 の SE（±0.02）の内側で、**GRPO はここで天井**という結論だった。
過度な期待をしない。

### Step 6 — 配信と LiteLLM 登録

```bash
make serve-sft                        # または base をそのまま配信
```

LiteLLM の `model_list` に追加する（`local-rag-llm/templates/litellm_config.yaml`）:

```yaml
  - model_name: analysis-local
    litellm_params:
      model: openai/analysis-local        # vLLM の --served-model-name と一致させる
      api_base: os.environ/ANALYSIS_BASE
      api_key: "sk-local"
litellm_settings:
  fallbacks:
    - analysis-local: ["claude-haiku"]    # 接続断・5xx の自動フォールバック
```

**`--served-model-name` と `model_name` を一致させる**。loop15 でここがずれて 422 を踏んだ。

配信は **vLLM 固定**。Nemotron は Mamba+Attention ハイブリッドで GGUF 非対応のため
Ollama / llama.cpp には載らない（詳細は設計書「vLLM と Ollama のどちらを使うか」）。

---

## 4. コストと承認ゲート

| Step | 課金 | 概算 | 承認 |
| --- | --- | --- | --- |
| 0 base測定 | GPU（配信のみ・数時間） | ~$5-10 | **要** |
| 1 データ確認 | なし | $0 | 不要 |
| 2 追加蒸留 | 教師API | 件数×単価。**実測1件で単価を出してから全量** | **要** |
| 3 SFT | GPU | seq2048/LoRA なら数時間・~$10-20 | **要** |
| 4 評価 | GPU（配信） | ~$5 | **要** |
| 5 GRPO | GPU | ~$30-50 | **要** |

**ノードはステップ間で停止する。** 走らせっぱなしが一番高くつく。

---

## 5. このタスク特有の罠

| 症状 | 原因 | 対処 |
| --- | --- | --- |
| **SFT したら analysis が壊れた** | base が既に強いタスクに SFT を当てた。loop9 round-1 で 0.836 → **0.115** | **Step 0 を飛ばさない**。base が要件を満たすなら学習しない |
| 合格したのか分からない | 合格ラインを測る前に決めていない | §2 の論点2 を先に固定する |
| base と比較できない | 評価セットが世代交代している（loop9: n=300 → loop13: n=70） | base を同一 run 条件で測り直す。baseline タグは評価セット世代とペアで管理 |
| 出力が途中で切れる / 空になる | 思考モードが出力を食い潰す | `EVAL_CHAT_KWARGS='{"enable_thinking": false}'`。**学習と評価で設定を揃える** |
| 新規学習のつもりが死ぬ | `checkpoints/sft` に前回の `step_*` が残り自動レジューム | 走行前に退避（mv） |
| GRPO が何も学習しない | 訓練セットが SFT と同一 → advantage=0 | 別データにする |

---

## 6. 完了定義

- [ ] base の `analysis_match` ほか5指標を測り、**合格ラインと突き合わせた結果を文書に残した**
- [ ] リーク検査を通した（heldout ∩ train = 0、heldout ∩ valid = 0）
- [ ] （学習した場合）base と同一条件で比較し、差を SE と比べて判定した
- [ ] LiteLLM に登録し、`--served-model-name` の一致を確認した
- [ ] フォールバック（接続断時に haiku へ落ちる）を実際に切断して確認した

---

## 7. 参照

- 設計書: `local-rag-llm/local-rag-llm-implementation-design.md`（§5 評価ハーネス / §6 Phase 1）
- 手法選択: `claude-skills/nemo-pipeline-runner/references/decision-table.md`
- 罠カタログ: 同 `references/traps.md`
- 課金ゲート: 同 `references/billing-gates.md`
- 評価設計: 同 `references/evaluation.md`
- 実測の出典: `docs/loop-07-report.md`（analysis 0.821）, `docs/loop-09-report.md`（base 0.836 / SFT崩壊 0.115 / GRPO KL）, `docs/loop-13-report.md`（TP=2・評価セット世代）
