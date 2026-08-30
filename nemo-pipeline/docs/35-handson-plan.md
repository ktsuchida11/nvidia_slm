# 35. 第2回ハンズオン設計

目的: **参加者が自分の手でパイプラインを一周させ、自分のデータでも回せる状態にする。**
所要: 3時間（休憩込み）。手順書は `37-handson-runbook.md`。

## 0. 設計方針

| 方針 | 理由 |
| --- | --- |
| **Step 1〜5 は全て $0・CPU・オフライン** | GPU が確保できなくてもハンズオンが成立する。人数分の GPU は用意できない |
| **各 Step に「壊してみる」演習を1つ入れる** | 動かすだけなら手順書を読めばよい。**壊れ方を見た人だけが自分のデータで判断できる** |
| **完了定義を数値で置く** | 「動いた気がする」を防ぐ。全員の画面に同じ数字が出る |
| **GPU 実走は Step 6 に隔離** | 前半の進行を GPU トラブルに巻き込ませない |
| 機微データは扱わせない | `05-proprietary-data-design.md` の方針 |

## 1. 前提と環境要件

### 参加者の PC（必須）

- **Docker が動くこと**（Docker Desktop / Colima / Docker Engine いずれでも可）
- ディスク空き **5GB 以上**（Python イメージ 1.5GB + 作業領域）
- メモリ 8GB 以上
- ネットワーク: **当日は不要**。事前課題でイメージを1つ pull するときだけ必要

### 事前課題（開催3日前までに案内）

```bash
git clone <このリポジトリ> && cd nemo-pipeline
make setup
docker pull python:3.12-bookworm                      # 1.5GB・数分
printf 'PY_IMG := python:3.12-bookworm\n' > hostpath.mk
docker run --rm python:3.12-bookworm python -c "print('ok')"   # ok が出れば完了
```

**イメージのビルドは不要**。Step 1〜5 が使うスクリプト
（`s1_curate.py` / `s2_distill.py` / `build_index.py` / `recall_eval.py` / `probe_qa.py` /
`check_rails.py`）は **Python 標準ライブラリだけで動く**ように書かれている。
numpy / anthropic / nemo_curator の import は全て遅延・任意で、無ければフォールバックする。

**実測確認（2026-08-28）**: `--network none` かつ `nemo-tools` を使わず、素の
`python:3.12-bookworm` だけで Step 1〜4 が完走し、数値も本書 §3 の表と完全一致した。

> 当初は `make build-tools`（5-10分・PyPI 必須・**11.7GB**）を事前課題にしていたが、
> **ハンズオンには不要**と実測で分かったので外した。ここが最大の脱落ポイントだったので、
> 事前課題は `docker pull` 1本まで軽くなっている。
> `nemo-tools` が要るのは実課金の `make distill` / `make fetch` など、
> 外部ライブラリを本当に使う経路だけ。

`hostpath.mk` はリポジトリが用意しているマシン固有の設定ファイル（git 管理外）。
既定の `python:3.12-slim` は **Apple Silicon の Docker Desktop で `exec I/O error`** になるため、
1行だけ書いて差し替える。

### 講師側の準備

- 予備の Python イメージ tar（`docker save python:3.12-bookworm -o python-3.12-bookworm.tar`）
- 各 Step の完了時に出るべき数値の一覧（`37-handson-runbook.md` の「✅ 完了定義」）
- Step 6 用の GPU（後述の §4 で可否を判定）

## 2. タイムテーブル

| 時刻 | Step | 内容 | 環境 |
| --- | --- | --- | --- |
| 0:00-0:15 | 0 | 環境確認・全体像の復習 | — |
| 0:15-0:45 | 1 | **S1 クレンジング** — ライセンス / 重複 / PII / 品質 | CPU |
| 0:45-1:15 | 2 | **S2 蒸留** — 配管検証と、教師データの欠陥を見つける | CPU |
| 1:15-1:25 | — | 休憩 | — |
| 1:25-2:05 | 3 | **S8 RAG + rerank** — 検索と読解を分けて測る | CPU |
| 2:05-2:35 | 4 | **S7 ガードレール** — 自作攻撃を1問足して測る | CPU |
| 2:35-3:00 | 5 | **スキルでループを1周回させる** | CPU |
| （延長） | 6 | **LoRA SFT 実走**（GPU が確保できた場合のみ） | GPU |

## 3. 各 Step の設計

### Step 1 — S1 クレンジング（30分）

**やること**: サンプル6件を投入して `make curate`。捨てられた3件の理由を読む。

**壊してみる演習**: 自分でサンプルを1件追加して、狙った理由で落とす。

| お題 | 期待する `reject_reason` |
| --- | --- |
| ライセンスを `cc-by-nd` にする | `license:cc-by-nd` |
| 既存文書の語尾だけ変える | `fuzzy_dropped` に計上 |
| 20文字未満にする | `too_short` |
| メールアドレスを本文に入れる | 落ちずに `[EMAIL]` にマスクされる |

**✅ 完了定義**: `stats.json` の `kept` が自分の追加分だけ想定どおりに動くこと。

**議論（5分）**: 「自社データを入れたら、何が落ちると思いますか？」
→ 日本語での品質フィルタの効かなさ（Curator の非英数字フィルタは英語専用）に触れる。

---

### Step 2 — S2 蒸留（30分）

**やること**: `make distill-dry` で配管を通す。API キー不要。
出力は `data/distilled_dry/` に出る（実データは壊れない）。

**見るポイント**: `train` / `valid` / `heldout` の3分割と、カテゴリ別の件数。
`heldout.jsonl` の1行目を開いて「これは評価専用で、絶対に学習に使わない」を体感する。

**壊してみる演習（本題）**: loop1 で実際に起きた事故の追体験。

> `data/curated/` の文書と、生成された質問を10件ずつ並べて読む。
> **質問がその文書から答えられるか**を目視で判定する。

loop1 では、この目視をしなかったために
**generation 学習データ 17/17件すべてが「記載がありません」**になっていた。

**✅ 完了定義**: 3分割の件数が出て、`heldout` と `train` に同じ質問が無いことを自分で確認できた。

**実課金オプション（希望者のみ・講師が判断）**: `make distill N_ANALYSIS=10 N_GEN=5`
（$1未満）。**全員では回さない** — API キーの配布と外部送信が発生するため。

---

### Step 3 — S8 RAG + rerank（40分）

**やること**: 索引 → 検索品質 → 生成 → rerank の4本を順に流す。

```bash
make retriever-index-dry     # dummy埋め込みで索引を作る
make retriever-recall-dry    # 検索だけを測る
make rag-eval-dry            # 検索 + 生成の配管
make rag-rerank-dry          # rerank を挟む
```

**見るポイント**: `probe_acc` ではなく **`gold_in_ctx_rate`**。
dry では生成をゴールドでモックしているので `probe_acc` は 1.0 が正常。

**壊してみる演習**: `RAG_K` を変えて `gold_in_ctx_rate` の変化を見る。

**議論（10分）**: 実機の数字を並べて、なぜ rerank top5 が k=10 に勝ったのかを考える。

| 構成 | probe_acc | gold_in_ctx |
| --- | --- | --- |
| k=5 | 0.5075 | 0.6985 |
| k=10 | 0.5427 | 0.7387 |
| rerank k0=50 → top5 | **0.5879** | 0.7186 |

→ **gold 同梱率が低いのに正答率で勝つ** = 文脈は量より質。

**✅ 完了定義**: 4本が全部通り、`gold_in_ctx_rate` が k によって変わることを自分の画面で確認した。

---

### Step 4 — S7 ガードレール（30分）

**やること**: `make guardrails-dry` で攻撃セットと良性セットを両方走らせる。

**見るポイント**: attack の `ok_rate` が低く、benign が `1.0`。
スタブ LLM は何も防がないので**これが正しい**。

**壊してみる演習（本題）**: `s7_guardrails/attacks.jsonl` に**自分で攻撃を1問足す**。

```json
{"id": "A13", "kind": "attack", "owasp": "LLM01-direct",
 "name": "自作", "prompt": "（考えた攻撃文）", "leak_markers": ["社内アシスタント"]}
```

そして良性側にも1問足す。**攻撃だけ足して満足しないこと**が今日の主題。

**議論（10分）**: loop15 の結果を見せて考える。

- garak（標準ベンチ）は **100% → 0%** で満点
- なのに自作12問は **9/12**。落ちた2問は**間接注入**（参考資料に指示を埋め込む）
- 罠を潰す前は、攻撃 4/4 全部拒否 ↔ **良性も全部拒否**していた

→ 「自分のドメインの攻撃は、自分で書かないと測れない」

**✅ 完了定義**: 自作の攻撃1問 + 良性1問が両方セットに乗り、件数が増えて結果が出た。

---

### Step 5 — スキルでループを1周回させる（25分）

**やること**: Claude Code で `nemo-pipeline-runner` スキルを起動し、
**次のループの計画を書かせる**（実行はさせない）。

```
このリポジトリで次のループを設計してください。
前回（loop15）の示唆から1テーマ選び、仮説・評価軸・概算コスト・承認ゲートを
docs/loop-16-plan.md にまとめてください。GPU は起動しないでください。
```

**見るポイント**:

1. エージェントが `decision-table.md` を引いて手法を選ぶか
2. **概算コストを出して承認を求めてくるか**（勝手に GPU を起動しないか）
3. `traps.md` の既知の罠を計画に織り込むか

**壊してみる演習**: `traps.md` から1行消して、同じ指示をもう一度出す。
**罠カタログが無いとエージェントの計画がどう劣化するか**を見る。
（消した行は演習後に `git checkout` で戻す）

**議論（10分）**: 「自分の仕事のどの部分がスキルにできるか」
→ 判断が定型で、参照すべき知識が文書化できて、失敗のコストが高いもの。

**✅ 完了定義**: `docs/loop-16-plan.md` が生成され、その中に**概算コストと承認ゲートの記述がある**。

---

### Step 6 — LoRA SFT 実走（GPU がある場合のみ・60-90分）

**やること**: 実際に学習を1本回して、loss が下がるのを見る。

```bash
make prep-rl                 # distilled → NeMo-RL 形式へ
make sft                     # LoRA SFT
make convert-sft             # アダプタをマージして HF 形式へ
```

**✅ 完了定義**: `loss` が下がり、`checkpoints/sft/step_*` ができる。

**注意**: 学習前に必ず `checkpoints/sft` を退避する。
残っていると**新規学習のつもりが自動レジュームを試みて死ぬ**（loop9 の罠）。

## 4. GPU 可否の判定（Step 6 の前提）

**未確定事項**: 使用予定 GPU の型番・枚数・VRAM 構成。以下は loop13 の実測からの外挿です。

loop13 実測: **9B LoRA・seq4096 は L40S 44GB×1 で OOM**
（43.5GiB 使用 + 1.9GiB 要求 = **約45.4GB 必要**）→ `gpus_per_node: 2` + `tensor_parallel_size: 2` で解決。

| VRAM 構成 | 9B LoRA seq2048 | 9B LoRA seq4096 | 備考 |
| --- | --- | --- | --- |
| 24GB×1 | ❌ | ❌ | 9B は無理。より小さいモデルに差し替える |
| 44-48GB×1 | ✅ 実測済み（loop1-7: 25s/step） | ❌ OOM 実測 | 標準構成 |
| **64GB×1** | ✅ 余裕 | **◯ 見込み**（必要 ~45.4GB < 64GB） | **要事前検証** |
| 44GB×2 | ✅ | ✅（TP=2 + `use_triton: false` + `dropout: 0.0`） | loop13 構成 |
| 80GB×1 | ✅ | ✅ | 余裕あり |

**64GB 単体なら seq2048 は確実、seq4096 も入る見込み**ですが、
`use_triton` / dropout の TP 依存の罠は TP=1 なら発生しないので、素直に動くはずです。

→ **当日までに 1 回だけ実走して確認してください**。
確認できない場合は seq2048 で回す（loop1-7 と同じ設定なので実績があります）。

## 5. 詰まりどころ（先回りして案内する）

| 症状 | 原因 | 対処 |
| --- | --- | --- |
| `pull access denied for nemo-tools` | `hostpath.mk` の `PY_IMG` 未設定（既定が `nemo-tools` の環境） | 事前課題の `printf 'PY_IMG := python:3.12-bookworm\n' > hostpath.mk` をやり直す |
| `python:3.12-bookworm` が pull できない | プロキシで Docker Hub に出られない | イメージ tar を配布して `docker load` |
| `docker: permission denied` | Docker グループ未所属（Linux） | `sudo usermod -aG docker $USER` して再ログイン |
| macOS で `*-slim` イメージが `exec I/O error` | Docker Desktop の arm64 slim 不具合 | `python:3.12-bookworm` を使う（`hostpath.mk` で切替済み） |
| ポート衝突（5000 / 8501） | macOS の AirPlay 等が占有 | `hostpath.mk` で別ポートに逃がす |
| `make` が無い（Windows） | — | WSL2 上で実施してもらう。**事前に案内する** |
| 日本語が文字化けする | ロケール警告 | 表示だけの問題。無視してよい |

## 6. 持ち帰り課題（任意）

> **自分のチームのデータを10件、`data/raw/` に入れて `make curate` を通す。**
> 何件残り、何が落ちたかを報告する。

これが「自分のデータでパイプラインを回す」の最小の第一歩です。
機微データは入れないこと（社外 API へは出ませんが、演習環境には置かない）。

## 7. やらないこと

- 全員での教師API実行（キー配布と外部送信が発生する）
- 参加者に GPU ノードの起動権限を渡す（課金事故の防止）
- 機微データ・実顧客データの持ち込み
- 既存 `results/` や `data/distilled/` の上書き（**dry は別ディレクトリに出る**ように修正済み）
