"""loop10 P5: probe_qa 採点関数のユニットテスト。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_probe_qa.py）
外部依存なし。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "s6_evaluation"))
sys.path.insert(0, str(ROOT / "common"))

from probe_qa import norm_text, score_answer  # noqa: E402


def test_numeric_exact_and_tolerance():
    assert score_answer("売上高は1,485億円でした", "1,485億円") == 1
    assert score_answer("約148,600,000,000円", "1,486億円") == 1     # 相対誤差1%以内
    assert score_answer("1,600億円", "1,485億円") == 0
    assert score_answer("回答できません", "1,485億円") == 0


def test_numeric_unit_scale():
    assert score_answer("12.3%", "12.3%") == 1
    assert score_answer("従業員は 1,234名", "1,234名") == 1


def test_string_containment_normalized():
    assert score_answer("本社は名古屋市にあります。", "名古屋市") == 1
    assert score_answer("塗料 事業", "塗料事業") == 1               # 空白差を吸収
    assert score_answer("大阪市です", "名古屋市") == 0
    assert score_answer("", "名古屋市") == 0


def test_norm_text():
    assert norm_text("ＡＢＣ 株式会社。") == "abc株式会社"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok {name}")
    print("all tests passed")
