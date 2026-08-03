"""loop10 P2 のユニットテスト: minhash高速化のパリティ + curator_stage の純関数。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_curator_stage.py）
外部依存なし（nemo-curator / numpy 不在でも検査できる。numpy があればベクトル化経路も同時検証）。
"""
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s1_curation"))

import s1_curate  # noqa: E402
from curator_stage import fold_meta, parse_lang, unfold_meta  # noqa: E402


# ---- minhash 高速化（ユニバーサルハッシュ置換）の挙動検証 ----------------------
BASE = ("当社の当期の売上高は前期比12%増の1,485億円となった。主力の塗料事業が国内外で好調に推移し、"
        "原材料価格の上昇を価格転嫁で吸収した。営業利益は98億円で過去最高を更新した。") * 10


def test_minhash_detects_near_duplicate():
    near = BASE.replace("塗料事業", "化成品事業")     # 一部だけ違う近似重複
    a, b = s1_curate.minhash(BASE), s1_curate.minhash(near)
    sim = sum(x == y for x, y in zip(a, b)) / s1_curate.NUM_PERM
    assert sim >= s1_curate.FUZZY_THRESHOLD, f"近似重複を捕捉できない (推定J={sim})"


def test_minhash_separates_independent_docs():
    other = ("金の先物相場は週明けに反落した。米長期金利の上昇を受けて金利を生まない資産である"
             "金への投資妙味が薄れ、売りが先行した。") * 10
    a, b = s1_curate.minhash(BASE), s1_curate.minhash(other)
    sim = sum(x == y for x, y in zip(a, b)) / s1_curate.NUM_PERM
    assert sim < 0.3, f"独立文書が近すぎる (推定J={sim})"


def test_minhash_deterministic_and_shape():
    a = s1_curate.minhash(BASE)
    assert a == s1_curate.minhash(BASE)
    assert len(a) == s1_curate.NUM_PERM
    assert all(isinstance(x, int) and 0 <= x < (1 << 64) for x in a)


def test_fuzzy_dedup_end_to_end():
    def doc(text, q):
        return {"text": text, "meta": {"quality": q}, "_sig": s1_curate.minhash(text)}
    near = BASE.replace("12%", "13%")
    kept, dropped = s1_curate.fuzzy_dedup(
        [doc(BASE, 1.0), doc(near, 0.5), doc("全く別の話題の文書。" * 50, 0.9)])
    assert dropped == 1 and len(kept) == 2
    assert kept[0]["meta"]["quality"] == 1.0          # 品質降順で高い方が残る


# ---- curator_stage 純関数 ----------------------------------------------------
def test_parse_lang_variants():
    assert parse_lang([0.98, "__label__ja"]) == ("JA", 0.98)
    assert parse_lang(["__label__en", 0.7]) == ("EN", 0.7)
    assert parse_lang("__label__ja") == ("JA", None)
    assert parse_lang({"lang": "JA", "score": 0.9}) == ("JA", 0.9)
    assert parse_lang(None) == (None, None)
    # pyarrow/JsonlWriter 経由で文字列化されたリスト（実機で発生し得る形式ゆれ）
    assert parse_lang('[0.99, "__label__ja"]') == ("JA", 0.99)
    assert parse_lang("['0.99', '__label__ja']") == ("JA", 0.99)
    assert parse_lang("[np.float64(0.99), '__label__ja']")[0] == "JA"


def test_fold_unfold_meta_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        src, work, out, dst = td / "src", td / "work", td / "curator_out", td / "dst"
        src.mkdir()
        rec = {"text": "日本語の文書です。", "meta": {"license": "pdl-1.0", "source": "x#1"}}
        (src / "a.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        assert fold_meta(src, work) == 1
        folded = json.loads((work / "a.jsonl").read_text(encoding="utf-8"))
        assert json.loads(folded["meta_json"])["license"] == "pdl-1.0"
        # Curator出力を模す: language スコアが付与され、非日本語の行は落ちる想定
        out.mkdir()
        folded["language"] = [0.99, "__label__ja"]
        en = {"text": "english doc", "meta_json": "{}", "language": [0.9, "__label__en"]}
        (out / "part0.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in (folded, en)) + "\n",
            encoding="utf-8")
        kept, dropped_lang = unfold_meta(out, dst)
        assert (kept, dropped_lang) == (1, 1)
        restored = json.loads((dst / "curator_passed.jsonl").read_text(encoding="utf-8"))
        assert restored["meta"]["source"] == "x#1" and restored["meta"]["lang"] == "JA"
        assert restored["meta"]["lang_score"] == 0.99


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok {name}")
    print("all tests passed")
