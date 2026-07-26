"""finqaスコアラ・報酬・環境振り分けのユニットテスト（ループ9）。
実行: python3 -m pytest tests/ -q  （pytest無し環境: python3 tests/test_finqa.py）
外部依存なし（ray/nemo_rl不在でも finance_env の純関数部分を検査できる）。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "s5_rl"))
sys.path.insert(0, str(ROOT / "s2_distillation"))

from finqa import (extract_final_answer, has_answer_marker, parse_number,  # noqa: E402
                   parse_valued_number, score_finqa, score_value)
from reward import reward_finance_qa  # noqa: E402
from finance_env import score_batch  # noqa: E402
from s2_finqa import format_answer_line  # noqa: E402
import json  # noqa: E402


def test_parse_number_units():
    assert parse_number("1,234百万円") == (1.234e9, "円")
    assert parse_number("12.3億円") == (1.23e9, "円")
    assert parse_number("8900万") == (8.9e7, None)
    assert parse_number("2.5倍") == (2.5, "倍")
    assert parse_number("15.2パーセント") == (15.2, "%")


def test_parse_number_composite_and_negative():
    assert parse_number("1兆2000億円") == (1.2e12, "円")
    assert parse_number("3億5000万") == (3.5e8, None)
    assert parse_number("▲123百万円") == (-1.23e8, "円")
    assert parse_number("△45.6億円") == (-4.56e9, "円")


def test_parse_number_fullwidth():
    assert parse_number("１２．３％") == (12.3, "%")


def test_parse_valued_number_skips_bare_numbers():
    # 「第19期」「指標92」のような裸の数字は候補にしない（S2の課金の無駄防止）
    assert parse_valued_number("第19期の指標92は堅調でした") is None
    got = parse_valued_number("第19期の売上は392.0百万円でした")
    assert got == (3.92e8, "円")


def test_extract_final_answer_prefers_marker_line():
    ans = "計算: 1,234百万円 - 1,100百万円 = 134百万円\n答え: 134百万円"
    assert extract_final_answer(ans) == (1.34e8, "円")
    assert has_answer_marker(ans)


def test_extract_final_answer_fallback_last_number():
    assert extract_final_answer("自己資本比率はおよそ42.5%です") == (42.5, "%")
    assert extract_final_answer("数値はありません") is None


def test_score_value_tolerance_and_monotone():
    assert score_value(100.0, 100.3, 0.005) == 1.0     # 許容誤差内=満点
    assert score_value(150.0, 100.0) == 0.0            # 50%誤差=0
    prev = 1.0
    for p in (100, 101, 103, 105, 108, 111, 120):      # 誤差増で単調非増加(連続部分点)
        s = score_value(float(p), 100.0)
        assert s <= prev + 1e-9
        prev = s


def test_score_finqa_numeric():
    v = {"value": 1.34e8, "unit": "円"}
    assert score_finqa("計算過程。\n答え: 134百万円", v) == 1.0
    partial = score_finqa("答え: 140百万円", v)          # 4.5%誤差 → 部分点(二値でない)
    assert 0.2 < partial < 1.0
    assert score_finqa("わかりません", v) == 0.0


def test_score_finqa_percent_decimal_equivalence():
    assert score_finqa("答え: 0.425", {"value": 42.5, "unit": "%"}) == 1.0


def test_score_finqa_answer_text():
    assert score_finqa("答え: 自己資本比率", {"answer_text": "自己資本比率"}) == 1.0
    assert score_finqa("全く違う話", {"answer_text": "自己資本比率"}) < 0.5


def test_reward_finance_qa_penalty():
    assert reward_finance_qa("", {"value": 1.0, "unit": None}) == -1.0   # 空生成ペナルティ
    assert reward_finance_qa("答え: 1", {"value": 1.0, "unit": None}) == 1.0


def test_score_batch_dispatch_finqa():
    gt = json.dumps({"finqa": {"value": 42.5, "unit": "%", "tolerance": 0.005}})
    scores = score_batch(["<think>思考</think>答え: 42.5%", "答え: 99%"], [gt, gt])
    assert scores[0] == 1.0                             # <think>除去後に採点される
    assert scores[1] < scores[0]                        # グループ内に報酬差が出る


def test_format_answer_line():
    # 整数値は小数点以下を落とす・単位を付す
    assert format_answer_line({"value": 1.34e8, "unit": "円"}) == "答え: 134000000円"
    # 小数値は保持
    assert format_answer_line({"value": 42.5, "unit": "%"}) == "答え: 42.5%"
    # 単位 None / その他 は数値のみ
    assert format_answer_line({"value": 8900.0, "unit": None}) == "答え: 8900"
    assert format_answer_line({"value": 2.5, "unit": "その他"}) == "答え: 2.5"


def test_format_answer_line_roundtrips_through_scorer():
    # fmt教示で付けた「答え:」行が、同一verifyでscore_finqa満点になること(訓練=評価の整合)
    for verify in ({"value": 1.34e8, "unit": "円"}, {"value": 42.5, "unit": "%"},
                   {"value": 2.5, "unit": "倍"}):
        line = "回答本文です。\n\n" + format_answer_line(verify)
        assert score_finqa(line, verify) == 1.0


def test_score_batch_dispatch_analysis_unchanged():
    gt = json.dumps({"label": {"sectors": ["crude_oil"], "query_type": "single",
                               "date_range": None, "semantic_query": "q"}})
    pred = json.dumps({"sectors": ["crude_oil"], "query_type": "single",
                       "date_range": None, "semantic_query": "q"}, ensure_ascii=False)
    assert score_batch([pred], [gt])[0] == 1.0


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except AssertionError as e:
                failed += 1; print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
