"""S5 GRPO 検証可能報酬 — 既存アプリのスコア関数と同じ概念(単一の忠実性概念を共有)
解析: schema_valid + sectors/query_type/date_range一致 / 生成: source_exists + citation + number_grounded
"""
import json, re

def schema_valid(pred: str) -> float:
    try:
        obj = json.loads(pred)
        return 1.0 if {"sectors", "query_type"} <= obj.keys() else 0.0
    except Exception:
        return 0.0

def citation_format(ans: str) -> float:
    return 1.0 if re.search(r"【出典:", ans) else 0.0

def source_exists(ans: str, chunk_labels: list[str]) -> float:
    cited = re.findall(r"【出典:\s*([^】]+)】", ans)
    return 1.0 if cited and all(c.strip() in chunk_labels for c in cited) else 0.0

def refusal_without_citation(ans: str) -> bool:
    """「レポートに記載がありません」拒否は出典なしでも忠実とみなす。
    蒸留の品質ゲート(s2)は出典なし拒否を許容する一方、評価(s6)が全回答に出典を
    要求し、unanswerableへの正しい拒否が0点になる非対称があった(ループ4所見4)。
    両者でこの述語を共有して整合させる。判定はunanswerable文脈に限って使うこと
    (grounded_qaにも適用すると「常に拒否」で満点が取れてしまう)"""
    return "記載がありません" in ans and "【出典:" not in ans

def penalty(ans: str) -> float:
    if not ans.strip():
        return -1.0
    if re.search(r"(.{20,}?)\1{3,}", ans):   # 反復=EOS崩壊の兆候
        return -1.0
    return 0.0

def reward_generation(ans: str, chunk_labels: list[str]) -> float:
    return 0.5 * source_exists(ans, chunk_labels) + 0.2 * citation_format(ans) + penalty(ans)

def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m: raise ValueError("no json")
    return json.loads(m.group(0))

def reward_analysis(ans: str, gold: dict) -> float:
    """解析タスクの検証可能報酬。一致判定は s6_eval.eval_analysis と同一
    (sectors=集合一致 / query_type・date_range=完全一致)。date_range を最重み(0.5)に
    する — ループ7時点で残failure 5件中3件が date_range 起因(曜日計算・今週開始・
    summary+本日→null)で、SFTの模倣学習では上書きできないと確定したため(ループ8の1変更)。"""
    from schema import validate_analysis  # 同ディレクトリ。呼び出し側のsys.path設定後に解決するため遅延import
    base = penalty(ans)
    try:
        pred = validate_analysis(_extract_json(ans))
    except Exception:
        return base                      # schema不成立は加点なし
    r = 0.2                              # schema_valid
    r += 0.15 if set(pred["sectors"]) == set(gold["sectors"]) else 0.0
    r += 0.15 if pred["query_type"] == gold["query_type"] else 0.0
    r += 0.5 if pred["date_range"] == gold["date_range"] else 0.0
    return r + base

def reward_finance_qa(ans: str, verify: dict) -> float:
    """金融QA(finqa)タスクの検証可能報酬(ループ9)。数値=相対誤差の連続減衰、
    文字列=正規化類似。ループ8の全成分二値報酬はグループ内報酬差(GRPOの唯一の
    学習信号)を潰した — 部分点を連続値にして「惜しい」生成に信号を流す。
    verify仕様と抽出規約は common/finqa.py 参照。"""
    from finqa import score_finqa  # 同ディレクトリ。呼び出し側のsys.path設定後に解決するため遅延import
    return score_finqa(ans, verify) + penalty(ans)
