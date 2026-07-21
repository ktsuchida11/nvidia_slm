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
