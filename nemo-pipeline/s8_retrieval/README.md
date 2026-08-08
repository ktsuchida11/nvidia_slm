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

## loop13: grounded QA SFT データ構築（build_grounded_sft.py）

読解ミス23ppの回収（docs/loop-13-plan.md が正）。プロンプトは `probe_qa.py --rag` の
評価時描画（RAG_SYS / build_rag_user）を import して共有 = 学習と評価の形式を構造的に一致。

```bash
# ローカル（$0）
make grounded-dry                  # seed→合成QA→dummy索引→build の自己完結配管検証
make grounded-seed                 # probe-50以外から500文書を決定的サンプル

# ゲートC承認後（教師API）
make grounded-gen GEN_DOCS=13      # まず~50問で単価実測+サンプル目視検査
make grounded-gen                  # 承認後に全量（追記式・再課金ゼロで再開）

# ノード（要 NIM + 全量インデックス /data/retriever）
make grounded-build                # リーク検査→実検索top5→/data/pretrain/grounded/
make grounded-sft                  # ゲートA/B承認後（ckptは/ckpt/sft_grounded に分離）
```

データ品質の規律: probe 199問・probe文書は学習不使用（リーク検査違反で即死）/
gold文書が文脈内でも答えがチャンク内に無い例は破棄（ハルシネーション学習の予防）/
検索ミス例は「記載なし」教師として上限18%。統計は `grounded/build_report.json`。
