"""loop13: grounded QA SFT データ構築 — RAG文脈+質問→短答の形式学習データを作る。

読解ミス23pp（loop12: gold文脈を渡しても誤答32%）の回収が目的。docs/loop-13-plan.md が正。

3モード:
  --seed   corpus_train から probe-50 **以外**の文書を決定的にサンプル → grounded_seed.jsonl
           （この後 probe_qa.py --generate で教師QAを生成する = make grounded-gen・ゲートC）
  --build  教師QA → リーク検査 → 実検索 top-k → SFT jsonl（ResponseDataset形式）
           プロンプトは probe_qa.py の RAG_SYS / build_rag_user を import して共有
           （学習と評価の形式を構造的に一致させる — loop8/9 の非対称敗因対策）
  --dry    $0 自己完結配管検証: seed → 合成QA（本文の逐語断片）→ dummy索引 → build

データ品質の規律:
  - probe_qa.jsonl（199問）と probe 文書は学習に一切使わない（リーク検査で強制・違反は即死）
  - gold文書が文脈内でも答えの事実がチャンク内に無い例は**破棄**（文脈に無い事実を
    答える=ハルシネーションを教えてしまうため。件数は build_report.json に記録）
  - 検索ミス例は「記載なし」の教師として上限 noans-frac（既定0.18）まで混入・超過は破棄
    （loop1 罠「全件記載なし」の構造的予防。それでも生成後のサンプル目視検査は must）
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import tempfile
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s8_retrieval"))
sys.path.insert(0, str(ROOT / "s6_evaluation"))

from probe_qa import RAG_SYS, build_rag_user, norm_text  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[grounded] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

NOANS_ANSWER = "記載なし"


def stable_hash(s: str) -> int:
    """決定的ハッシュ（Date/randomなし・言語/環境非依存）。サンプルと分割の両方で使う。"""
    return zlib.crc32(s.encode("utf-8"))


def load_jsonl(path: str | pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in pathlib.Path(path).open(encoding="utf-8")
            if line.strip()]


# ---- --seed: 種文書サンプル（純関数） ----------------------------------------
def sample_seed(corpus_path: str, probe_corpus_path: str, n_docs: int) -> list[dict]:
    """probe文書を除外し、source のハッシュ順で n_docs 件を決定的に選ぶ。"""
    probe_sources = {d["source"] for d in load_jsonl(probe_corpus_path)}
    pool = [d for d in load_jsonl(corpus_path) if d["source"] not in probe_sources]
    pool.sort(key=lambda d: stable_hash("loop13-seed:" + d["source"]))
    picked = pool[:n_docs]
    log.info("seed: %d/%d 文書を選択（probe %d 文書は除外済み）",
             len(picked), len(pool), len(probe_sources))
    return picked


# ---- --dry 用の合成QA（教師API代替・本文の逐語断片） --------------------------
def synth_dry_qa(seed_docs: list[dict], per_doc: int = 2, span: int = 60) -> list[dict]:
    """本文の逐語断片を q/a にする（dummy 3-gram 埋め込みでも gold が上位に来る設計）。"""
    rows = []
    for d in seed_docs:
        text = d["text"]
        for j in range(per_doc):
            start = 100 + j * 400
            if start + span > len(text):
                break
            rows.append({"q": text[start:start + span],
                         "a": text[start + 10:start + 30], "source": d["source"]})
    return rows


# ---- リーク検査（違反ゼロが must — evaluation.md の規律） ---------------------
def _norm_q(s: str) -> str:
    # norm_text は疑問符・感嘆符を残すため、質問照合では追加で除去（検査は厳しめが正）
    return norm_text(s).translate(str.maketrans("", "", "?？!！"))


def check_leak(qa_rows: list[dict], probe_qa_rows: list[dict],
               probe_sources: set[str]) -> dict:
    """評価資産（probe 199問・probe文書）との重複を検査。doc/質問の2レベル。"""
    doc_overlap = sorted({r["source"] for r in qa_rows} & probe_sources)
    probe_qs = {_norm_q(r["q"]) for r in probe_qa_rows}
    q_overlap = sorted({r["q"] for r in qa_rows if _norm_q(r["q"]) in probe_qs})
    return {"doc_overlap": doc_overlap, "q_overlap": q_overlap}


# ---- 学習例の構築（純関数） --------------------------------------------------
def _norm_for_containment(s: str) -> str:
    # norm_text は半角カンマを残すため、数値ゴールド（例: 1,485億円）の照合用に追加除去
    return norm_text(s).replace(",", "")


def answer_in_hits(answer: str, hits: list[dict], gold_source: str) -> bool:
    """答えの事実が gold 文書由来のチャンク内に実在するか（正規化包含）。"""
    ctx = "".join(h["text"] for h in hits if h["source"] == gold_source)
    na = _norm_for_containment(answer)
    return bool(na) and na in _norm_for_containment(ctx)


def split_of(q: str, valid_pct: int) -> str:
    return "valid" if stable_hash("loop13-split:" + q) % 100 < valid_pct else "train"


def build_examples(qa_rows: list[dict], hits_all: list[list[dict]],
                   noans_frac: float, valid_pct: int) -> tuple[list[dict], dict]:
    """QA+検索結果 → SFT例。返り値: (例のリスト, 統計)。

    3分岐: gold文書あり×答えがチャンク内 → 抽出例 / gold文書あり×答え無し → 破棄 /
    gold文書なし → 「記載なし」候補（answerable比 noans-frac/(1-noans-frac) まで）。
    """
    annotated = []
    n_ans = 0
    for qa, hits in zip(qa_rows, hits_all):
        gold_in_ctx = any(h["source"] == qa["source"] for h in hits)
        extractable = gold_in_ctx and answer_in_hits(qa["a"], hits, qa["source"])
        annotated.append((qa, hits, gold_in_ctx, extractable))
        n_ans += int(extractable)

    max_noans = int(n_ans * noans_frac / max(1.0 - noans_frac, 1e-9))
    examples = []
    stats = {"n_qa": len(qa_rows), "n_answerable": n_ans, "n_dropped_ans_not_in_ctx": 0,
             "n_noans_kept": 0, "n_noans_dropped": 0}
    for qa, hits, gold_in_ctx, extractable in annotated:
        if gold_in_ctx and not extractable:
            stats["n_dropped_ans_not_in_ctx"] += 1
            continue
        if not gold_in_ctx:
            if stats["n_noans_kept"] >= max_noans:
                stats["n_noans_dropped"] += 1
                continue
            stats["n_noans_kept"] += 1
        examples.append({"input": build_rag_user(qa["q"], hits),
                         "output": qa["a"] if extractable else NOANS_ANSWER,
                         "_split": split_of(qa["q"], valid_pct),
                         "_source": qa["source"], "_gold_in_ctx": int(gold_in_ctx)})
    stats["n_examples"] = len(examples)
    stats["n_train"] = sum(1 for e in examples if e["_split"] == "train")
    stats["n_valid"] = len(examples) - stats["n_train"]
    return examples, stats


def char_stats(lengths: list[int]) -> dict:
    """入力長の分布（seq長判断の材料 — ゲートAの煙試験前に p95/max を見る）。"""
    if not lengths:
        return {"p50": 0, "p95": 0, "max": 0}
    xs = sorted(lengths)
    return {"p50": xs[len(xs) // 2], "p95": xs[min(int(len(xs) * 0.95), len(xs) - 1)],
            "max": xs[-1]}


# ---- --build 本体 ------------------------------------------------------------
def run_build(qa_path: str, index_dir: str, out_dir: str, probe_qa_path: str,
              probe_corpus_path: str, k: int, noans_frac: float, valid_pct: int,
              embedder=None) -> dict:
    from embedder import get_embedder
    from retrieve import Retriever

    qa_rows = load_jsonl(qa_path)
    leak = check_leak(qa_rows, load_jsonl(probe_qa_path),
                      {d["source"] for d in load_jsonl(probe_corpus_path)})
    if leak["doc_overlap"] or leak["q_overlap"]:
        raise SystemExit(f"リーク検査違反 — 学習データ化を中止: {json.dumps(leak, ensure_ascii=False)}")
    log.info("リーク検査 pass: %d問（doc重複0・質問重複0）", len(qa_rows))

    retriever = Retriever(index_dir, embedder or get_embedder())
    hits_all = []
    for i in range(0, len(qa_rows), 32):
        hits_all += retriever.search([r["q"] for r in qa_rows[i:i + 32]], k=k)
    log.info("retrieval完了: %d問 × top%d (backend=%s)", len(qa_rows), k,
             retriever.meta["backend"])

    examples, stats = build_examples(qa_rows, hits_all, noans_frac, valid_pct)
    stats["rag_k"] = k
    stats["input_chars"] = char_stats([len(e["input"]) for e in examples])

    out = pathlib.Path(out_dir)
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    (out / "prompts" / "grounded_sys.txt").write_text(RAG_SYS, encoding="utf-8")
    writers = {s: (out / f"sft_grounded_{s}.jsonl").open("w", encoding="utf-8")
               for s in ("train", "valid")}
    with (out / "build_details.jsonl").open("w", encoding="utf-8") as det:
        for e in examples:
            writers[e["_split"]].write(json.dumps(
                {"input": e["input"], "output": e["output"]}, ensure_ascii=False) + "\n")
            det.write(json.dumps({"split": e["_split"], "source": e["_source"],
                                  "gold_in_ctx": e["_gold_in_ctx"],
                                  "output": e["output"]}, ensure_ascii=False) + "\n")
    for w in writers.values():
        w.close()
    (out / "build_report.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("report: %s", json.dumps(stats, ensure_ascii=False))
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="種文書サンプル（$0・CPU）")
    ap.add_argument("--build", action="store_true", help="QA→検索→SFTデータ化")
    ap.add_argument("--dry", action="store_true", help="$0自己完結の配管検証")
    ap.add_argument("--corpus", default="/data/pretrain/corpus_train.jsonl")
    ap.add_argument("--probe-corpus", default="/data/pretrain/corpus_probe.jsonl")
    ap.add_argument("--probe-qa", default="/data/pretrain/probe_qa.jsonl")
    ap.add_argument("--seed-out", default="/data/pretrain/grounded_seed.jsonl")
    ap.add_argument("--qa", default="/data/pretrain/grounded_qa.jsonl")
    ap.add_argument("--index", default="/data/retriever")
    ap.add_argument("--out", default="/data/pretrain/grounded")
    ap.add_argument("--n-docs", type=int, default=500)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--noans-frac", type=float, default=0.18)
    ap.add_argument("--valid-pct", type=int, default=5)
    a = ap.parse_args()

    if a.seed:
        docs = sample_seed(a.corpus, a.probe_corpus, a.n_docs)
        p = pathlib.Path(a.seed_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as w:
            for d in docs:
                w.write(json.dumps(d, ensure_ascii=False) + "\n")
        log.info("seed出力: %s (%d文書)", p, len(docs))
    elif a.build:
        run_build(a.qa, a.index, a.out, a.probe_qa, a.probe_corpus,
                  a.k, a.noans_frac, a.valid_pct)
    elif a.dry:
        from build_index import build
        from embedder import DummyEmbedder
        seed_docs = sample_seed(a.corpus, a.probe_corpus, n_docs=8)
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grounded_dry_"))
        seed_p, qa_p = tmp / "seed.jsonl", tmp / "qa.jsonl"
        for path, rows in ((seed_p, seed_docs), (qa_p, synth_dry_qa(seed_docs))):
            with path.open("w", encoding="utf-8") as w:
                for r in rows:
                    w.write(json.dumps(r, ensure_ascii=False) + "\n")
        emb = DummyEmbedder()
        build(str(seed_p), str(tmp / "index"), emb)
        stats = run_build(str(qa_p), str(tmp / "index"), a.out, a.probe_qa,
                          a.probe_corpus, a.k, a.noans_frac, a.valid_pct, embedder=emb)
        # 配管の生死判定: 抽出例が1件も作れなければ配管異常（逐語断片ならgold上位のはず）
        if stats["n_answerable"] == 0 or stats["n_examples"] == 0:
            raise SystemExit(f"dry失敗: 抽出例ゼロ — 配管異常 {stats}")
        log.info("dry pass: %s", a.out)
    else:
        log.error("--seed / --build / --dry のいずれかを指定")
        sys.exit(1)


if __name__ == "__main__":
    main()
