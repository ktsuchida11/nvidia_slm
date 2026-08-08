# S8 Retriever（loop12: RAG vs DAPT費用対効果比較）

DAPT知識注入（loop10: closed-book probe完全同点）の対抗馬として、検索で文書を渡す
open-book 経路を作る。設計と判定基準は docs/loop-12-plan.md が正。

## 構成（可動部最小）

- `chunker.py` — 800字・stride 400 でチャンク化（doc_id 追跡・純関数）
- `embedder.py` — nim（NeMo Retriever embedding NIM・本線）/ hf（e5フォールバック）/ dummy（dry・テスト）
- `build_index.py` — chunks.jsonl + vectors.f32（float32生バイナリ）+ meta.json。中断再開可
- `retrieve.py` — フラット行列の内積 top-k（numpy 経路 + stdlib フォールバック）
- `recall_eval.py` — 評価①: 検索品質 recall@k / MRR（gold doc_id 照合・ローカル採点）

ベクトルDBは使わない: 約20万チャンク×1024次元≈0.8GB は総当たりで十分。

## 手順

```bash
# ローカル配管検証（GPU/API不要・数秒〜数十秒）
make retriever-index-dry && make retriever-recall-dry && make rag-eval-dry

# ノード（要 NGC_API_KEY・ゲートA承認後）
make serve-embed                                   # embedding NIM :8001
make retriever-index LIMIT=50                      # 小規模で配管確認
make retriever-index                               # 全量（中断再開可）
make retriever-recall TAG=recall_nim               # 評価①
make serve-nemotron && make rag-eval TAG=rag199    # 評価②（closed-book 0.0402 と比較）
```

評価②の details には `ctx_sources` / `gold_in_ctx` が入り、誤答を
「検索ミス（gold_in_ctx=0）/ 読解ミス（gold_in_ctx=1 で誤答）」に切り分けられる。
