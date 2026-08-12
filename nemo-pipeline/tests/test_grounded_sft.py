"""loop13: build_grounded_sft のユニットテスト（seed / リーク検査 / 例構築 / dry一気通貫）。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_grounded_sft.py）
外部依存なし（dummy埋め込み・stdlib経路）。
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s8_retrieval"))
sys.path.insert(0, str(ROOT / "s6_evaluation"))
sys.path.insert(0, str(ROOT / "common"))

from build_grounded_sft import (  # noqa: E402
    NOANS_ANSWER, answer_in_hits, build_examples, char_stats, check_leak,
    run_build, sample_seed, split_of, synth_dry_qa,
)
from build_index import build  # noqa: E402
from embedder import DummyEmbedder  # noqa: E402
from probe_qa import build_rag_user  # noqa: E402

DOCS = [
    {"text": f"文書{i}の有価証券報告書。売上高は{i}00億円で本社は都市{i}にある。" * 30,
     "source": f"EDINET-Bench/T{i}/DOC{i}"}
    for i in range(10)
]


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as w:
        for r in rows:
            w.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_sample_seed_excludes_probe_and_deterministic():
    with tempfile.TemporaryDirectory() as td:
        corpus, probe = f"{td}/c.jsonl", f"{td}/p.jsonl"
        _write_jsonl(corpus, DOCS)
        _write_jsonl(probe, DOCS[:3])          # 先頭3文書をprobe扱い
        picked = sample_seed(corpus, probe, n_docs=5)
        assert len(picked) == 5
        probe_sources = {d["source"] for d in DOCS[:3]}
        assert not {d["source"] for d in picked} & probe_sources
        assert picked == sample_seed(corpus, probe, n_docs=5)  # 決定的


def test_check_leak_doc_and_normalized_question():
    qa = [{"q": "旭化成の売上高は？", "a": "100億円", "source": "S1"},
          {"q": "北電の 発電量は？？", "a": "5GWh", "source": "S2"}]
    probe_qa = [{"q": "北電の発電量は?", "a": "5GWh", "source": "P1"}]  # 空白・記号ゆれ
    leak = check_leak(qa, probe_qa, probe_sources={"S2"})
    assert leak["doc_overlap"] == ["S2"]
    assert leak["q_overlap"] == ["北電の 発電量は？？"]     # 正規化一致で検出
    clean = check_leak(qa[:1], probe_qa, probe_sources={"P1"})
    assert not clean["doc_overlap"] and not clean["q_overlap"]


def test_answer_in_hits_normalizes_commas():
    hits = [{"source": "G", "text": "当期の売上高は1,485億円であった。"},
            {"source": "X", "text": "無関係の文書。"}]
    assert answer_in_hits("1,485億円", hits, "G")
    assert answer_in_hits("1485億円", hits, "G")   # カンマゆれを吸収
    assert not answer_in_hits("999億円", hits, "G")
    assert not answer_in_hits("無関係", hits, "G")  # gold以外のチャンクは照合対象外


def test_answer_in_hits_table_composed():
    """有報の表: セル値+ヘッダ単位の合成答（P2実測52問中21問）を数値トークン照合で残す。"""
    hits = [{"source": "G", "text": "5【従業員の状況】(1)連結会社の状況 従業員数(人)867(1,123)"
                                    "(注)1.従業員数は就業人員であります。平均勤続年数20.3年"}]
    assert answer_in_hits("867名", hits, "G")        # 単位はヘッダ由来 → 数値のみ照合
    assert answer_in_hits("20.3年", hits, "G")       # 小数
    assert not answer_in_hits("868名", hits, "G")    # 値そのものが無ければ破棄
    # 複数数値の答えは全数値の実在が必要
    hits2 = [{"source": "G", "text": "子会社の数173社 関連会社28社"}]
    assert answer_in_hits("子会社173社、関連会社28社", hits2, "G")
    assert not answer_in_hits("子会社173社、関連会社29社", hits2, "G")
    # 非数値の答えは従来どおり逐語包含
    assert not answer_in_hits("名古屋市", hits, "G")


def test_build_examples_three_branches_and_noans_cap():
    gold_hit = [{"source": "G", "text": "売上高は1,485億円。"}]
    miss_hit = [{"source": "X", "text": "別文書。"}]
    qa_rows = ([{"q": f"q{i}", "a": "1,485億円", "source": "G"} for i in range(8)]  # 抽出例
               + [{"q": "qd", "a": "本文に無い事実", "source": "G"}]                 # 破棄
               + [{"q": f"qn{i}", "a": "999億円", "source": "Z"} for i in range(5)])  # 検索ミス
    hits_all = [gold_hit] * 9 + [miss_hit] * 5
    ex, stats = build_examples(qa_rows, hits_all, noans_frac=0.2, valid_pct=0)
    assert stats["n_answerable"] == 8
    assert stats["n_dropped_ans_not_in_ctx"] == 1
    # cap = 8 * 0.2/0.8 = 2件まで
    assert stats["n_noans_kept"] == 2 and stats["n_noans_dropped"] == 3
    assert stats["n_examples"] == 10 and stats["n_valid"] == 0
    outs = [e["output"] for e in ex]
    assert outs.count(NOANS_ANSWER) == 2 and outs.count("1,485億円") == 8
    # プロンプトは評価時（probe_qa --rag）と同一描画
    assert ex[0]["input"] == build_rag_user("q0", gold_hit)


def test_split_deterministic_and_char_stats():
    assert all(split_of(f"q{i}", 5) == split_of(f"q{i}", 5) for i in range(50))
    splits = [split_of(f"question-{i}", 50) for i in range(200)]
    assert 0 < splits.count("valid") < 200      # 両側に分かれる
    assert char_stats([1, 2, 3, 100]) == {"p50": 3, "p95": 100, "max": 100}
    assert char_stats([]) == {"p50": 0, "p95": 0, "max": 0}


def test_dry_pipeline_end_to_end():
    """seed→合成QA→dummy索引→run_build（リーク検査込み）を temp 上で一気通貫。"""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        _write_jsonl(td / "corpus.jsonl", DOCS)
        _write_jsonl(td / "probe_corpus.jsonl", DOCS[:2])
        _write_jsonl(td / "probe_qa.jsonl",
                     [{"q": "probe専用の質問", "a": "x", "source": DOCS[0]["source"]}])
        seed = sample_seed(td / "corpus.jsonl", td / "probe_corpus.jsonl", n_docs=6)
        qa = synth_dry_qa(seed, per_doc=2)
        assert qa and all(r["source"] not in {d["source"] for d in DOCS[:2]} for r in qa)
        _write_jsonl(td / "seed.jsonl", seed)
        _write_jsonl(td / "qa.jsonl", qa)
        emb = DummyEmbedder()
        build(str(td / "seed.jsonl"), str(td / "index"), emb)
        stats = run_build(str(td / "qa.jsonl"), str(td / "index"), str(td / "out"),
                          str(td / "probe_qa.jsonl"), str(td / "probe_corpus.jsonl"),
                          k=3, noans_frac=0.18, valid_pct=5, embedder=emb)
        assert stats["n_answerable"] > 0        # 逐語断片ならgoldが上位に来るはず
        out = td / "out"
        assert (out / "prompts" / "grounded_sys.txt").exists()
        rows = [json.loads(l) for l in (out / "sft_grounded_train.jsonl").open()]
        assert rows and set(rows[0]) == {"input", "output"}
        report = json.loads((out / "build_report.json").read_text())
        assert report["input_chars"]["max"] > 0


def test_run_build_dies_on_leak():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        _write_jsonl(td / "corpus.jsonl", DOCS)
        _write_jsonl(td / "probe_corpus.jsonl", DOCS[:2])
        _write_jsonl(td / "probe_qa.jsonl", [{"q": "x", "a": "y", "source": "P"}])
        # probe文書由来のQAを混入 → 即死すること
        _write_jsonl(td / "qa.jsonl",
                     [{"q": "混入質問", "a": "z", "source": DOCS[0]["source"]}])
        emb = DummyEmbedder()
        build(str(td / "corpus.jsonl"), str(td / "index"), emb)
        try:
            run_build(str(td / "qa.jsonl"), str(td / "index"), str(td / "out"),
                      str(td / "probe_qa.jsonl"), str(td / "probe_corpus.jsonl"),
                      k=3, noans_frac=0.18, valid_pct=5, embedder=emb)
            raise AssertionError("リーク検査をすり抜けた")
        except SystemExit as e:
            assert "リーク検査違反" in str(e)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
