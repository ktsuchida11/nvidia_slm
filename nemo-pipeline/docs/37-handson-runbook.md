# 37. ハンズオン Runbook（参加者用・そのままコピペで進む）

第2回ハンズオンの手順書。設計意図は `35-handson-plan.md`。

**必要なもの: Docker が動く PC だけ。** GPU も API キーもネットワークも（Step 0 以外は）不要です。
各 Step の末尾に **✅ 完了定義** があります。そこに書かれた数字が画面に出れば次へ進んでください。

> **表記**: `$` で始まる行がコピペするコマンドです。`#` はコメントなので一緒に貼って構いません。
> 全コマンドは `nemo-pipeline/` ディレクトリで実行します。

---

## Step 0. 事前準備（開催前にやっておく・ネットワークが要ります）

```bash
$ git clone <このリポジトリのURL>
$ cd nemo-pipeline
$ make setup
$ docker pull python:3.12-bookworm                      # 1.5GB・数分
$ printf 'PY_IMG := python:3.12-bookworm\n' > hostpath.mk
```

**イメージのビルドは要りません。** Step 1〜5 で動かすスクリプトは Python 標準ライブラリだけで
書かれているので、素の Python イメージがあれば足ります
（`--network none` で遮断した状態で全 Step が通ることを実測確認済み）。

`hostpath.mk` はこのリポジトリが用意している**マシン固有の設定ファイル**（git 管理外）です。
既定の `python:3.12-slim` は **Apple Silicon の Docker Desktop で `exec I/O error` になる**ため、
1行だけ書いて `bookworm` に差し替えています。

**✅ 完了定義**

```bash
$ docker run --rm python:3.12-bookworm python -c "print('ok')"
ok
```

`ok` が出れば準備完了です。

> **うまくいかないとき**
> - `permission denied`（Linux）: `sudo usermod -aG docker $USER` して再ログイン
> - Windows: WSL2 の中で実行してください（`make` が要ります）
> - 会社のプロキシで Docker Hub に出られない: 講師からイメージ tar をもらって
>   `docker load -i python-3.12-bookworm.tar`

> **補足（今日は使いません）**: `make build-tools` で作る `nemo-tools:latest` は、
> 実際に教師 API を叩く `make distill` や `make fetch` など、**外部ライブラリを本当に使う経路**の
> ためのイメージです（11.7GB・ビルド 5-10分）。今日のハンズオンでは出番がありません。

---

## Step 1. S1 クレンジング — 何が捨てられるかを見る（30分）

### 1-1. サンプルデータを作る

```bash
$ mkdir -p data/raw && python3 - << 'PY'
import json, pathlib
docs=[
 {"text":"昨日の原油価格は上昇。WTIは前日比+1.8ドルの67ドル。中東情勢の緊迫が材料。OPECの減産遵守が需給を引き締め。ヘッジは分割の値決めを検討すべき局面。","meta":{"license":"own","title":"3/2 原油"}},
 {"text":"昨日の原油価格は上昇。WTIは前日比+1.8ドルの67ドル。中東情勢の緊迫が材料。OPECの減産遵守が需給を引き締め。ヘッジは分割の値決めを検討すべき局面。","meta":{"license":"own","title":"dup-exact"}},
 {"text":"昨日の原油価格は上昇した。WTIは前日比+1.8ドルの67ドル。中東情勢の緊迫が材料。OPECの減産遵守が需給を引き締めている。ヘッジは分割での値決めを検討すべき局面だ。","meta":{"license":"own","title":"dup-fuzzy"}},
 {"text":"天然ガスTTFは需要減で軟調。連絡先は test@example.com、03-1234-5678。","meta":{"license":"own"}},
 {"text":"asdf!!!! ????","meta":{"license":"own"}},
 {"text":"商用不可サンプル。金銀相場の説明。","meta":{"license":"cc-by-nc"}},
]
pathlib.Path("data/raw/sample.jsonl").write_text(
    "\n".join(json.dumps(d,ensure_ascii=False) for d in docs), encoding="utf-8")
print("6件書きました")
PY
```

### 1-2. 通す

```bash
$ make curate
$ cat data/curated/stats.json
```

**出るはずの数字**:

```json
{"kept": 2, "rejected": 3, "fuzzy_dropped": 1,
 "pii_masked": {"EMAIL": 1, "PHONE_JP": 1}, "by_domain": {"finance": 1, "general": 1}}
```

6件入れて **2件しか残りません**。

### 1-3. なぜ落ちたかを読む

```bash
$ python3 -c "
import json
for l in open('data/curated/rejected.jsonl'):
    d=json.loads(l); print(d['reject_reason'], '|', d['text'][:30])
"
```

```
exact_dup        | 昨日の原油価格は上昇。WTIは前日比+1.8ドルの67ドル。
too_short        | asdf!!!! ????
license:cc-by-nc | 商用不可サンプル。金銀相場の説明。
```

さらに、**近似重複**（語尾だけ違う3件目）が `fuzzy_dropped: 1` として落ちています。
残った2件では PII がマスクされています:

```bash
$ python3 -c "
import json
for l in open('data/curated/curated.jsonl'): print(json.loads(l)['text'][:70])
"
```

```
昨日の原油価格は上昇。WTIは前日比+1.8ドルの67ドル。中東情勢の緊迫が材料。...
天然ガスTTFは需要減で軟調。連絡先は [EMAIL]、[PHONE_JP]。
```

### 1-4. 演習: 自分で1件足して、狙った理由で落とす

`data/raw/sample.jsonl` に1行足して `make curate` をやり直します。

| お題 | 期待する結果 |
| --- | --- |
| `"license":"cc-by-nd"` にする | `reject_reason` が `license:cc-by-nd` |
| 本文を20文字未満にする | `too_short` |
| 既存の文の語尾だけ変える | `fuzzy_dropped` が増える |
| 本文にメールアドレスを入れる | **落ちずに** `[EMAIL]` にマスクされる |

**✅ 完了定義**: 自分が足した1件が、狙ったとおりの `reject_reason` で落ちた（またはマスクされた）。

> **考えどころ**: 自社のデータを入れたら、何件残ると思いますか？
> 日本語では品質フィルタが効かないことがあります
> （NeMo Curator の非英数字フィルタは英語専用で、日本語は74%が非英数字なので全滅します）。

---

## Step 2. S2 蒸留 — 学習データの作り方と、その落とし穴（30分）

### 2-1. 配管を通す（API キー不要）

```bash
$ make distill-dry
```

```
[distill] INFO done: {'total': 400, 'train': 322, 'valid': 39, 'heldout': 39, 'rejects': {}, ...}
[distill] INFO ★ heldout.jsonl は学習・GRPO報酬に使わないこと(評価専用)
```

教師 LLM は**一度も呼んでいません**。テンプレートでダミーを作って、
分割・検証・統計の**配線だけ**を確かめています。

> 出力は `data/distilled_dry/` に出ます。実データ（`data/distilled/`）は壊しません。

### 2-2. 3分割を見る

```bash
$ wc -l data/distilled_dry/*.jsonl
$ head -1 data/distilled_dry/heldout.jsonl | python3 -m json.tool | head -20
```

`train` / `valid` / `heldout` の3つに分かれています。

**`heldout` は評価専用で、学習にも報酬にも絶対に使いません。**
ここが汚れると、以降の全ての数字が意味を失います。

### 2-3. 演習（本題）: 教師データの欠陥を探す

このプロジェクトの1周目で、実際にこういう事故がありました。

> **generation の学習データ 17/17件すべてが「レポートに記載がありません」だった。**
> 文書（企業の沿革）と質問（市況の見通し）が噛み合っておらず、
> 教師 LLM は**正しく**「記載なし」と答えていた。

これは評価するまで気づきませんでした。**蒸留の直後に目視すれば5分で分かった**ことです。

やってみましょう:

```bash
$ python3 -c "
import json
rows=[json.loads(l) for l in open('data/distilled_dry/train.jsonl')][:10]
for r in rows:
    print('Q:', r['input'][:60])
    print('   task =', r.get('meta',{}).get('task'), '/', r.get('meta',{}).get('category'))
    print()
"
```

**問い**: この質問は、渡した文書から本当に答えられますか？

**✅ 完了定義**: 3分割の件数が出て、`heldout` と `train` に同じ質問が無いことを自分で確認できた。

> **実課金オプション（講師の指示があった場合のみ）**
> `make distill N_ANALYSIS=10 N_GEN=5` で本物の教師 LLM を使います（$1未満）。
> **外部 API にデータが送られます。**機微データが入っていないことを必ず確認してください。

---

## Step 3. S8 RAG + rerank — 検索と読解を分けて測る（40分）

「知識が足りない」を学習で解決しようとすると高くつきます。
このプロジェクトでは、**RAG が DAPT に圧勝**しました（$300-550 の見積り vs 実測 $12）。

### 3-1. 索引を作る

```bash
$ make retriever-index-dry
```

```
[index] INFO chunks.jsonl 再利用: 4153 chunks
[index] INFO 埋め込み済み 4153/4153
```

50文書を 4,153 チャンクに割って、ダミーのベクトルで索引を作りました。

### 3-2. 検索だけを測る

```bash
$ make retriever-recall-dry
```

```json
{"tag": "recall_dry", "n": 199, "backend": "dummy", "search_k": 100,
 "recall@1": 0.2462, "recall@5": 0.5327, "recall@10": 0.6633, "mrr": 0.3769}
```

**まだ生成していません。**「正解の文書を引っぱってこられたか」だけを測っています。
（ダミー埋め込みなので数字は低いです。実機の NIM では recall@5 = 0.6985 でした）

### 3-3. 検索 + 生成の配管

```bash
$ make rag-eval-dry
```

```json
{"tag": "rag_dry", "n": 199, "probe_acc": 1.0, "rag_k": 5,
 "gold_in_ctx_rate": 0.4673, "dry_run": true}
```

**`probe_acc` が 1.0 なのは正常です** — 生成をゴールドでモックしているので、
配管が正しければ必ず満点になります。**見るべきは `gold_in_ctx_rate`**。

### 3-4. rerank を挟む

```bash
$ make rag-rerank-dry
```

```json
{"tag": "rag_rerank_dry", "n": 199, "probe_acc": 1.0, "rag_k": 5,
 "gold_in_ctx_rate": 0.6181, "rerank_k0": 20, "gold_in_k0_rate": 0.7035, "dry_run": true}
```

`gold_in_ctx_rate` が **0.4673 → 0.6181** に上がりました。
広く取って（k0=20）から絞り直す（top5）と、同じ5件でも中身が良くなります。

`gold_in_k0_rate 0.7035` は「そもそも候補20件に正解が入っていた率」。
**この差が rerank の伸びしろで、この上限が埋め込み検索の限界**です。

### 3-5. 演習: k を変える

```bash
$ make rag-eval-dry RAG_K=10          # 文脈をただ増やす
$ make rag-rerank-dry RERANK_K0=50    # 候補を広く取ってから絞り直す
```

出るはずの数字（既定と並べたもの）:

| コマンド | `gold_in_ctx_rate` | `gold_in_k0_rate` |
| --- | --- | --- |
| `make rag-eval-dry`（k=5） | 0.4673 | — |
| `make rag-eval-dry RAG_K=10` | 0.6131 | — |
| `make rag-rerank-dry`（k0=20） | 0.6181 | 0.7035 |
| `make rag-rerank-dry RERANK_K0=50` | **0.6935** | 0.8342 |

**k0 を広げると `gold_in_k0_rate` が上がり、それがそのまま `gold_in_ctx_rate` に通ります。**
逆に言えば、`gold_in_k0_rate` に入っていない分は **rerank では原理的に救えません**
（実機ではこれが 16.6% あり、それが次に手を入れるべき場所だと分かりました）。

**✅ 完了定義**: 4本が全部通り、`gold_in_ctx_rate` が構成によって変わることを自分の画面で確認した。

> **考えどころ**: 実機ではこうなりました。
>
> | 構成 | probe_acc | gold_in_ctx |
> | --- | --- | --- |
> | k=5 | 0.5075 | 0.6985 |
> | k=10 | 0.5427 | 0.7387 |
> | rerank k0=50 → top5 | **0.5879** | 0.7186 |
>
> **rerank top5 のほうが gold 同梱率は低いのに、正答率で勝っています。**
> なぜだと思いますか？

---

## Step 4. S7 ガードレール — 攻撃だけ見ると騙される（30分）

### 4-1. 攻撃セットと良性セットを両方走らせる

```bash
$ make guardrails-dry
```

最後に2つの結果が出ます:

```
attack : ok_rate が低い          ← スタブLLMは何も防がないので、攻撃が素通りする
benign : {"ok": 20, "leaked": 0, "ok_rate": 1.0}   ← 良性は全部通る（誤爆ゼロ）
```

**これが正しい結果です。** スタブは防御しないので、攻撃は通って当然。
大事なのは、**攻撃と良性を必ず同時に測っている**という形のほうです。

### 4-2. 攻撃セットの中身を見る

```bash
$ python3 -c "
import json
for l in open('s7_guardrails/attacks.jsonl'):
    d=json.loads(l)
    if d['kind']=='attack': print(d['id'], d['owasp'], '|', d['name'])
"
```

OWASP LLM Top 10 のカテゴリごとに12問あります:
直接注入4 / **間接注入2** / PII 2 / プロンプト漏洩2 / ドメイン固有2。

### 4-3. 演習（本題）: 自分で攻撃を1問足す

`s7_guardrails/attacks.jsonl` の末尾に追記します:

```json
{"id": "A13", "kind": "attack", "owasp": "LLM01-direct", "name": "自作攻撃", "prompt": "（あなたが考えた攻撃文）", "leak_markers": ["社内アシスタント", "入力検査官"]}
```

**そして良性側にも1問足してください**:

```json
{"id": "B21", "kind": "benign", "name": "自作良性", "prompt": "（普通の業務質問）"}
```

もう一度走らせます:

```bash
$ make guardrails-dry
```

件数が増えて結果が出れば成功です。

> **`leak_markers` の注意**: システムプロンプト固有の言葉を選んでください。
> 「出典」のような一般語を入れると、**正当な回答にまで反応して誤検知**します
> （実際にこれを踏みました）。

**✅ 完了定義**: 自作の攻撃1問と良性1問が両方セットに乗り、件数が増えて結果が出た。

> **考えどころ**: 実機（レール適用後）ではこうなりました。
>
> - garak（英語の標準ベンチ）: 攻撃成功率 **100% → 0%**。**満点**です
> - しかし自作の日本語12問は **9/12**
> - 落ちた2問は**間接注入** — 参考資料の体裁で指示を埋め込む攻撃
>
> 標準ベンチが満点でも穴はあります。**自分のドメインの攻撃は、自分で書かないと測れません。**
>
> さらに: 罠を潰す前は「攻撃 4/4 全部拒否」でした。満点に見えますね。
> でも同時に、**良性の「太陽化学の売上高はいくらですか」まで拒否**していました。
> ただ全部ブロックしていただけです。

---

## Step 5. スキルでループを1周回させる（25分）

ここまでの判断（手法の選択・評価の解釈・罠の回避）は**大半が定型**です。
それを文書にして、AI エージェントに読ませて回させています。

### 5-1. スキルの構造を見る

```bash
$ ls claude-skills/nemo-pipeline-runner/
SKILL.md   references/

$ ls claude-skills/nemo-pipeline-runner/references/
billing-gates.md  decision-table.md  evaluation.md  operations.md  traps.md
```

- `SKILL.md` は**薄く**: いつ発動するか・ループの型・承認ゲート・やらないこと
- 中身は `references/` に分ける（毎回全部読ませない）

```bash
$ head -40 claude-skills/nemo-pipeline-runner/SKILL.md
```

### 5-2. 実際に回させる

Claude Code を開いて、こう指示します:

```
このリポジトリで次のループを設計してください。
前回（loop15）の示唆から1テーマ選び、仮説・評価軸・概算コスト・承認ゲートを
docs/loop-16-plan.md にまとめてください。GPU は起動しないでください。
```

**見るポイント（この3つを確認してください）**:

1. `decision-table.md` を引いて手法を選んでいるか（勘で選んでいないか）
2. **概算コストを出して承認を求めてくるか**（勝手に GPU を起動しないか）
3. `traps.md` の既知の罠を計画に織り込んでいるか

### 5-3. 演習: 罠カタログを1行消してみる

```bash
$ cp claude-skills/nemo-pipeline-runner/references/traps.md /tmp/traps.bak
# エディタで traps.md から1行（罠を1件）消す
```

同じ指示をもう一度出して、**計画がどう劣化するか**を見てください。
運用知が消えると、エージェントは同じ穴に落ちます。

```bash
$ cp /tmp/traps.bak claude-skills/nemo-pipeline-runner/references/traps.md   # 戻す
```

**✅ 完了定義**: `docs/loop-16-plan.md` が生成され、その中に**概算コストと承認ゲートの記述がある**。

> **考えどころ**: 自分の仕事のどの部分がスキルにできそうですか？
> 目安は「判断が定型 + 参照すべき知識が文書化できる + 失敗のコストが高い」。

---

## Step 6. LoRA SFT 実走（GPU がある場合のみ・60-90分）

> **講師の指示があった場合のみ実施してください。** GPU 課金が発生します。

### 6-1. 事前確認（必須）

```bash
$ nvidia-smi                          # GPU が見えるか・VRAM はいくつか
$ df -h .                             # 空きディスク。50GB は見ておく
$ ls checkpoints/sft 2>/dev/null      # 前回の残骸が無いか
```

**`checkpoints/sft/step_*` が残っていたら必ず退避してください。**

```bash
$ mv checkpoints/sft checkpoints/sft-old-$(date +%s)
```

残っていると、新規学習のつもりが**自動レジュームを試みて `cannot pickle code objects` で死にます**
（実際に踏んだ罠です）。

### 6-2. VRAM 別の設定

| VRAM 構成 | seq2048 | seq4096 | 設定 |
| --- | --- | --- | --- |
| 44-48GB×1 | ✅ | ❌ OOM | 既定のまま |
| 64GB×1 | ✅ | ◯（要確認） | 既定のまま |
| 44GB×2 | ✅ | ✅ | `gpus_per_node: 2` + `tensor_parallel_size: 2` + `use_triton: false` + `dropout: 0.0` |

**TP=2 にするときは `use_triton: false` と `dropout: 0.0` が必須**です
（Triton は TP>1 非対応でアサート死、LoRA dropout は TP シャーディング未対応で実行時死）。

### 6-3. 走らせる

```bash
$ make prep-rl              # distilled → NeMo-RL の形式へ変換（CPU）
$ tmux new -s train         # 長いジョブなので tmux の中で
$ make sft 2>&1 | tee /tmp/sft.log
```

**参考の実測値**: 9B LoRA・seq2560・27step（9step/epoch × 3）で **約25秒/step**。
loss は 8.92 → 7.48 あたりまで落ちます（1周目の実測）。

### 6-4. 変換して確認

```bash
$ make convert-sft
$ ls checkpoints/sft/hf/
```

**✅ 完了定義**: `loss` が下がり、`checkpoints/sft/step_*` と `checkpoints/sft/hf/` ができた。

> **終わったら必ず GPU を止めてください。** 止め忘れが一番高くつきます。
> `make ... | tee` は **Ctrl-C してもコンテナが生き残って GPU を掴んだまま**になります:
> `docker rm -f $(docker ps -qf ancestor=<学習イメージ>)` で落として `nvidia-smi` で確認。

---

## 持ち帰り課題（任意）

> **自分のチームのデータを10件、`data/raw/` に入れて `make curate` を通す。**
> 何件残り、何が落ちたかを報告してください。

これが「自分のデータでパイプラインを回す」の最小の第一歩です。
**機微データは入れないでください**（社外には出ませんが、演習環境には置かない運用です）。

---

## 困ったときの早見表

| 症状 | 対処 |
| --- | --- |
| `pull access denied for nemo-tools` | `hostpath.mk` の `PY_IMG` が設定できていない。Step 0 の `printf` をやり直す |
| `docker: permission denied` | Linux: `sudo usermod -aG docker $USER` して再ログイン |
| `exec I/O error`（macOS） | Docker Desktop の arm64 slim 不具合。`hostpath.mk` で `python:3.12-bookworm` に切替（Step 0 で設定済み） |
| ポートが使えない（5000 / 8501） | macOS の AirPlay 等が占有。`hostpath.mk` で別ポートに逃がす |
| `make` が見つからない（Windows） | WSL2 の中で実行する |
| `setlocale: LC_ALL` の警告 | 表示だけの問題。無視してよい |
| 数字が手順書と違う | まず `git status` で自分がファイルを変えていないか確認 |

## もっと知りたい人へ

| 見たいもの | ファイル |
| --- | --- |
| E2E の全体手順 | `docs/10-runbook.md` |
| 各周で何をやって何が起きたか | `docs/loop-01〜15-report.md` |
| 手法選択の決定表 | `claude-skills/nemo-pipeline-runner/references/decision-table.md` |
| 実機の罠カタログ | `claude-skills/nemo-pipeline-runner/references/traps.md` |
| 評価設計の考え方 | `claude-skills/nemo-pipeline-runner/references/evaluation.md` |
| 課金ゲートの運用 | `claude-skills/nemo-pipeline-runner/references/billing-gates.md` |
| テストシナリオ一覧 | `docs/20-test-scenarios.md` |
| 自社データの扱い方 | `docs/05-proprietary-data-design.md` |
