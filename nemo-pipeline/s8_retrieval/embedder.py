"""loop12: 埋め込みバックエンド — nim(本線) / hf(フォールバック) / dummy(dry・テスト)。

環境変数で選択（Makefile から -e で注入）:
  EMBED_BACKEND  nim | hf | dummy（既定 nim）
  EMBED_BASE_URL nim エンドポイント（既定 http://host.docker.internal:8001/v1 = make serve-embed）
  EMBED_MODEL    nim: nvidia/llama-3.2-nv-embedqa-1b-v2 / hf: intfloat/multilingual-e5-large

いずれも非対称検索モデルの流儀に従い input_type("query"/"passage") を区別する
（NIM は extra_body、e5 は "query: "/"passage: " プレフィックス）。
encode() の返り値は L2 正規化済み list[list[float]]（内積=コサイン類似度）。
"""
from __future__ import annotations

import logging
import math
import os
import re
import time
import zlib

log = logging.getLogger(__name__)

NIM_DEFAULT_URL = "http://host.docker.internal:8001/v1"
NIM_DEFAULT_MODEL = "nvidia/llama-3.2-nv-embedqa-1b-v2"
HF_DEFAULT_MODEL = "intfloat/multilingual-e5-large"


def l2_normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


class DummyEmbedder:
    """決定的な文字3-gramハッシュ埋め込み。表層一致の粗い類似のみ（dry・テスト専用）。"""
    name = "dummy"
    dim = 256

    def encode(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            s = re.sub(r"\s+", "", t)
            for i in range(len(s) - 2):
                v[zlib.crc32(s[i:i + 3].encode("utf-8")) % self.dim] += 1.0
            out.append(l2_normalize(v))
        return out


class NimEmbedder:
    """NeMo Retriever text embedding NIM（OpenAI互換 /v1/embeddings + input_type拡張）。"""

    def __init__(self, base_url: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url,
                             api_key=os.environ.get("NGC_API_KEY", "dummy"))
        self.name, self.model = "nim", model

    def encode(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        for attempt in range(4):
            try:
                r = self.client.embeddings.create(
                    model=self.model, input=texts,
                    extra_body={"input_type": input_type, "truncate": "END"})
                vecs = [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
                return [l2_normalize(v) for v in vecs]
            except Exception as e:
                if attempt == 3:
                    raise
                log.warning("embedding失敗(%s) — %ds後にリトライ", e, 2 ** attempt)
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")


class HfEmbedder:
    """sentence-transformers 直ロード（NIM不可時のフォールバック。e5系はプレフィックス必須）。"""

    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer
        self.st = SentenceTransformer(model)
        self.name, self.model = "hf", model
        self._prefix = model.startswith("intfloat/")  # e5系の流儀

    def encode(self, texts: list[str], input_type: str = "passage") -> list[list[float]]:
        if self._prefix:
            texts = [f"{'query' if input_type == 'query' else 'passage'}: {t}" for t in texts]
        return self.st.encode(texts, normalize_embeddings=True).tolist()


def get_embedder():
    backend = os.environ.get("EMBED_BACKEND", "nim").strip()
    if backend == "dummy":
        return DummyEmbedder()
    if backend == "hf":
        return HfEmbedder(os.environ.get("EMBED_MODEL", HF_DEFAULT_MODEL))
    if backend == "nim":
        return NimEmbedder(os.environ.get("EMBED_BASE_URL", NIM_DEFAULT_URL),
                           os.environ.get("EMBED_MODEL", NIM_DEFAULT_MODEL))
    raise SystemExit(f"未知の EMBED_BACKEND: {backend}（nim | hf | dummy）")
