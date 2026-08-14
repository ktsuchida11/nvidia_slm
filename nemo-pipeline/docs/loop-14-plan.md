# ループ14 計画 — rerank NIM: 検索律速の解消（k=100→rerank→top5）

テーマ: **NeMo Retriever text reranking NIM** による検索側の改善。学習なし・教師APIなしの推論オンリー。
前提: loop13 総括（docs/loop-13-report.md）で「読解は SFT で改善済み・残りの誤答は検索律速」が確定。

## 1. 仮説と問い

**問い**: 埋め込み検索を広く取り（k0=50〜100）、rerank NIM で並べ直して top5 に絞れば、
プロンプト長を増やさずに gold_in_ctx 率が上がり、probe_acc が伸びるか？

根拠（loop13 実測）:

- rag199 の誤答のうち検索ミス（gold_in_ctx=0）は約60問（1 − 0.6985 ≈ 30%）
- RAG_K スイープで k=5→10 により gold_in_ctx 0.6985→0.7387、probe_acc 0.5075→0.5427。
  つまり **gold は rank 6-10 に一定数眠っている** → k0 をさらに広げて rerank で上位に引き上げれば、
  プロンプトは top5 のまま検索ミスの一部を回収できるはず
- P1 誤答分類の「検索粒度系14問」も、粗い候補集合を精密採点する rerank の守備範囲

教材面: NeMo Retriever の未消化コンポーネント（rerank NIM: llama-3.2-nv-rerankqa-1b-v2）を消化し、
SKILL.md のコンポーネント表を埋める。

## 2. 変更点（実装は全て $0・ローカル）

| 対象 | 変更 |
| --- | --- |
| `s8_retrieval/reranker.py`（新規） | rerank バックエンド: `nim`（/v1/ranking API・リトライ付き）/ `dummy`（決定的 3-gram 重なり採点。dry・テスト用） |
| `s6_evaluation/probe_qa.py` | `--rerank --rerank-k0 N`: 検索を k0 で広く取り rerank で `--rag-k` 件に絞る。report に rerank 設定を記録 |
| `Makefile` | `serve-rerank`（rerank NIM 起動 :8003）、`rag-eval` の RERANK/RERANK_K0 対応、`rag-rerank-dry`（$0 配管）、clean に rerank-nim 追加 |
| `tests/test_rerank.py`（新規） | dummy 採点の決定性・top-k 絞り込み・NIM ペイロード構築・probe_qa 統合 |

プロンプト・採点・評価セット（probe_qa.jsonl n=199）は**一切変更しない** — loop12/13 の実測値と直接比較可能に保つ。

## 3. 評価軸と判定値

比較の物差し（loop13 実測・全て sft13 アダプタ）:

| 構成 | probe_acc | gold_in_ctx_rate |
| --- | --- | --- |
| k=5（ベースライン） | 0.5075 | 0.6985 |
| k=10（無料の改善・プロンプト2倍） | 0.5427 | 0.7387 |

loop14 の判定:

| 層 | 指標 | 判定値 |
| --- | --- | --- |
| ① 検索改善 | gold_in_ctx_rate（rerank後 top5） | **≥ 0.75**（k=10 の 0.7387 を top5 のまま超える） |
| ② エンドツーエンド | probe_acc（rerank後 top5） | **≥ 0.55**（k=10 の 0.5427 超え） |
| ③ 切り分け | ①が改善したのに②が伸びない場合 | 「再び読解律速」と判定し、誤答の gold_in_ctx=1 内訳を分類して次段の材料にする |

測定マトリクス（ノード・各 199問）:

1. `sft13 + rerank k0=50 → top5`（主測定）
2. `sft13 + rerank k0=100 → top5`（k0 の効き確認）
3. 判定値付近なら `top10` も追加（rerank × プロンプト長の合算）

## 4. 概算コストと承認ゲート

| 項目 | 内容 | 概算 |
| --- | --- | --- |
| GPU（唯一の課金） | ノード起動 + rerank NIM pull + NIM×2/vLLM 配信 + 評価2-3本 | **$6-10**（~1.5-2.5h） |
| 教師API | なし | $0 |

ゲート:

- **ゲートA（要承認）**: ノード起動 + rerank NIM 疎通（数問の smoke）→ 実測レートで再見積り
- **ゲートB（要承認）**: 本評価 2-3本 → 即ノード停止

## 5. 実機の既知罠（loop12 の NIM 3点セットを再適用）

- nvcr.io は NGC API キーで docker login（`-u '$oauthtoken'`）
- `~/.cache/nim` の所有権を uid 1000 に（chown 済みなら再発しない想定）
- GPU ピン留め: embed NIM + rerank NIM = GPU0 / vLLM = GPU1（rerank は 1B・embed と同居可の見込み。
  Free memory エラーが出たら配置を見直す）
- ブリッジ IP はシェル/tmux ごとに再取得

## 6. 実施フロー

```
P1 設計       : 本ドキュメント PR → 承認
P2 実装($0)   : reranker.py + probe_qa 拡張 + Makefile + テスト + rag-rerank-dry pass → PR
P3 実機(課金) : ゲートA 疎通 → ゲートB 本評価 → 結果S3退避 → ノード停止
P4 総括       : loop-14-report.md + スキル書き戻し（NIM消化表の rerank を「済」に）
```
