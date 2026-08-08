"""loop12: フラットインデックス検索 — 正規化済みベクトルの内積 top-k。

numpy があれば行列積（全コーパス20万チャンクでも1クエリ数十ms）、無ければ
stdlib の純Pythonループにフォールバック（dry・テストの小規模インデックス用）。
"""
from __future__ import annotations

import array
import heapq
import json
import pathlib

try:
    import numpy as np
except ImportError:  # テスト・dry の小規模経路
    np = None


class Retriever:
    def __init__(self, index_dir: str | pathlib.Path, embedder):
        p = pathlib.Path(index_dir)
        self.meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        self.chunks = [json.loads(line)
                       for line in (p / "chunks.jsonl").open(encoding="utf-8")]
        if self.meta.get("n_embedded") != len(self.chunks):
            raise SystemExit(f"インデックス未完了: {self.meta.get('n_embedded')}/"
                             f"{len(self.chunks)} — make retriever-index で再開")
        self.dim = self.meta["dim"]
        raw = (p / "vectors.f32").read_bytes()
        if np is not None:
            self.mat = np.frombuffer(raw, dtype=np.float32).reshape(-1, self.dim)
        else:
            self.vecs = array.array("f")
            self.vecs.frombytes(raw)
        self.embedder = embedder

    def _topk(self, qvec: list[float], k: int) -> list[tuple[float, int]]:
        if np is not None:
            scores = self.mat @ np.asarray(qvec, dtype=np.float32)
            idx = np.argsort(-scores)[:k]
            return [(float(scores[i]), int(i)) for i in idx]
        scores = []
        for row in range(len(self.chunks)):
            base = row * self.dim
            scores.append(sum(self.vecs[base + j] * qvec[j] for j in range(self.dim)))
        top = heapq.nlargest(k, enumerate(scores), key=lambda t: t[1])
        return [(s, i) for i, s in top]

    def search(self, queries: list[str], k: int = 5) -> list[list[dict]]:
        """クエリごとに上位kチャンク（{chunk_id, source, text, score}）を返す。"""
        qvecs = self.embedder.encode(queries, input_type="query")
        results = []
        for qv in qvecs:
            hits = [{**self.chunks[i], "score": round(s, 4)}
                    for s, i in self._topk(qv, k)]
            results.append(hits)
        return results
