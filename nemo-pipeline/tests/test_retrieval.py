"""loop12: s8_retrieval のユニットテスト（chunker / dummy埋め込み / index構築+検索 / recall集計 / RAGプロンプト）。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_retrieval.py）
外部依存なし（numpy無し環境では retrieve.py の stdlib フォールバック経路を検証する）。
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s8_retrieval"))
sys.path.insert(0, str(ROOT / "s6_evaluation"))
sys.path.insert(0, str(ROOT / "common"))

from build_index import build  # noqa: E402
from chunker import chunk_text, iter_chunks  # noqa: E402
from embedder import DummyEmbedder, l2_normalize  # noqa: E402
from probe_qa import build_rag_user  # noqa: E402
from recall_eval import compute_metrics, doc_ranking  # noqa: E402
from retrieve import Retriever  # noqa: E402

DOCS = [
    {"text": "旭化成ホールディングスの当期売上高は1,485億円であり、主力は塗料事業である。"
             "本社は名古屋市に所在し、従業員数は1,234名。" * 20,
     "source": "EDINET-Bench/TEST1/DOC1"},
    {"text": "北海道電力の発電事業に関する有価証券報告書。水力発電所の設備投資額は320億円。"
             "苫小牧の火力発電所は2025年に稼働を開始した。" * 20,
     "source": "EDINET-Bench/TEST2/DOC2"},
    {"text": "サイバーセキュリティ関連のソフトウェア企業。クラウド事業の売上構成比は45.6%。"
             "研究開発費は前年比12%増加し、拠点は福岡県。" * 20,
     "source": "EDINET-Bench/TEST3/DOC3"},
]


def test_chunk_short_doc_single():
    assert chunk_text("短い文書。") == ["短い文書。"]
    assert chunk_text("") == []
    assert chunk_text("あ" * 800) == ["あ" * 800]


def test_chunk_boundaries_and_tail():
    text = "あ" * 2100
    chunks = chunk_text(text, size=800, stride=400)
    assert [len(c) for c in chunks] == [800] * 5
    assert chunks[0] == text[0:800]
    assert chunks[-1] == text[1300:2100]      # 末尾窓が取り残しをカバー
    covered = set()
    for i in range(len(chunks) - 1):
        covered.update(range(i * 400, i * 400 + 800))
    covered.update(range(1300, 2100))
    assert covered == set(range(2100))        # 全文字がいずれかの窓に含まれる


def test_iter_chunks_source_tracking():
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "corpus.jsonl"
        with p.open("w", encoding="utf-8") as w:
            for d in DOCS:
                w.write(json.dumps(d, ensure_ascii=False) + "\n")
        chunks = list(iter_chunks(p, size=800, stride=400))
        assert {c["source"] for c in chunks} == {d["source"] for d in DOCS}
        assert chunks[0]["chunk_id"] == "EDINET-Bench/TEST1/DOC1#0000"
        assert len(list(iter_chunks(p, limit=1))) < len(chunks)


def test_dummy_embedder_deterministic_normalized():
    e = DummyEmbedder()
    v1, v2 = e.encode(["旭化成の売上高", "旭化成の売上高"])
    assert v1 == v2
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-6
    # 表層が近いテキストは遠いテキストより類似
    a, b, c = e.encode(["旭化成の売上高は1,485億円", "旭化成の売上高について", "北海道の水力発電所"])
    sim_ab = sum(x * y for x, y in zip(a, b))
    sim_ac = sum(x * y for x, y in zip(a, c))
    assert sim_ab > sim_ac


def test_l2_normalize_zero_safe():
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]


def _build_test_index(td: str) -> pathlib.Path:
    corpus = pathlib.Path(td) / "corpus.jsonl"
    with corpus.open("w", encoding="utf-8") as w:
        for d in DOCS:
            w.write(json.dumps(d, ensure_ascii=False) + "\n")
    out = pathlib.Path(td) / "index"
    build(str(corpus), str(out), DummyEmbedder(), size=800, stride=400, batch=16)
    return out


def test_build_and_search_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        out = _build_test_index(td)
        meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
        assert meta["n_embedded"] == meta["n_chunks"]
        assert (out / "vectors.f32").stat().st_size == meta["n_embedded"] * meta["dim"] * 4
        r = Retriever(out, DummyEmbedder())
        hits = r.search(["北海道電力の水力発電所の設備投資額はいくらか"], k=3)[0]
        assert hits[0]["source"] == "EDINET-Bench/TEST2/DOC2"
        assert hits[0]["score"] >= hits[-1]["score"]


def test_build_resume_is_noop():
    with tempfile.TemporaryDirectory() as td:
        out = _build_test_index(td)
        size_before = (out / "vectors.f32").stat().st_size
        build(str(pathlib.Path(td) / "corpus.jsonl"), str(out), DummyEmbedder(),
              size=800, stride=400, batch=16)   # 再実行=埋め込み済みスキップ
        assert (out / "vectors.f32").stat().st_size == size_before


def test_doc_ranking_and_metrics():
    hits = [{"source": "A"}, {"source": "A"}, {"source": "B"}, {"source": "C"}]
    assert doc_ranking(hits) == ["A", "B", "C"]
    m = compute_metrics([["A", "B"], ["X", "G"], ["Y"]], ["A", "G", "G"], [1, 5])
    assert m["recall@1"] == round(1 / 3, 4)
    assert m["recall@5"] == round(2 / 3, 4)
    assert m["mrr"] == round((1.0 + 0.5 + 0.0) / 3, 4)


def test_rag_user_prompt():
    hits = [{"source": "EDINET-Bench/TEST1/DOC1", "text": "売上高は1,485億円。"}]
    msg = build_rag_user("旭化成の売上高は？", hits)
    assert "参考資料1" in msg and "EDINET-Bench/TEST1/DOC1" in msg
    assert msg.endswith("質問: 旭化成の売上高は？")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
