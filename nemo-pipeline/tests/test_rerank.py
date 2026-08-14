"""loop14: reranker のユニットテスト（dummy採点 / ペイロード構築 / 適用 / probe_qa統合dry）。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_rerank.py）
外部依存なし（dummy埋め込み・dummy rerank・生成はgoldモック）。
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s8_retrieval"))
sys.path.insert(0, str(ROOT / "s6_evaluation"))
sys.path.insert(0, str(ROOT / "common"))

from build_index import build  # noqa: E402
from embedder import DummyEmbedder  # noqa: E402
from reranker import (  # noqa: E402
    DummyReranker, NimReranker, apply_ranking, build_ranking_payload, get_reranker,
)

HITS = [
    {"chunk_id": "c0", "source": "D0", "text": "全く関係のない話題。天気は晴れです。"},
    {"chunk_id": "c1", "source": "D1", "text": "当社の売上高は1,485億円でした。"},
    {"chunk_id": "c2", "source": "D2", "text": "売上高について: 当期の売上高は1,485億円。"},
]


def test_dummy_rerank_orders_by_overlap():
    rr = DummyReranker()
    out = rr.rerank("当期の売上高はいくらですか", HITS, top_k=3)
    # 質問との3-gram重なりが最大のc2が先頭・無関係なc0が最下位
    assert out[0]["chunk_id"] == "c2"
    assert out[-1]["chunk_id"] == "c0"
    assert all("rerank_score" in h for h in out)


def test_dummy_rerank_topk_and_deterministic():
    rr = DummyReranker()
    a = rr.rerank("売上高", HITS, top_k=2)
    b = rr.rerank("売上高", HITS, top_k=2)
    assert len(a) == 2
    assert [h["chunk_id"] for h in a] == [h["chunk_id"] for h in b]


def test_dummy_rerank_tie_keeps_search_order():
    rr = DummyReranker()
    # どのチャンクとも重ならない質問 → 全て同点0 → 元の検索順を維持
    out = rr.rerank("zzzzzz", HITS, top_k=3)
    assert [h["chunk_id"] for h in out] == ["c0", "c1", "c2"]


def test_build_ranking_payload():
    p = build_ranking_payload("nvidia/llama-3.2-nv-rerankqa-1b-v2", "質問文", HITS)
    assert p["query"] == {"text": "質問文"}
    assert [x["text"] for x in p["passages"]] == [h["text"] for h in HITS]
    assert p["truncate"] == "END"
    json.dumps(p)  # シリアライズ可能であること


def test_apply_ranking_reorders_and_truncates():
    order = [(2, 9.5), (0, 1.25), (1, -3.0)]
    out = apply_ranking(HITS, order, top_k=2)
    assert [h["chunk_id"] for h in out] == ["c2", "c0"]
    assert out[0]["rerank_score"] == 9.5
    assert "source" in out[0]  # 元のヒット属性を保持


def test_get_reranker_backends():
    os.environ["RERANK_BACKEND"] = "dummy"
    assert isinstance(get_reranker(), DummyReranker)
    os.environ["RERANK_BACKEND"] = "nim"
    rr = get_reranker()
    assert isinstance(rr, NimReranker) and rr.base_url.endswith("/v1")
    os.environ["RERANK_BACKEND"] = "dummy"


def test_run_eval_dry_with_rerank():
    """probe_qa統合: dummy索引→k0検索→dummy rerank→goldモック採点の一気通貫。"""
    from probe_qa import run_eval
    os.environ["EMBED_BACKEND"] = "dummy"
    os.environ["RERANK_BACKEND"] = "dummy"
    docs = [{"text": f"文書{i}の有報。売上高は{i}00億円で本社は都市{i}にある。" * 30,
             "source": f"D{i}"} for i in range(6)]
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        with (td / "corpus.jsonl").open("w", encoding="utf-8") as w:
            for d in docs:
                w.write(json.dumps(d, ensure_ascii=False) + "\n")
        build(str(td / "corpus.jsonl"), str(td / "index"), DummyEmbedder())
        with (td / "qa.jsonl").open("w", encoding="utf-8") as w:
            w.write(json.dumps({"q": "文書3の有報の売上高は？ 本社は都市3", "a": "300億円",
                                "source": "D3"}, ensure_ascii=False) + "\n")
        run_eval(str(td / "qa.jsonl"), "t_rr", str(td / "out"), "", {}, 100,
                 rag_index=str(td / "index"), rag_k=2, rerank_k0=5, dry_run=True)
        rep = json.loads((td / "out" / "eval_probe_t_rr.json").read_text(encoding="utf-8"))
        assert rep["probe_acc"] == 1.0          # goldモックなので常に正解
        assert rep["rag_k"] == 2 and rep["rerank_k0"] == 5
        assert rep["rerank_backend"] == "dummy"
        assert 0.0 <= rep["gold_in_ctx_rate"] <= rep["gold_in_k0_rate"] <= 1.0
        det = [json.loads(l) for l in
               (td / "out" / "eval_probe_t_rr_details.jsonl").open(encoding="utf-8")]
        assert len(det[0]["ctx_sources"]) == 2  # rerank後はrag_k件に絞られている
        assert "gold_in_k0" in det[0]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
