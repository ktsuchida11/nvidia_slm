"""loop14: rerank バックエンド — nim(本線) / dummy(dry・テスト)。

埋め込み検索の広い候補（k0=50-100）を query×passage の交差採点で並べ直し、
プロンプトに入れる top-k を精密化する。呼び出しは probe_qa.py --rerank から。

環境変数で選択（Makefile から -e で注入）:
  RERANK_BACKEND  nim | dummy（既定 nim）
  RERANK_BASE_URL nim エンドポイント（既定 http://host.docker.internal:8003/v1 = make serve-rerank）
  RERANK_MODEL    既定 nvidia/llama-3.2-nv-rerankqa-1b-v2

NIM の /v1/ranking は OpenAI 互換ではない独自スキーマのため openai クライアントを使わず
stdlib urllib で叩く（依存追加なし）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request

log = logging.getLogger(__name__)

NIM_DEFAULT_URL = "http://host.docker.internal:8003/v1"
NIM_DEFAULT_MODEL = "nvidia/llama-3.2-nv-rerankqa-1b-v2"


def build_ranking_payload(model: str, query: str, hits: list[dict]) -> dict:
    """NIM /v1/ranking のリクエスト体（純関数・テスト対象）。"""
    return {"model": model,
            "query": {"text": query},
            "passages": [{"text": h["text"]} for h in hits],
            "truncate": "END"}


def apply_ranking(hits: list[dict], order: list[tuple[int, float]],
                  top_k: int) -> list[dict]:
    """rankings（(元index, score) 降順）を hits に適用して top_k 件を返す（純関数）。"""
    out = []
    for idx, score in order[:top_k]:
        out.append({**hits[idx], "rerank_score": round(float(score), 4)})
    return out


class DummyReranker:
    """決定的な文字3-gram重なり率で採点（dry・テスト専用）。同点は元の検索順を維持。"""
    name = "dummy"

    def rerank(self, query: str, hits: list[dict], top_k: int) -> list[dict]:
        q = re.sub(r"\s+", "", query)
        grams = {q[i:i + 3] for i in range(len(q) - 2)}
        scores = []
        for i, h in enumerate(hits):
            t = re.sub(r"\s+", "", h["text"])
            hit = sum(1 for g in grams if g in t)
            scores.append((i, hit / max(len(grams), 1)))
        order = sorted(scores, key=lambda t: (-t[1], t[0]))
        return apply_ranking(hits, order, top_k)


class NimReranker:
    """NeMo Retriever text reranking NIM（POST {base}/ranking → rankings[{index,logit}]）。"""

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.name, self.model = "nim", model

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/ranking", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {os.environ.get('NGC_API_KEY', 'dummy')}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))

    def rerank(self, query: str, hits: list[dict], top_k: int) -> list[dict]:
        payload = build_ranking_payload(self.model, query, hits)
        for attempt in range(4):
            try:
                body = self._post(payload)
                order = [(r["index"], r["logit"]) for r in body["rankings"]]
                return apply_ranking(hits, order, top_k)
            except Exception as e:
                if attempt == 3:
                    raise
                log.warning("rerank失敗(%s) — %ds後にリトライ", e, 2 ** attempt)
                time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")


def get_reranker():
    backend = os.environ.get("RERANK_BACKEND", "nim").strip()
    if backend == "dummy":
        return DummyReranker()
    if backend == "nim":
        return NimReranker(os.environ.get("RERANK_BASE_URL", NIM_DEFAULT_URL),
                           os.environ.get("RERANK_MODEL", NIM_DEFAULT_MODEL))
    raise SystemExit(f"未知の RERANK_BACKEND: {backend}（nim | dummy）")
