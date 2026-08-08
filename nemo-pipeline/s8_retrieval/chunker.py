"""loop12: コーパス文書のチャンク化 — 検索単位への分割（純関数・外部依存なし）。

800字・stride 400（50%オーバーラップ）が既定。末尾の取り残しは独立窓で必ずカバーする。
chunk_id = "<source>#<連番>" とし、gold doc_id（source）への逆引きを常に保持する
（recall@k は文書単位で採点するため）。
"""
from __future__ import annotations

import json
import pathlib
from typing import Iterator


def chunk_text(text: str, size: int = 800, stride: int = 400) -> list[str]:
    """先頭から stride 刻みで size 字の窓を切る。末尾が欠ける場合は末尾窓を追加。"""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    starts = list(range(0, len(text) - size + 1, stride))
    if starts[-1] + size < len(text):
        starts.append(len(text) - size)
    return [text[s:s + size] for s in starts]


def iter_chunks(corpus_path: str | pathlib.Path, size: int = 800, stride: int = 400,
                limit: int = 0) -> Iterator[dict]:
    """corpus jsonl({"text","source"}) → {"chunk_id","source","text"} を逐次生成。"""
    n_docs = 0
    for line in pathlib.Path(corpus_path).open(encoding="utf-8"):
        if not line.strip():
            continue
        d = json.loads(line)
        for i, c in enumerate(chunk_text(d["text"], size, stride)):
            yield {"chunk_id": f"{d['source']}#{i:04d}", "source": d["source"], "text": c}
        n_docs += 1
        if limit and n_docs >= limit:
            return
