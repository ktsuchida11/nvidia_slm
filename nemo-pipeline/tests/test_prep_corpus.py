"""loop10 P3: prep_corpus のリーク検査・決定的3分割のユニットテスト。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_prep_corpus.py）
外部依存なし。
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s3_pretraining"))

from prep_corpus import (SHINGLE, build_docs, find_leaks, heldout_shingles,  # noqa: E402
                         split_docs)


def _docs(n=10, chars=400):
    return [(f"src{i}", f"文書{i}の本文。" * (chars // 8)) for i in range(n)]


def test_build_docs_joins_by_source_and_filters():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "curated.jsonl"
        rows = [
            {"text": "A社の沿革。" * 50, "meta": {"source": "docA"}},
            {"text": "A社のリスク。" * 50, "meta": {"source": "docA"}},   # 同一sourceに結合
            {"text": "短い", "meta": {"source": "docB"}},                 # min_chars未満
            {"text": "x", "meta": {"source": "docC",
                                   "provenance": {"extraction": "skipped"}}},  # マーカー除外
        ]
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        docs, stats = build_docs(p, min_chars=200)
        assert [k for k, _ in docs] == ["docA"]
        assert "沿革" in docs[0][1] and "リスク" in docs[0][1]
        assert stats["markers"] == 1 and stats["short_docs"] == 1


def test_leak_detection_with_whitespace_difference():
    secret = "当社の粗利率は前年同期比で3.2ポイント改善し18.5%となった重要な非公開検証文"
    with tempfile.TemporaryDirectory() as td:
        hp = pathlib.Path(td) / "heldout.jsonl"
        hp.write_text(json.dumps({"input": f"質問: {secret}", "label": {"x": 1}},
                                 ensure_ascii=False), encoding="utf-8")
        shingles, files = heldout_shingles([str(hp)])
        assert files and shingles
        # 空白を挟んだ再掲でも検出できる（正規化比較）
        leaked_doc = ("前置き。" * 30) + secret[:SHINGLE * 2].replace("は", "は ") + ("後書き。" * 30)
        clean_doc = "全く関係のない別文書の本文。" * 30
        leaks = find_leaks([("bad", leaked_doc), ("ok", clean_doc)], shingles)
        assert leaks == {"bad"}


def test_heldout_missing_files_is_empty_not_error():
    shingles, files = heldout_shingles(["/nonexistent/heldout*.jsonl"])
    assert shingles == set() and files == []


def test_split_deterministic_and_disjoint():
    docs = _docs(20)
    t1, v1, p1 = split_docs(docs, n_probe=5, n_val=3)
    t2, v2, p2 = split_docs(list(reversed(docs)), n_probe=5, n_val=3)  # 入力順に依存しない
    assert (t1, v1, p1) == (t2, v2, p2)
    keys = [k for part in (t1, v1, p1) for k, _ in part]
    assert len(keys) == len(set(keys)) == 20
    assert len(p1) == 5 and len(v1) == 3 and len(t1) == 12


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok {name}")
    print("all tests passed")
