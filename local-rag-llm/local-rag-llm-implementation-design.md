# 対象アプリ チャットアシスタント × ローカルLLM 実装設計書

> ℹ 本書は特定顧客の固有情報を除いた**一般化版**です。対象は「金融マーケットレポート向けRAGアシスタント」という汎用像で、セクター体系・コスト数値・執筆者スタイルは例示（自社の実値に置換して使用）。

![使う（推論構成）](serving-routing-overview.svg)

> 本設計の実行時アーキ（モデル使い分け・RAG・ルーティング・フォールバック）の俯瞰図。学習パイプラインは別図 `nemo-pipeline/` の architecture-overview.svg。

> 既存の「金融マーケットレポート向けRAGアシスタント」(RAG + ペルソナ生成) に、ローカル学習モデル + LiteLLM ルーティング + ガードレールを **最小差分** で組み込む設計。
> 段階: Phase 1(クエリ解析4Bの置換) → **Phase 2(回答生成9Bの学習＋ルーティング=コストの本丸)** → Phase 3(セキュリティ) → Phase 4(継続改善)。
> 原則: **評価ファースト**(指標・閾値・評価セットを先に用意し base を先に測る) / **既存資産の最大流用**(LangChain・LiteLLM・LangFuse・`make eval`・12スコア) / **フォールバック常設**(いつでも 100% Claude に戻せる)。

---

## 0. ゴールとスコープ

### ゴール
- 30,000 クエリ/月(200人 × 5 × 30日)の運用で、品質を保ったまま **Claude API コストに天井**をつける。
- 同時に、商用(対象アプリ・金融)で出せる **セキュリティ(ガードレール+ゲートウェイ監視)** を備える。
- 勉強会「作る・使う・守る」三部作の実装根拠とする。

### スコープ内
- クエリ解析(Stage 1)のローカルモデル化と、その学習パイプライン。
- 回答生成(Stage 2)の難易度ルーティングと品質ゲート。
- AIゲートウェイ(LiteLLM)への入力/取得/出力レールと監視。

### スコープ外(本設計では扱わない)
- 既存の取り込み・パーサ・UI・DBスキーマの作り替え(再利用する)。
- 対象アプリ 実データでの学習(先方許諾後。本設計は合成/ダミーで完結)。
- エージェント化(ツール実行)。将来 LLM06(過大な権限)が論点化したら別途。

---

## 1. 前提

### 既存システム(再利用する資産)
| 層 | 既存 | 本設計での扱い |
|---|---|---|
| クエリ解析 | Claude Haiku 4.5 → 構造化JSON | **Phase 1 でローカルに置換** |
| 回答生成 | Claude Sonnet 4 + ペルソナ | **Phase 2 で易ケースをローカルへ** |
| 検索 | PostgreSQL + pgvector / LangChain VectorStore | 変更なし |
| Embedding | OpenAI text-embedding-3-small | 変更なし(取り込み時のみ) |
| 観測 | LangFuse(セルフホスト) + 12スコア | **評価・監視の土台として拡張** |
| 評価 | `make eval`(3層 + `--judge`) | **モデル差し替えで base vs ローカル比較に流用** |
| UI | Streamlit | 変更なし |

### ハードウェア / 環境
- **開発**: DevContainer + Claude Code(CPU/API面)。GPU推論サーバへは `host.docker.internal` でHTTP接続。詳細は **12章**。
- **学習**: Colab A100(40/80GB) または EC2 spot(g6e.xlarge = L40S 48GB)。予算 ~$100。
- **本番推論**: 所有マシン(RTX5090 32GB or Mac Studio 等)で自己ホスト。限界費用ほぼ$0。GTX1660 は使わない。
- **配信**: 学習済み → **vLLM(OpenAI互換, 既定bf16/任意fp8)** → LiteLLM(`127.0.0.1:4000`)。※既定NemotronはvLLM必須でGGUF非対応。GGUF/llama.cppはGGUF対応モデル使用時の代替。

#### 補足: vLLM と Ollama のどちらを使うか

**本設計では vLLM 一択で、選ぶ余地がない。** 好みではなくモデル側の制約:

- 回答生成の既定 **Nemotron-Nano-9B-v2 は Mamba+Attention ハイブリッド**で **GGUF 非対応** → llama.cpp / Ollama に載らない
- クエリ解析の **Qwen3.5 も現状 Ollama 非対応**（`README.md`）

Ollama を使いたい場合は**モデルごと GGUF 対応のものに差し替える**必要があり、そうすると
学習パイプライン（nemo-pipeline）で作った資産が載らなくなる。

一般論としての使い分けは以下。**レイテンシは「同時アクセスがあるか」で逆転する**:

| 観点 | vLLM | Ollama（llama.cpp） |
| --- | --- | --- |
| **同時アクセス時の遅延** | continuous batching で、同時実行しても1件あたりが伸びにくい | 並列は不得手。同時実行数が増えると急速に悪化 |
| **単発の速さ** | GPU があれば速い | 量子化GGUFで軽い。1人で試すぶんには十分速い |
| **起動時間** | 重い（モデルロード＋CUDAグラフ構築） | 軽い。すぐ立ち上がる |
| 量子化 | bf16 基本（AWQ/GPTQ/fp8 も可） | GGUF前提（4bit等）。省メモリだが品質は落ちる |
| 新アーキ対応 | 速い | GGUF 変換の対応待ちが要る |
| 向く用途 | **社内複数人・本番配信** | 個人のローカル試用・Mac |

社内アプリは複数人が同時に叩くので、その意味でも vLLM が正しい。

**レイテンシを本当に詰めるなら**（効く順・すべて vLLM 前提）:

1. **思考モードを明示的に切る** — ここで2回踏んでいる（loop8・loop15）。思考出力が長いと
   総時間が跳ね、loop15 では**1問が180秒でもタイムアウト**した（1問 = LLM 3回 × 長考）。
   `chat_template_kwargs {"enable_thinking": false}` を配信側と評価側で**必ず揃える**
2. **`max_tokens` を必ず指定する** — 未指定だと上限まで書き続ける
3. **ルーティングで easy を確実にローカルへ** — Claude 往復のネットワーク遅延が一番大きい
4. rerank を入れる場合、増分は **rerank 1回ぶんだけ**（loop14 実測: 検索の k0 拡大は行列積の
   top-k のみでほぼ無料）。精度の伸びに対して遅延の代償は小さい

> なお **vLLM と Ollama を並べて実測したことはない**。上表は一般的な性質と、本プロジェクトで
> 実測した vLLM 側の挙動（loop8/10/14/15）にもとづく。差し替えを検討するなら実測すること。
- **コンテナ方針**: CPU/API面はコンテナで全OS共通、GPU面はNVIDIA+Container Toolkit(Linux/WSL2)。Windows将来対応含め **12章** に集約。

### 制約
- 金融ドメイン: データ社外流出最小化、モデルリスク管理(MRM)、no live data passing。
- ライセンス: 学習データ・モデルは Apache/MIT/CC-BY 優先、NC/ND は不採用。

---

## 2. 経済性(なぜローカル化するか)

**例**: 30,000 クエリ/月(=200人×5×30日と仮定)。PoC実測から得た単価(例: 生成 $約0.04/クエリ)を延長した**例示の概算**。数値は一般化した仮値であり、実コストは1クエリのトークン数・キャッシュ率に依存するため、本番投入前に実測で再確認すること。Sonnet 4.6 の現行料金は $3/$15・キャッシュ最大90%減(2026/6時点・確認済)。

| 項目 | 概算 |
|---|---|
| 月間クエリ | 30,000 |
| 現状(全Sonnet) | **~$1,310/月**(うち Sonnet ~$1,260 = 96%) |
| プロンプトキャッシュ後 | ~$790〜920/月 |
| クエリ解析(Haiku) | ~$50/月(ローカル化しても誤差) |

**効くレバーは回答生成のローカルoffloadのみ**(コストの96%がそこ)。品質ゲートを通った分だけ Claude を肩代わり:

| ローカル採用率 | Sonnet節約/月 | 自前機($3k)の回収 |
|---|---|---|
| 30%(初期の現実値) | ~$280〜380 | 約8〜11ヶ月 |
| 50% | ~$470〜630 | 約5〜6ヶ月 |
| 70%(ゲート成熟後) | ~$660〜880 | 約3.5〜5ヶ月 |

**設計上の結論**:
- クラウドGPUを 24/7 で借りるのは不採用(30,000クエリ/月 ≈ 平均0.7クエリ/分でGPUが遊ぶ。レンタル代がAPI代と同等以上)。
- **本番推論=自前ハード(capex)、クラウド=学習専用**。
- サイジングは平均QPSでなく **朝のレポート配信直後の同時実行ピーク** で決める。
- **初期投資(一回限り)**: Sonnet蒸留データ $40〜120 + 4B/9B学習 GPU $40〜80 ≈ **$80〜200**。月$280〜880の offload 節約で **おおむね数週間〜2ヶ月で回収**。「学習だけ借りる」前提なので継続費は自前ハードの電気代のみ。

---

## 3. アーキテクチャ

### Before(現状)
```
Streamlit → [Haiku: クエリ解析] → pgvector検索 → [Sonnet: 回答生成 + ペルソナ] → 出典付き回答
                         ↘ LangFuse トレース ↙
```

### After(本設計)
```
Streamlit
  → [入力レール: インジェクション検知 / PII / スコープ]        ← Phase 3
  → LiteLLM ゲートウェイ(全I/OをLangFuse記録 = 監査証跡)        ← Phase 2/3
      → [クエリ解析] route → ローカル or Haiku(接続断・5xx時)     ← Phase 1
      → [検索が要るか判定(ルール)] ─ 要らない ──────┐            ← 【提案・未決定】
      → pgvector検索                                │
      → [取得レール: チャンク内命令検出・サニタイズ] │            ← Phase 3
      ←──────────────────────────────────────────────┘
      → [回答生成] route → ローカル(qwen3-gen) or Sonnet         ← Phase 2
         └ 品質ゲート(source_exists / tone)不合格 → Claudeへフォールバック
  → [出力レール: 忠実性 / PII漏洩 / プロンプト漏洩 / スキーマ]  ← Phase 3
  → 出典付き回答
```

LiteLLM が唯一の関所(choke point)。モデルの差し込み口は実質 **2箇所**(クエリ解析・回答生成)で、
**そのどちらもルーティング点**である。分岐はすべてルールベースで、LLM に「難しいか」を判定させない。

### 検索の要否分岐（提案・未決定）

現状の設計は**全クエリで pgvector を引く**。しかし挨拶・雑談・スコープ外の依頼・
直前回答の言い換え依頼などは検索が不要で、**引くだけレイテンシと埋め込みコストを払う**。

クエリ解析(①)の出力に `query_type` / `sectors` / `needs_overall_context` があるので、
**追加のLLM呼び出しなしにルールで判定できる**。例:

```python
def needs_retrieval(qa: dict) -> bool:
    if qa.get("query_type") in ("chitchat", "out_of_scope"):   # 要スキーマ拡張
        return False
    if not qa.get("sectors") and not qa.get("commodities") \
       and not qa.get("needs_overall_context"):
        return False        # 対象も期間も文脈も要らない = 検索する対象がない
    return True
```

**採否は測ってから決める**。導入するなら次を必ず確認すること:

- **「検索不要」と判定した質問群の正答率が落ちていないか**（落ちるなら本末転倒）
- 判定ミスで検索を飛ばした場合に、品質ゲート(`source_exists`)が拾えるか
- スキップ率とレイテンシ改善の実測値（改善が小さいなら複雑さに見合わない）

> ⚠ 現行スキーマに「検索不要」を表すフィールドは無い。導入するなら `query_type` の値域拡張か
> `needs_retrieval` の追加が要り、**クエリ解析モデルの再学習または再蒸留が発生する**。
> Phase 1 を学習なしで終えられる見込みがある以上、**これは Phase 2 以降で検討するのが安全**。

---

## 4. 段階計画(エントリ条件・合格・撤退)

| Phase | 内容 | エントリ条件 | 合格(次へ進む)閾値 | 撤退/ロールバック |
|---|---|---|---|---|
| **1** | クエリ解析のローカル化 | 評価ハーネス完成 + base測定済 | ローカルの解析一致率 ≥ base(Claude) かつ スキーマ妥当率100% / ループ0% | model名をHaikuに戻すだけ |
| **2** | **回答生成モデル(9B)の学習＋投入** | Phase 1完了 + 蒸留データ + 品質ゲート実装 | offload率 ≥ 30% を `source_exists`=100%維持で達成、コスト低下を確認 | ルートを100% Sonnetに固定 |
| **3** | セキュリティ | Phase 2の経路が安定 | インジェクション成功率/PII漏洩率が閾値以下、主要OWASP項目に対策配置 | レールを監視のみ(非ブロック)に切替 |
| **4** | 継続改善(後日) | Phase 2/3が安定 | offload率の継続的向上、ドリフトなし | フォールバックで吸収 |

各Phaseは **フォールバックが常に存在** するため、本番を壊さずに段階導入できる。

---

## 5. 評価ハーネス(最初に実装する)

> 前回の敗因は評価を最後に回したこと。**学習前にここを完成**させ、base を先に測る。

### 5.1 既存資産の流用
- 既存 `make eval`(40〜55ケース)と LangFuse 12スコアをそのまま使う。
- **モデル差し替えだけ** で base(Claude) vs ローカル を横並び比較する。ケースは固定(seed)。

### 5.2 モデル差し替えの仕組み
- 評価実行に `--analysis-model` / `--gen-model` を追加し、LiteLLM の model名を切替可能にする。
  ```
  make eval ARGS="--analysis-model claude-haiku --gen-model claude-sonnet"   # baseline
  make eval ARGS="--analysis-model qwen3-analysis --gen-model claude-sonnet"  # Phase 1 検証
  make eval ARGS="--analysis-model qwen3-analysis --gen-model qwen3-gen"      # Phase 2 検証
  ```

### 5.3 指標と合格閾値(閾値は初期値・運用で調整)
**Phase 1: クエリ解析**(既存スコアを流用)
| 指標 | 既存スコア | 閾値 |
|---|---|---|
| セクター判定 | `qa_sectors_match` | ≥ 0.90 |
| query_type判定 | `qa_query_type_match` | ≥ 0.90 |
| 日付範囲判定 | `qa_date_range_match` | ≥ 0.80 |
| 全項目一致 | `query_analysis_match` | ≥ 0.85 |
| スキーマ妥当(有効JSON) | (新規追加) | 100% |
| 健全性(空/反復/ループ) | (新規追加) | 0% |
| レイテンシ・トークン | `response_latency`/`*_tokens` | base比で監視 |

**Phase 2: 回答生成**(既存スコアを流用、`source_exists` を品質ゲートに昇格)
| 指標 | 既存スコア | 閾値 |
|---|---|---|
| 忠実性(作話なし) | `source_exists` | **100%(ゲート)** |
| 出典付与 | `has_source_citation` | ≥ 95% |
| 口調の本人らしさ | `judge_tone_authenticity` | ≥ 3.5/5 |
| 回答正確性 | `judge_accuracy` | ≥ 4.0/5 |
| レイテンシ | `response_latency` | < 10秒 |

### 5.4 リーク防止
- **評価セットは学習データ・GRPO報酬と分離**(同じスコア関数を使うが、評価ケースは学習に出さない)。
- これで「base も 0 点」の評価地獄(エラー文字列が0点集計)も、出力目視 + 検証可能指標で回避。

---

## 6. Phase 1 — クエリ解析モデルの学習

### 6.1 タスク定義
入力 = 日本語の問い合わせ。出力 = 既存 Stage 1 の構造化JSON(下記)。**レポート本文は使わない**(機密性低・コンプラ軽)。

```json
{
  "sectors": ["sector_a"],   // 例: 対象ドメインのカテゴリ
  "date_range": {"start": "2026-03-10", "end": "2026-03-12"},
  "content_types": [],
  "commodities": ["WTI"],
  "query_type": "single",
  "semantic_query": "対象トピックの要点",
  "needs_overall_context": true,
  "response_mode": "analytical"
}
```

### 6.2 データ設計(質>量・合成主役)
| 役割 | データ | 方針 |
|---|---|---|
| 主役 | 合成ダミー問い合わせ → Sonnetで上記JSONにラベル付け | 数千件。既存テストカテゴリ(単一/トレンド/比較/要約/曖昧/エッジ)を網羅 |
| 土台 | 官公庁公開/EDINET(商用可) | 金融用語の地ならしに少量。※JaFInはNCで不可 |
| 汎用維持 | 一般日本語instruction | 破壊学習対策に少量混合 |

- 生成は既存の `commodity_dict.py`(セクター名寄せ辞書)とテストカテゴリ分布に沿わせる。
- **品質管理**: スキーマ検証 / 重複排除 / カテゴリ均衡 / 人手スポットチェック。
- 分割: train / valid / **held-out test(評価ハーネス用、学習に出さない)**。

### 6.3 学習設計
- **モデル**: クエリ解析(本Phase)は構造化JSONの狭タスク → **Qwen3.5-4B で十分**(高速・安価・配布容易)。回答生成は **Phase 2 で Qwen3.5-9B を本格学習**(口調・一貫性に容量が要る)。
- **手順**: SFT(教師ありFT, LoRA) → GRPO(検証可能報酬)。
  - SFT後に即 base→SFT の差分を eval で確認。
- **GRPO 報酬関数 = 既存スコア関数の流用**:
  ```
  reward = w1 * sectors_match(pred, gold)        # = qa_sectors_match のロジック
         + w2 * query_type_match(pred, gold)     # = qa_query_type_match
         + w3 * date_range_match(pred, gold)      # = qa_date_range_match
         + w4 * schema_valid(pred)                # 有効JSON + 必須キー + 型
         - penalty_if(empty or repetition or no-EOS)
  ```
  → フォーマット遵守とEOSを直接矯正(前回の崩壊の再発防止)。
- **ツール**: ~~Unsloth(単機最速・GRPO・Colab配布)。GUI派は LLaMA-Factory。NeMo-RL はデッキ上の「DC規模版」紹介に留める。~~
  → **【2026-08 改訂】nemo-pipeline（NeMo-RL）で作る。** 手順は
  `nemo-pipeline/docs/38-query-analysis-runbook.md`。

  > **改訂の理由**: 本節を書いた 2026-07 以降に nemo-pipeline が15周回り、その `analysis` タスクが
  > **本 Phase のクエリ解析と同じもの**だと分かった。出力スキーマ（§6.1 の8フィールド）が完全に一致し、
  > 評価指標（`analysis_match` / `sectors_match` / `query_type_match` / `date_range_match` /
  > `schema_valid`）も `s6_evaluation/eval.yaml` に実装済み、学習データも
  > **train 316 / valid 39 / heldout 28 件が既にある**（teacher = claude-sonnet）。
  > Unsloth 側で作り直すと、15周ぶんの罠カタログ・課金ゲート・リーク検査が効かなくなる。
  >
  > **さらに重要**: `analysis_match` は **base（未学習）で既に 0.836**（loop9 実測）で、
  > §5.3 の合格ライン 0.85 との差 0.014 は n=70 では測定誤差と区別がつかない。
  > **Phase 1 は学習せずに配信設定だけで終わる可能性が高い。**
  > 逆に loop9 round-1 では別目的の SFT の巻き添えで analysis が **0.836 → 0.115** に崩壊しており、
  > 「base が強いタスクに SFT を当てるな」の典型例になっている。
  > **必ず base を先に測ってから学習の要否を決めること。**
- **再現性**: 200 iterごと S3/Drive にチェックポイント。`caffeinate -i` / spot中断前提の区切り。連続学習≠分割学習(Adam reset)を考慮し1晩完走規模に。
- **学習中の健全性チェック**: 数件 inference して 空応答/反復/EOS閉じを目視(callback)。

### 6.4 配信
**vLLM の OpenAI互換エンドポイント**で配信（既定bf16／任意fp8）→ LiteLLM の model_list に追加。GGUF対応モデルなら llama.cpp も代替可。
> ⚠ **ファクトチェック反映**: Qwen3.5 はマルチモーダル(VLM)で mmproj が分離しているため、**現状 Ollama では GGUF が動かない**(Unslothドキュメント記載)。よって配信は Ollama ではなく llama.cpp / vLLM を用いる。thinking は小型(0.8B〜9B)で既定OFF。

---

## 7. アプリ統合(最小差分)

### 7.1 LLMクライアントの一本化
既存の `ChatAnthropic` / `ChatOpenAI` 直叩きを、**LiteLLM 経由の `ChatOpenAI`** に集約する `get_chat_model()` ファクトリを新設。差し込みは2箇所(解析・生成)のみ。

```python
# src/llm/factory.py (新規)
from langchain_openai import ChatOpenAI

LITELLM_BASE = os.environ["LITELLM_BASE_URL"]      # http://127.0.0.1:4000/v1
LITELLM_KEY  = os.environ["LITELLM_MASTER_KEY"]

def get_chat_model(model_name: str, **kw) -> ChatOpenAI:
    return ChatOpenAI(base_url=LITELLM_BASE, api_key=LITELLM_KEY,
                      model=model_name, **kw)
```

- クエリ解析: `get_chat_model(route_analysis(query))`
- 回答生成: `get_chat_model(route_generation(query_analysis))`
- LangFuse の CallbackHandler は既存どおり付与(LiteLLM側でも記録され二重に追跡可)。
- プロンプトキャッシュ: Claude経路は LiteLLM が `cache_control` をパススルー(ペルソナ~1,700トークンのキャッシュ維持)。ローカル経路は推論サーバ(llama.cpp/vLLM)の kv cache。

### 7.2 LiteLLM 設定(model_list 抜粋)
```yaml
model_list:
  - model_name: claude-sonnet      # 回答生成(難ケース)
    litellm_params: { model: anthropic/claude-sonnet-4-6, api_key: os.environ/ANTHROPIC_API_KEY }
  - model_name: claude-haiku       # クエリ解析(フォールバック)
    litellm_params: { model: anthropic/claude-haiku-4-5, api_key: os.environ/ANTHROPIC_API_KEY }
  - model_name: qwen3-analysis     # クエリ解析(ローカル, Phase 1) — llama.cpp/vLLM の OpenAI互換
    litellm_params: { model: openai/qwen3-analysis, api_base: os.environ/QWEN_ANALYSIS_BASE, api_key: "sk-local" }
  - model_name: qwen3-gen          # 回答生成(ローカル, Phase 2) — llama.cpp/vLLM の OpenAI互換
    litellm_params: { model: openai/qwen3-gen, api_base: os.environ/QWEN_GEN_BASE, api_key: "sk-local" }
litellm_settings:
  fallbacks:                       # 接続断/5xx → Claudeへ自動切替(品質ゲートとは別の層)
    - qwen3-analysis: ["claude-haiku"]
    - qwen3-gen: ["claude-sonnet"]
```
> 現行モデルID(2026/6時点): Sonnet=`claude-sonnet-4-6`($3/$15)、Haiku=`claude-haiku-4-5-20251001`($1/$5)。元アプリ仕様の「Sonnet 4」は旧世代 → 現行は 4.6。
> `QWEN_*_BASE` の既定は **同一composeのサービス名直結**(`http://llm-analysis:8000/v1` 等)。別GPU機へ委譲する時のみ `.env` でIPに上書き。⚠ `host.docker.internal` + `127.0.0.1`バインドの組合せは**Linuxでは通らない**(Docker Desktop限定の挙動)のでコンテナ間通信に使わない。

### 7.3 ルーティング関数
```python
# src/llm/routing.py (新規)
def route_analysis(query) -> str:
    return "qwen3-analysis"            # Phase 1 後は既定でローカル、失敗時 claude-haiku にフォールバック

def route_generation(qa: dict) -> str:
    # Phase 2: 易ケースのみローカル
    easy = (qa["query_type"] == "single"
            and len(qa["sectors"]) == 1
            and qa["response_mode"] == "analytical"
            and "special_column" not in qa["sectors"])
    return "qwen3-gen" if easy else "claude-sonnet"
```

---

## 8. Phase 2 — 回答生成モデル(9B)の学習＋投入(コストの本丸)

> **ここが本丸**。コストの96%は回答生成なので、**応答に9B SLMを使えなければコストキャップは成立しない**。Phase 1(4B解析)は配管と評価の証明、Phase 2が本番の節約を生む。

### 8.1 なぜ9Bでも回答生成が成り立つか(設計の核心)
- **RAGが知識を外付けにする** → 9Bは"ドメイン博識"である必要がなく、**取得済みコンテキストを忠実に・執筆者の口調で描く**ことだけ学べばよい。
- 学ぶのは (a) grounding(出典に忠実) (b) persona(口調) (c) format(出典タグ/長さ) の3点。知識は検索が担う。
- これにより 9B でも easy/factual ケースは Sonnet 品質に肉薄しうる(難ケースはルーティングで Claude に回す)。

### 8.2 データ設計(Sonnet蒸留 + 既存ペルソナ資産)
| 役割 | データ | 作り方 |
|---|---|---|
| 主役 | **Sonnet蒸留**: (取得チャンク+質問)→ Sonnet生成の出典付きペルソナ回答 | 既存アプリでSonnetを走らせ (入力, Sonnet回答) を収集。1〜3k件 |
| 補強 | 執筆者の実レポート / 既存 few-shot(04-persona) | analytical / casual 両モードを網羅 |
| 評価 | held-out(学習に出さない) | base vs SFT vs GRPO 比較用 |

- **知識は学習に載せない**(RAGが担う)→ ダミー/合成チャンクで足り、機密性が低くコンプラ軽。
- 蒸留コスト(正直): Sonnet生成 1〜3k件 × ~$0.04 ≈ **$40〜120**(一回限り)。予算内、offload節約で回収。件数で調整可。

### 8.3 学習設計(SFT → GRPO) ← 9B を本格学習
- **モデル**: **Qwen3.5-9B**(bf16 LoRA ~22GB)。借りGPU(A100 40/80GB・L40S 48GB)に収まる。回答生成は口調・一貫性に容量が要るため4Bでなく9B。
- **SFT(=ペルソナ蒸留)**: Sonnet回答を模倣。従来「Phase 4(後日)」としていたペルソナ蒸留を **本丸の一部として前倒し**。
- **GRPO(報酬は"検証可能"を主・"判定"を従)**:
  ```
  reward = w1 * source_exists(answer, chunks)     # 引用出典が検索結果に存在(検証可能・強い信号)
         + w2 * citation_format(answer)           # 【出典:】タグ(正規表現・検証可能)
         + w3 * number_grounded(answer, chunks)   # 価格/数値がチャンクに存在(検証可能)
         + w4 * judge_tone(answer)                # 口調(LLM/分類器判定・小weight)
         - penalty(empty / repetition / no-EOS / 長さ逸脱)
  ```
  - **忠実性・出典・数値・形式(検証可能)を高weight、tone判定を低weight**。これで報酬ハッキング/審判ノイズを抑える。
  - `source_exists` は Phase 3(出力レール)とも品質ゲート(8.5)とも **同じ関数** ── 一つの忠実性概念を、学習報酬・運用ゲート・セキュリティで共有。
- **rollout注意**: 回答生成はJSON解析より生成長が長い → 生成長に上限、group内サンプル数を抑制、まず4Bで報酬設計を検証してから9Bへ。
- **再現性**: S3チェックポイント / caffeinate / spot中断前提。9B GRPOは長い(目安 15〜30 GPU時間)。
- **配信**: **vLLM(OpenAI互換, bf16/fp8)** → LiteLLM。GGUF/llama.cppは代替モデル時のみ。

### 8.4 難易度ルーティング(投入)
| → ローカル(qwen3-gen, 9B) | → Claude(claude-sonnet) |
|---|---|
| single / 単一セクター / analytical / 事実照会 | trend / comparison / summary |
| | column(特集コラム)/ casual / mixed(ペルソナ重要) |

### 8.5 品質ゲート(採用条件)
ローカル生成 → 採用前に検査(GRPO報酬と同じ関数):
1. `source_exists` == 1(**作話=即不採用**。金融で最重要)
2. `has_source_citation` == 1(出典タグあり)
3. (任意) tone チェック ≥ 閾値
- 不合格 → **claude-sonnet に再生成フォールバック**。ユーザーには合格回答のみ提示。

### 8.6 計測(LangFuse)
- `route_target`(local/claude)、`gate_passed`(0/1)、`escalated`(0/1) をスコア化。
- ダッシュボードで **offload率・ゲート通過率・コスト内訳・エスカレーション率** を可視化(デモの山場)。
- 正直な初期値: offload 30〜50%。9B学習の品質向上 + ルート対象拡大で上げる。

---

## 9. Phase 3 — セキュリティ(ゲートウェイ + ガードレール)

### 9.1 脅威モデル(OWASP LLM Top 10 / 2025 から本アプリに効くもの)
| ID | 脅威 | 本アプリでの現れ方 |
|---|---|---|
| LLM01 | プロンプトインジェクション | 直接(ユーザー入力)+ **間接(RAG取り込み文書に命令)** |
| LLM02 | 機微情報漏洩 | 顧客コンテキスト/PIIの流出 |
| LLM05 | 出力ハンドリング不備 | 出力が下流(UI/SQL)で悪さ |
| LLM07 | システムプロンプト漏洩 | **執筆者スタイル(persona)= 商用IP の抽出** |
| LLM08 | ベクトル/埋め込みの弱点 | 取り込み時のRAG汚染 |

> 最大の急所は **間接インジェクション**。RAG だけでは防げない(研究で確認済)→ 取得レールが必須。Gmail取り込みの偽レポート/コラムに命令を仕込む攻撃面が現実に存在する。

### 9.2 多層防御(LiteLLMゲートウェイの pre/post hook に実装)
- **入力レール**: ジェイルブレイク/インジェクション検知(Llama Guard / Prompt Guard)、PII検知・マスク(Microsoft Presidio)、トピック/スコープ強制(市場レポート以外は断る)。
- **取得レール(RAG固有)**: 取得チャンク内の命令文を検出・サニタイズ、**データと命令の境界を明示(信頼境界)**。取り込み時スキャン(LLM08)も併用。
- **出力レール**: 忠実性(**既存 `source_exists`** を再利用)、PII漏洩、**システムプロンプト/ペルソナ漏洩フィルタ**、スキーマ妥当、下流サニタイズ。

### 9.3 ツール配置(2〜3個を重ねる)
| 役割 | ツール | 配置 |
|---|---|---|
| 対話/トピック制御・5レール | **NeMo Guardrails(NVIDIA, Colang)** | ゲートウェイの枠組み(※ベータ。本番は追加hardening) |
| インジェクション/コンテンツ分類 | **Llama Guard(8B/1B, ローカル)** | 入力レール |
| 入出力スキャナ(PII/secrets/toxicity) | **LLM Guard** | 入力/出力レール |
| PII検知・マスク | **Presidio** | 入力/出力レール |
| 構造化出力検証 | **Guardrails AI** | 出力レール(JSON系) |

- **ローカルで動かす**(Llama Guard/Presidio)→ 検査データも社外に出ない(MRMの一貫性)+ 自前GPUに"守る"仕事。

### 9.4 監視・レッドチーミング
- **監視 = LangFuse**(既存): ガードレール発火をスコア化・時系列・アラート・低スコアtrace調査 = SIEMライト。
- **レッドチーミング**: promptfoo / garak の攻撃カタログを CI に。**防御も評価ファースト**:
  - 指標: インジェクション成功率 / PII漏洩率 / 忠実性違反率(いずれも閾値以下)。
- **レイテンシ予算**: 高速分類器 10〜50ms は同期ゲート、LLM審判 200〜1000ms は非同期監視に回す(10秒目標を守る)。

### 9.5 正直な前提
デプロイ面の最小権限も必須（app=127.0.0.1バインド+TLSプロキシ、ゲートウェイ/DBは強いキー必須化、no-new-privileges、推論委譲時はmTLS+送信元制限、.envはSOPS暗号化）。完全なインジェクション防御は不可能。**多層 + 監視 + 最小権限**で確率を下げ、起きたら検知・記録する。残存リスクは受容してMRMに記録。OWASP LLM Top 10 / NIST AI RMF / MITRE ATLAS に対応づけて整理(商用提案資料の骨にも流用)。

---

## 10. Phase 4 — 継続改善(後日)

offload率を上げ続ける運用フェーズ。(1) 蒸留データ追加で 9B の tone/grounding を底上げ、(2) ルート対象を trend など隣接 query_type へ慎重に拡大(品質ゲートで安全に試せる)、(3) continuous eval でドリフト監視。難ケースは常に Claude フォールバックが吸収。
> ※ ペルソナ蒸留は本設計では **Phase 2(8.3)に前倒し済み**。本フェーズはその継続的な底上げに位置づける。

---

## 11. ディレクトリ構成(新規分のみ)

```
rag-chat-app/                      # 既存アプリ(再利用)
├── .devcontainer/                 # ★ DevContainer(Claude Code開発, finance-llm流用)
│   ├── devcontainer.json          #   hooks/sandbox/拡張、host.docker.internal 許可
│   └── Dockerfile                 #   uv + Python3.12(CPU/API面)
├── src/
│   ├── llm/
│   │   ├── factory.py             # ★ get_chat_model() (LiteLLM集約)
│   │   └── routing.py             # ★ route_analysis / route_generation / gate
│   ├── guardrails/                # ★ Phase 3
│   │   ├── input_rail.py          #   Llama Guard / Presidio / scope
│   │   ├── retrieval_rail.py      #   チャンク命令検出・サニタイズ
│   │   └── output_rail.py         #   source_exists / PII / prompt-leak
│   └── eval/                      # 既存 make eval を拡張
│       └── run_eval.py            # ★ --analysis-model / --gen-model
├── litellm_config.yaml            # ★ model_list(claude / qwen3-*)
├── compose.yml                    # ★ CPU/API面(全OS共通): app/litellm/db/langfuse/presidio
└── compose.gpu.yml                # ★ GPU面(Linux/WSL2のみ): 推論サーバ/llama-guard

finance-llm-train/                 # ★ 学習リポ(別)
└── finetune/
    ├── data/                      # 合成タスクデータ + 官公庁公開/EDINET(商用可) + 蒸留データ(git除外)
    ├── scripts/
    │   ├── synth_queries.py       #   合成問い合わせ生成(Phase 1)
    │   ├── label_with_sonnet.py   #   Sonnetで構造化JSONラベル付け(Phase 1)
    │   ├── reward_analysis.py     #   解析GRPO報酬(既存スコア関数を流用)
    │   ├── train_analysis_4b.py   #   クエリ解析4B: SFT → GRPO
    │   ├── distill_answers.py     # ★ 既存アプリでSonnet出典付き回答を収集(蒸留, Phase 2)
    │   ├── reward_gen.py          # ★ 回答生成GRPO報酬(忠実性/出典/数値/形式 + tone判定)
    │   └── train_gen_9b.py        # ★ 回答生成9B: SFT(蒸留) → GRPO
    └── eval/
        ├── held_out_analysis.jsonl  # 解析の評価セット(学習に出さない)
        └── held_out_gen.jsonl       # 回答生成の評価セット(学習に出さない)
```

---

## 12. 環境・コンテナ構成(DevContainer / Docker / Windows対応)

> 結論: **コンテナでOK**。ただし **GPU依存で2面に分ける** のが要点。CPU/API面はどのOSでもコンテナで完結、GPU面だけ NVIDIA + Container Toolkit が要る。

### 12.1 2面に分割(GPU依存で切る)
| 面 | 構成要素 | コンテナ | GPU |
|---|---|---|---|
| **CPU/API面** | app(Streamlit)/LiteLLM/PostgreSQL+pgvector/Langfuse/Presidio(PII)/eval/データ合成 | `compose.yml`(共通) | 不要 |
| **GPU面(ローカル=本番推論のみ)** | 推論サーバ(llama.cpp/vLLM で qwen3-analysis/gen)/Llama Guard | `compose.gpu.yml`(分離) | **NVIDIA必須** |
| **学習(ローカルで動かさない)** | Unsloth SFT/GRPO | — | **Colab/EC2 専用** |

- CPU/API面は **Mac/Windows/Linux 共通** で `docker compose up`。既存 `07-environment` の compose(db=pgvector, app, langfuse network external)に **litellm と presidio を追加**。
- GPU面(本番推論)は **CUDA環境(Linux or WSL2)でのみ起動**。compose側は `deploy.resources.reservations.devices` でGPU予約、`--gpus all` 相当。
- **学習は常に Colab/EC2(借りGPU)で実施。ローカル(Mac/Windows/WSL2)では学習しない**。本番ホストに学習を載せず、成果物(学習済みモデル)だけを推論サーバに配置する（配信はvLLM、量子化はfp8任意）。
- **Mac(Apple Silicon)はCUDA無し → GPUコンテナ不可**。ローカル推論は MLX ネイティブ、または GPUホスト(Windows/Linux機)へHTTP委譲。

### 12.2 開発: DevContainer + Claude Code
- 開発は **CPU/API面のDevContainer** で行う(既存 finance-llm の DevContainer 流用: hooks/sandbox/SOPS暗号化)。Claude Code でここを構築 ── app改修・`get_chat_model()`・ルーティング・ガードレールglue・eval・データ合成は **すべてGPU不要**。
- **DevContainer は compose統合型**(`dockerComposeFile` で `compose.yml` と合成)にし、db/litellm/presidio へ**サービス名で直結**する(`litellm:4000` 等)。GPU推論サーバも同一composeなら `llm-gen:8000` で直結、別GPU機ならIP指定(finance-llm Phase 7 のHTTP委譲パターン)。⚠ `host.docker.internal` 頼みの接続はLinuxで壊れるため使わない。
- **学習・GPU推論を開発DevContainer内でやらない**(CUDA要・重い・中断リスク)。学習はColab/EC2、本番推論はGPUホスト。
- 秘匿情報は平文 `.env` を置かず、既存の **SOPS + age + Keychain** 運用を踏襲。

### 12.3 Windows対応(将来)
あなたの知見(ネイティブWindowsはsandbox非対応 → WSL2が必要)を前提に、**dev も prod も WSL2 経由**に寄せる。

**(a) 開発(Windows)**
- **Docker Desktop(WSL2バックエンド)上の DevContainer + Claude Code**。sandbox/security の観点でネイティブWindowsではなくWSL2側に置く。

**(b) 本番推論(WindowsのNVIDIA GPU機, 例: RTX5090) ── 推論専用。学習はここで行わない(Colab/EC2)**
- **WSL2 + Docker + NVIDIA Container Toolkit** でGPUコンテナ(vLLM/llama.cpp, Llama Guard)を `--gpus all` 起動。
  - Docker Desktop の `--gpus` は **4.30+** でWSL2正式対応。GPU確認: `docker run --rm --gpus=all nvcr.io/nvidia/k8s/cuda-sample:nbody nbody -benchmark`。
  - CUDA-on-WSL2 は成熟。**Windowsドライバのみ入れ、WSL2内にLinux NVIDIAドライバを入れない**(libcuda.so がスタブされるため)。
  - ⚠ **RTX5090(Blackwell, sm_120)はWSL2でドライバパスに固有の注意**。Blackwell用シンボルが別ディレクトリにあり、追加マウントが要る場合がある(4090以前は素直に動く)。導入時に要検証。
- **vLLM はネイティブWindows非対応**(公式が明言、ロードマップ無し)。Windowsで動かす正規3パス: ① **WSL2 + 公式Linux vLLM**(推奨, ネイティブ比 約20〜40%スループット減) ② **Docker Model Runner**(Docker Desktop 4.54+ / WSL2 / CC8.0+ で vLLM 対応, 2025/12〜) ③ コミュニティfork。**llama.cpp は Windows CUDA ビルドあり**で、単機・低QPSなら堅い選択。
- CPU/API面(app/LiteLLM/PG/Langfuse)も同じWindows機の Docker Desktop で起動し、推論サーバへ接続。

### 12.4 クロスプラットフォーム一貫性
- `compose.yml`(CPU/API面)= Mac/Win/Linux 共通。`compose.gpu.yml`(GPU面)= CUDAがある環境(Linux or WSL2)のみ。起動は `docker compose -f compose.yml -f compose.gpu.yml up`(GPUホスト時)/ `-f compose.yml up`(Mac等)。
- これで「Macで開発 → Windows GPU機(WSL2)で本番推論」が同一composeで通る。自前ハード=Windows GPU機 という capex 前提とも整合。
- サイジングは平均QPSでなく **朝のレポート配信直後の同時実行ピーク** で(2章のとおり)。

---

## 13. リスクと撤退基準(まとめ)

| リスク | 兆候 | 撤退/対処 |
|---|---|---|
| ローカル解析がbaseに届かない | `query_analysis_match` < base | route_analysis を claude-haiku に戻す(差分ゼロ) |
| ローカル回答が作話 | `source_exists` < 100% | 品質ゲートで自動フォールバック(本番は無害) |
| offloadが伸びずコスト効果薄 | offload率 < 30% | 蒸留データ追加で9B底上げ or 対象query_type拡大を検討 |
| 学習が中断/Adam reset | 夜間再起動 | S3チェックポイント + caffeinate + 1晩完走規模 |
| ガードレールが遅延を増やす | p90 > 10秒 | LLM審判を非同期監視へ、同期は高速分類器のみ |
| 間接インジェクション貫通 | redチームで成功 | 取得レール強化 + 監視で検知・記録(完全防御は不可と明記) |
| 学習コスト超過 | > $100 | spot + 小規模iter、Colab無料枠で配管確認 |

---

## 14. 用語・参照

- **SFT** Supervised Fine-Tuning / **LoRA** Low-Rank Adaptation / **GRPO** Group Relative Policy Optimization / **RLVR** Reinforcement Learning with Verifiable Rewards / **RAG** Retrieval-Augmented Generation / **EOS** End Of Sequence / **MRM** モデルリスク管理。
- 関連: `local-llm-study-deck-outline.md`(勉強会構成案・三部作)、既存アプリ設計(01-architecture〜09-test-catalog)。
- 標準: OWASP LLM Top 10(2025) / NIST AI RMF / MITRE ATLAS。

---

## 付録: 実装着手順(チェックリスト)

1. [ ] **環境**: `compose.yml`(CPU/API面)で app/LiteLLM/PG/Langfuse/Presidio を起動、DevContainer + Claude Code で開発開始。GPU面は `compose.gpu.yml`(Linux/WSL2)で別途
2. [ ] LiteLLM に model_list(claude-sonnet/haiku, qwen3-analysis/gen)を登録、`/health` 確認
3. [ ] `get_chat_model()` 導入、既存2箇所(解析・生成)を差し替え(まだ全Claude)→ 挙動不変を確認
4. [ ] `make eval` に `--analysis-model/--gen-model` 追加、**base を測定**(超えるべき線を確定)
5. [ ] 【Phase 1】合成クエリ生成 → Sonnetラベル → スキーマ検証 → train/valid/held-out 分割
6. [ ] 【Phase 1】Unsloth で SFT(Qwen3.5-4B)→ GRPO(報酬=既存スコア関数)→ held-out で base vs SFT vs GRPO
7. [ ] 【Phase 1】vLLM配信(bf16/fp8)→LiteLLM、`route_analysis` を解析モデルに(Phase 1 完了)
8. [ ] 【Phase 2】`distill_answers.py` で Sonnet出典付き回答を 1〜3k件 収集(蒸留データ)→ held-out分割
9. [ ] 【Phase 2】Unsloth で **9B SFT(蒸留)** → **9B GRPO(報酬=忠実性/出典/数値/形式 + tone判定)**、まず4Bで報酬設計を検証
10. [ ] 【Phase 2】held-out で base(Sonnet) vs 9B を source_exists/tone/accuracy で比較、vLLM配信(bf16/fp8) `nemotron-gen`
11. [ ] 【Phase 2】`route_generation` + 品質ゲート(source_exists) + フォールバック実装、offload率を計測
12. [ ] 【Phase 3】ガードレール(入力/取得/出力)をゲートウェイhookに、promptfoo/garakで攻撃評価
13. [ ] LangFuse ダッシュボードで コスト/offload/セキュリティ発火 を一望
