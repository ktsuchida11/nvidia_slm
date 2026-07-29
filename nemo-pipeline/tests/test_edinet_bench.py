"""fetch_edinet_bench の平文化・一意化のユニットテスト（loop10 P1）。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_edinet_bench.py）
外部依存なし（datasets 不在でも flatten_report の純関数部分を検査できる）。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s1_curation"))

from fetch_edinet_bench import _collect_strings, flatten_report  # noqa: E402


def _row(**over):
    base = {
        "meta": json.dumps({"会社名": "テスト株式会社", "EDINETコード": "E99999",
                            "証券コード": "12340", "ファンドコード": "－"}, ensure_ascii=False),
        "text": json.dumps({"沿革": {"FilingDate": "２【沿革】1948年設立。"},
                            "事業等のリスク": {"FilingDate": "為替変動リスクがある。"}},
                           ensure_ascii=False),
        "edinet_code": "E99999",
        "doc_id": "S100TEST",
    }
    base.update(over)
    return base


def test_flatten_plain_text_not_escaped_json():
    rec = flatten_report(_row())
    assert rec is not None
    # JSONエスケープが残っていない平文であること（旧 fetch_dataset の json.dumps フォールバック対策）
    assert '\\u' not in rec["text"] and '{"' not in rec["text"]
    assert "【沿革】" in rec["text"] and "1948年設立。" in rec["text"]
    assert "為替変動リスクがある。" in rec["text"]


def test_flatten_header_and_unique_source():
    rec = flatten_report(_row())
    assert rec["text"].startswith("会社名: テスト株式会社")
    assert "ファンドコード" not in rec["text"]          # "－" は見出しから除外
    assert rec["meta"]["source"] == "EDINET-Bench/E99999/S100TEST"
    assert rec["meta"]["license"] == "pdl-1.0"


def test_flatten_empty_body_returns_none():
    assert flatten_report(_row(text="{}")) is None
    assert flatten_report(_row(text="not-json")) is None


def test_collect_strings_nested():
    v = {"a": {"b": "x", "c": ["y", {"d": "z"}]}, "e": 1}
    assert _collect_strings(v) == ["x", "y", "z"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok {name}")
    print("all tests passed")
