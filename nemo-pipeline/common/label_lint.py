"""教師ゴールドの規約リント — LABEL_SYS 規約(1)〜(5)の機械検証可能な部分だけを検査する。
ループ3でゴールド側の誤り(「今週火曜日」のend+1日、「一言で表すと」がqt=single)が
評価失敗として観測された対策。検出のみで自動修正はしない — 修正は教師再ラベル
(make relabel-lint)で行い、意味判断は教師に残す。誤検知は再ラベル費の微増で済むが、
見逃しは天井として残るため、日付規則は厳密に・語彙規則は保守的に定める。"""
from __future__ import annotations
import datetime
import re

# 規約(5): セクション種別はセクターとして使わない
BANNED_SECTORS = {"misc", "column", "data", "announcement"}
_WD = "月火水木金土日"
# 規約(2)の「明示的な日付・期間表現」検出。ここは広めに取る(広いほど
# date_without_expr の誤検知が減る安全側)。実データ検証で追補:
# 今朝/昨晩/四半期/半年/この3ヶ月/1年間 等も明示的な期間表現(「最近」との違い)
_DATE_EXPR = re.compile(
    r"今日|本日|昨日|一昨日|先週|今週|先月|今月|昨年|去年|今年|年初|年末|年度|"
    r"今朝|昨晩|昨夜|今晩|今夜|四半期|半年|"
    r"\d+\s*日前|\d+\s*週間|数\s*[日週]|[\d数]\s*[ヶかカケ箇]月|\d+\s*年間|"
    r"過去\s*\d+|直近\s*\d+|\d{1,2}月\d{1,2}日|\d{4}年|"
    rf"[{_WD}]曜")


def _d(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def lint_label(q: str, label: dict, today: str) -> list[str]:
    """質問文とゴールドを規約に照らし、違反コードのリストを返す(空=合格)"""
    v: list[str] = []
    t = _d(today)
    sectors = label.get("sectors", [])
    dr = label.get("date_range")
    qt = label.get("query_type")

    # ---- sectors: 規約(1)(5) ----
    if "overall" in sectors and len(sectors) > 1:
        v.append("overall_mixed")
    # 全列挙の閾値は6: 「再生可能エネルギー素材vs化石燃料」のような正当な
    # 多セクター比較が5個に達する実例があるため(ループ2の揺れは8個全列挙)
    if len(sectors) >= 6:
        v.append("sector_enumeration")
    if BANNED_SECTORS & set(sectors):
        v.append("banned_section_sector")

    # ---- date_range: 一般整合 ----
    if dr is not None:
        start, end = _d(dr["start"]), _d(dr["end"])
        if start > end:
            v.append("date_start_after_end")
        if end > t:
            v.append("date_future")
        if not _DATE_EXPR.search(q):
            v.append("date_without_expr")  # 規約(2)

    # ---- date_range: 規約(3)の固定解釈(表現が1種類だけのときのみ厳密検査。
    #      「今日と昨日を比較」のような複合表現は範囲が合成されるため対象外) ----
    single_day_exprs = {
        "今日": t, "本日": t, "今朝": t,
        "昨日": t - datetime.timedelta(days=1),
        "昨晩": t - datetime.timedelta(days=1), "昨夜": t - datetime.timedelta(days=1),
        "一昨日": t - datetime.timedelta(days=2),
    }
    range_exprs = {
        "先週": (t - datetime.timedelta(days=7), t),
        "今月": (t.replace(day=1), t),
    }
    # 検査対象外の表現(今週/先月/四半期等)も複合判定のブロッカーとして数える —
    # 「先月末から今月初旬」のような複合表現は範囲が合成されるため厳密検査しない
    blockers = ["今週", "先月", "昨年", "去年", "今年", "四半期", "半年"]
    hits = [w for w in list(single_day_exprs) + list(range_exprs) + blockers if w in q]
    if "一昨日" in hits and "昨日" in hits:  # 「一昨日」は「昨日」を含む
        hits.remove("昨日")
    m_past = re.search(r"過去\s*(\d+)\s*日", q)
    if len(hits) == 1 and not m_past:
        w = hits[0]
        if w in single_day_exprs:
            d = single_day_exprs[w].isoformat()
            if dr != {"start": d, "end": d}:
                v.append(f"date_rule3_{w}")
        elif w in range_exprs and not re.search(rf"{w}[{_WD}]曜", q):  # 「先週火曜」等は単日
            s, e = range_exprs[w]
            if dr != {"start": s.isoformat(), "end": e.isoformat()}:
                v.append(f"date_rule3_{w}")
    elif m_past and not hits:
        s = t - datetime.timedelta(days=int(m_past.group(1)))
        if dr != {"start": s.isoformat(), "end": t.isoformat()}:
            v.append("date_rule3_過去N日")
    # 「今週/先週X曜日」は該当X曜日の単日(月曜始まり週)。日付まで決定的に検査する —
    # ループ3でend+1日、ループ4再ラベルでも曜日ずれ(火曜のつもりが水曜の日付)が
    # 出た規則。教師は暦計算に弱いため機械検証が必須
    m_wd = re.search(rf"(今週|先週)([{_WD}])曜", q)
    if m_wd and dr is not None:
        monday = t - datetime.timedelta(days=t.weekday())
        if m_wd.group(1) == "先週":
            monday -= datetime.timedelta(days=7)
        exp = (monday + datetime.timedelta(days=_WD.index(m_wd.group(2)))).isoformat()
        if dr != {"start": exp, "end": exp}:
            v.append("date_weekday_mismatch")

    # ---- query_type: 規約(4)の機械検証可能部分 ----
    # 比較語がある1セクターcomparisonは時点間比較(規約(4)で許容)なので流さない
    if qt == "comparison" and len(sectors) < 2 and not re.search(r"比較|比べ|対比", q):
        v.append("comparison_lt2_sectors")
    if qt == "single" and re.search(r"一言|ひとこと|まとめて|総括|要約して", q):
        v.append("summary_worded_but_single")

    return v
