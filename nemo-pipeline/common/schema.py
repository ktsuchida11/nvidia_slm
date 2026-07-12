"""Stage1構造化JSONの検証（標準ライブラリのみ）。S2の教師ラベル検証とS6の被評価出力検証で共有。"""
import re
from prompts import SECTORS  # 同ディレクトリ

QUERY_TYPES = {"single", "trend", "comparison", "summary"}
RESPONSE_MODES = {"analytical", "casual", "mixed"}

def validate_analysis(obj: dict) -> dict:
    if not isinstance(obj, dict): raise ValueError("label must be object")
    sectors = obj.get("sectors", [])
    if not isinstance(sectors, list): raise ValueError("sectors must be list")
    bad = [s for s in sectors if s not in SECTORS]
    if bad: raise ValueError(f"unknown sectors: {bad}")
    dr = obj.get("date_range")
    if dr is not None:
        if not isinstance(dr, dict): raise ValueError("date_range must be object|null")
        for k in ("start", "end"):
            v = dr.get(k, "")
            if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ValueError(f"date_range.{k} must be YYYY-MM-DD")
    qt = obj.get("query_type")
    if qt not in QUERY_TYPES: raise ValueError(f"query_type invalid: {qt}")
    sq = obj.get("semantic_query")
    if not isinstance(sq, str) or not sq.strip(): raise ValueError("semantic_query required")
    rm = obj.get("response_mode", "analytical")
    if rm not in RESPONSE_MODES: raise ValueError(f"response_mode invalid: {rm}")
    return {"sectors": sectors, "date_range": dr,
            "content_types": obj.get("content_types", []) or [],
            "commodities": obj.get("commodities", []) or [],
            "query_type": qt, "semantic_query": sq.strip(),
            "needs_overall_context": bool(obj.get("needs_overall_context", False)),
            "response_mode": rm}
