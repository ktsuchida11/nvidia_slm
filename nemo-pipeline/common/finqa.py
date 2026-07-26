"""金融QA(finqa)の検証可能スコアラ — S2構築・S5報酬・S6評価で同一関数を共有する。

ループ8の教訓を織り込んだ設計:
  - 全成分二値の報酬はグループ内報酬差(=GRPOの学習信号)を潰す。数値一致は
    相対誤差に応じた連続減衰(score_value)で「惜しい」答えに部分点を流す
  - 採点対象は「答え: <数値><単位>」形式の最終行を優先し、無ければ本文末尾の
    数値へフォールバック(形式未習得のbaseモデルも0点に張り付かない)

ゴールド仕様(grpo/heldoutのverify辞書):
  {"value": 正規化済みfloat(円建て等・倍率適用済み), "unit": "円"|"%"|"倍"|null,
   "tolerance": 相対誤差の満点閾値(省略時0.005)}
  文字列答えの場合: {"answer_text": "正解文字列"}
"""
import re
import unicodedata
from difflib import SequenceMatcher

# 倍率語(数値の直後に付くもの)。「1兆2000億」等の複合は _COMPOSITE_RE で先に解決
_MULT = {"兆": 1e12, "十億": 1e9, "億": 1e8, "百万": 1e6, "万": 1e4, "千": 1e3}
_NUM = r"[-+▲△]?\d[\d,]*(?:\.\d+)?"
_COMPOSITE_RE = re.compile(
    rf"({_NUM})\s*兆\s*(\d[\d,]*(?:\.\d+)?)\s*億|({_NUM})\s*億\s*(\d[\d,]*(?:\.\d+)?)\s*万")
_UNIT = "円|ドル|%|パーセント|ポイント|倍|株|人|トン|件|社"
_NUM_RE = re.compile(rf"({_NUM})\s*(兆|十億|億|百万|万|千)?\s*({_UNIT})?")
_ANS_LINE_RE = re.compile(r"答え\s*[:：]\s*(.+)")


def _to_float(s: str) -> float:
    s = s.replace(",", "")
    neg = s[0] in "▲△" or s.startswith("-")
    return (-1 if neg else 1) * float(s.lstrip("+-▲△"))


def parse_number(text: str) -> tuple[float, str | None] | None:
    """text中の最初の数値を(正規化値, 単位)で返す。単位は '%'|'円'|'倍' 等か None。
    全角・カンマ・▲△(負値の財務表記)・兆/億/百万/万の倍率・「1兆2000億」複合に対応。"""
    text = unicodedata.normalize("NFKC", text)
    m = _COMPOSITE_RE.search(text)
    mn = _NUM_RE.search(text)
    if m and (not mn or m.start() <= mn.start()):
        if m.group(1) is not None:      # X兆Y億
            val = _to_float(m.group(1)) * 1e12 + _to_float(m.group(2)) * 1e8
        else:                            # X億Y万
            val = _to_float(m.group(3)) * 1e8 + _to_float(m.group(4)) * 1e4
        rest = text[m.end():]
        unit = rest[:1] if rest[:1] in ("円",) else None
        return val, unit
    if not mn:
        return None
    val = _to_float(mn.group(1)) * _MULT.get(mn.group(2) or "", 1.0)
    unit = mn.group(3)
    if unit == "パーセント":
        unit = "%"
    return val, unit


def parse_valued_number(text: str) -> tuple[float, str | None] | None:
    """単位か倍率語を伴う最初の数値。裸の数字(「第19期」「指標92」等)を拾わないため、
    数値答え候補の検出とdry-run検証はこちらを使う(S2の教師verify課金の無駄を防ぐ)。"""
    text = unicodedata.normalize("NFKC", text)
    m = _COMPOSITE_RE.search(text)
    for mn in _NUM_RE.finditer(text):
        if m and m.start() <= mn.start():
            return parse_number(text[m.start():])
        if mn.group(2) or mn.group(3):
            return parse_number(text[mn.start():])
    return parse_number(text[m.start():]) if m else None


def extract_final_answer(ans: str) -> tuple[float, str | None] | None:
    """「答え:」行(最後の出現)を優先して数値を取り、無ければ本文の最後の数値。"""
    lines = _ANS_LINE_RE.findall(ans)
    if lines:
        got = parse_number(lines[-1])
        if got:
            return got
    last = None
    for m in _NUM_RE.finditer(unicodedata.normalize("NFKC", ans)):
        last = m
    if last is None:
        return None
    return parse_number(unicodedata.normalize("NFKC", ans)[last.start():])


def has_answer_marker(ans: str) -> bool:
    return bool(_ANS_LINE_RE.search(ans))


def score_value(pred: float, gold: float, tolerance: float = 0.005) -> float:
    """相対誤差ベースの連続部分点。tolerance以内=1.0、以後10%誤差幅で線形減衰して0。"""
    relerr = abs(pred - gold) / max(abs(gold), 1e-9)
    if relerr <= tolerance:
        return 1.0
    return max(0.0, 1.0 - (relerr - tolerance) / 0.10)


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[\s。、．，「」『』()（）]", "", s)


def score_finqa(ans: str, verify: dict) -> float:
    """finqa報酬の本体(ペナルティ抜き・0.0〜1.0)。
    数値: 0.1 数値を出せた + 0.1 「答え:」形式 + 0.8×score_value(連続)
    文字列: 0.2 形式 + 0.8×類似度(正規化一致=1.0、部分一致は連続減衰)"""
    if "answer_text" in verify:
        r = 0.2 if has_answer_marker(ans) else 0.0
        gold_n, ans_n = _norm_text(verify["answer_text"]), _norm_text(ans)
        if not gold_n:
            return r
        if gold_n in ans_n:
            return r + 0.8
        ratio = SequenceMatcher(None, gold_n, ans_n[-len(gold_n) * 3:]).ratio()
        return r + 0.8 * max(0.0, (ratio - 0.5) / 0.5)
    got = extract_final_answer(ans)
    if got is None:
        return 0.0
    pred, pred_unit = got
    r = 0.1 + (0.1 if has_answer_marker(ans) else 0.0)
    tol = float(verify.get("tolerance", 0.005))
    gold = float(verify["value"])
    s = score_value(pred, gold, tol)
    # ゴールドが%でpredが単位なし小数(0.123 vs 12.3%)は同義とみなし良い方を採用
    if verify.get("unit") == "%" and pred_unit is None:
        s = max(s, score_value(pred * 100, gold, tol))
    return r + 0.8 * s
