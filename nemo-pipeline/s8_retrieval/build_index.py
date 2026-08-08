"""loop12: インデックス構築 — corpus jsonl → chunks.jsonl + vectors.f32 + meta.json。

ベクトルDBは使わない（約20万チャンク×1024次元≈0.8GBはフラット行列の総当たりで十分・
可動部を増やさない）。vectors.f32 は float32 little-endian の生バイナリで、
numpy（np.fromfile）でも stdlib array でも読める。

埋め込みはバッチ逐次追記で、中断時は既埋め込み件数をスキップして再開する
（billing-gates.md「再課金ゼロの再開」の原則。chunks.jsonl はコーパスとパラメータから
決定的に再現されるため、既存ファイルがあればそのまま正として再利用する）。
"""
from __future__ import annotations

import argparse
import array
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from chunker import iter_chunks  # noqa: E402
from embedder import get_embedder  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[index] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def build(corpus: str, out_dir: str, embedder, size: int = 800, stride: int = 400,
          limit: int = 0, batch: int = 64) -> dict:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    chunks_p, vec_p, meta_p = out / "chunks.jsonl", out / "vectors.f32", out / "meta.json"

    if chunks_p.exists():
        chunks = [json.loads(line) for line in chunks_p.open(encoding="utf-8")]
        log.info("chunks.jsonl 再利用: %d chunks", len(chunks))
    else:
        chunks = list(iter_chunks(corpus, size, stride, limit))
        with chunks_p.open("w", encoding="utf-8") as w:
            for c in chunks:
                w.write(json.dumps(c, ensure_ascii=False) + "\n")
        log.info("チャンク化: %d chunks (%d文書, size=%d stride=%d)",
                 len(chunks), len({c["source"] for c in chunks}), size, stride)

    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    for key, val in (("size", size), ("stride", stride), ("backend", embedder.name),
                     ("model", getattr(embedder, "model", ""))):
        if meta and meta.get(key) != val:
            raise SystemExit(f"既存インデックスと不一致: {key}={meta.get(key)!r} vs {val!r}"
                             " — 別の --out を指定するか既存を削除して再構築")

    dim = meta.get("dim", 0)
    n_done = vec_p.stat().st_size // (4 * dim) if vec_p.exists() and dim else 0
    if n_done >= len(chunks):
        log.info("埋め込み済み %d/%d — スキップ", n_done, len(chunks))
        return meta
    if n_done:
        log.info("再開: %d/%d 埋め込み済み", n_done, len(chunks))

    with vec_p.open("ab") as w:
        for i in range(n_done, len(chunks), batch):
            vecs = embedder.encode([c["text"] for c in chunks[i:i + batch]],
                                   input_type="passage")
            if not dim:
                dim = len(vecs[0])
            for v in vecs:
                w.write(array.array("f", v).tobytes())
            w.flush()
            meta = {"size": size, "stride": stride, "backend": embedder.name,
                    "model": getattr(embedder, "model", ""), "dim": dim,
                    "n_chunks": len(chunks), "n_embedded": i + len(vecs)}
            meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                              encoding="utf-8")
            if (i // batch) % 50 == 0:
                log.info("%d/%d (%.1f%%)", i + len(vecs), len(chunks),
                         100.0 * (i + len(vecs)) / len(chunks))
    log.info("done: %s (n=%d dim=%d backend=%s)", out, meta["n_embedded"], dim,
             embedder.name)
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_", default="/data/pretrain/corpus_train.jsonl")
    ap.add_argument("--out", default="/data/retriever")
    ap.add_argument("--size", type=int, default=800)
    ap.add_argument("--stride", type=int, default=400)
    ap.add_argument("--limit", type=int, default=0, help="先頭N文書のみ（配管検証用）")
    ap.add_argument("--batch", type=int, default=64)
    a = ap.parse_args()
    build(a.in_, a.out, get_embedder(), a.size, a.stride, a.limit, a.batch)


if __name__ == "__main__":
    main()
