"""loop12 評価①: 検索品質 — recall@k / MRR（gold doc_id 照合・完全ローカル・API課金なし）。

probe_qa.jsonl の各質問で検索し、gold source（QA の出典文書）が文書単位ランキングの
上位に入るかを測る。②生成品質（make rag-eval）と分離して測ることで、不合格時に
「検索の問題 / 読解の問題」を切り分けられる（evaluation.md「1指標で合否を語らない」）。
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from embedder import get_embedder  # noqa: E402
from retrieve import Retriever  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[recall] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def doc_ranking(hits: list[dict]) -> list[str]:
    """チャンクの hit 列 → 出現順の一意な文書ランキング。"""
    seen, docs = set(), []
    for h in hits:
        if h["source"] not in seen:
            seen.add(h["source"])
            docs.append(h["source"])
    return docs


def compute_metrics(ranked_docs: list[list[str]], golds: list[str],
                    recall_at: list[int]) -> dict:
    n = len(golds)
    metrics = {}
    for k in recall_at:
        metrics[f"recall@{k}"] = round(
            sum(1 for r, g in zip(ranked_docs, golds) if g in r[:k]) / max(n, 1), 4)
    rr = 0.0
    for r, g in zip(ranked_docs, golds):
        if g in r:
            rr += 1.0 / (r.index(g) + 1)
    metrics["mrr"] = round(rr / max(n, 1), 4)
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="/data/retriever")
    ap.add_argument("--qa", default="/data/pretrain/probe_qa.jsonl")
    ap.add_argument("--out", default="/results/loop12")
    ap.add_argument("--tag", default="recall")
    ap.add_argument("--search-k", type=int, default=100,
                    help="チャンク検索深さ（文書ランキングを recall@max まで安定させる余裕）")
    ap.add_argument("--recall-at", default="1,5,10")
    ap.add_argument("--only-indexed", action="store_true",
                    help="gold がインデックス内の QA に限定（dry の部分インデックス用）")
    a = ap.parse_args()

    retriever = Retriever(a.index, get_embedder())
    rows = [json.loads(line) for line in pathlib.Path(a.qa).open(encoding="utf-8")
            if line.strip()]
    if a.only_indexed:
        indexed = {c["source"] for c in retriever.chunks}
        kept = [r for r in rows if r["source"] in indexed]
        log.info("--only-indexed: %d/%d 問に限定（%d問はインデックス外のため除外）",
                 len(kept), len(rows), len(rows) - len(kept))
        rows = kept

    recall_at = [int(k) for k in a.recall_at.split(",")]
    ranked_docs, details = [], []
    for i in range(0, len(rows), 32):
        batch = rows[i:i + 32]
        for row, hits in zip(batch, retriever.search([r["q"] for r in batch],
                                                     k=a.search_k)):
            docs = doc_ranking(hits)
            ranked_docs.append(docs)
            details.append({"q": row["q"], "gold": row["source"], "top_docs": docs[:10],
                            "top_chunks": [{"chunk_id": h["chunk_id"],
                                            "score": h["score"]} for h in hits[:5]],
                            "gold_rank": docs.index(row["source"]) + 1
                            if row["source"] in docs else None})
        log.info("%d/%d", min(i + 32, len(rows)), len(rows))

    metrics = compute_metrics(ranked_docs, [r["source"] for r in rows], recall_at)
    report = {"tag": a.tag, "n": len(rows), "backend": retriever.meta["backend"],
              "model": retriever.meta["model"], "search_k": a.search_k, **metrics}
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"retriever_recall_{a.tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / f"retriever_recall_{a.tag}_details.jsonl").open(
            "w", encoding="utf-8") as w:
        for d in details:
            w.write(json.dumps(d, ensure_ascii=False) + "\n")
    log.info("report: %s", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
