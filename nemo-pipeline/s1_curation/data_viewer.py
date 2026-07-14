"""パイプラインデータビューア（完全ローカル・閉域）
/data 配下の jsonl（curated/rejected/distilled/rl）をブラウザで目視確認する。
起動: make viewer → http://localhost:$VIEWER_PORT

NeMo Curator にはデータ内容を閲覧するUIは無い（Ray dashboard / Grafana は
スループット等の運用監視のみ）ため、キュレーション結果・蒸留結果の
QC(目視検品)はこのビューアで行う。
"""
from __future__ import annotations
import json
import pathlib

import streamlit as st

DATA = pathlib.Path("/data")
PAGE_SIZE = 20

st.set_page_config(page_title="nemo-pipeline data viewer", layout="wide")
st.title("nemo-pipeline データビューア")


@st.cache_data(show_spinner=False)
def list_jsonl() -> list[str]:
    return sorted(str(p.relative_to(DATA)) for p in DATA.rglob("*.jsonl"))


@st.cache_data(show_spinner="読み込み中…")
def load(rel: str) -> list[dict]:
    rows = []
    with (DATA / rel).open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"_parse_error": line[:200]})
    return rows


def record_text(rec: dict) -> str:
    """本文表示に使うフィールドを推定（curated=text / distilled=input）"""
    for key in ("text", "input", "prompt"):
        if isinstance(rec.get(key), str):
            return rec[key]
    return json.dumps(rec, ensure_ascii=False)


def searchable(rec: dict) -> str:
    """検索対象: 本文に加え教師の回答側（label/output/ground_truth）も含める
    （例:「記載がありません」で蒸留回答の空振り件数を確認する用途）"""
    parts = [record_text(rec)]
    for k in ("label", "output", "ground_truth"):
        if k in rec:
            v = rec[k]
            parts.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return "\n".join(parts)


files = list_jsonl()
if not files:
    st.warning("/data に jsonl がありません。make fetch / curate を先に実行してください。")
    st.stop()

with st.sidebar:
    sel = st.selectbox("ファイル", files)
    query = st.text_input("検索（本文＋label/output/ground_truth・部分一致）")
    st.caption("列フィルタは meta の値で絞り込み")

rows = load(sel)

# --- stats.json（同ディレクトリにあれば表示） ---------------------------------
stats_path = (DATA / sel).parent / "stats.json"
if stats_path.exists():
    with st.expander(f"stats.json（{stats_path.relative_to(DATA)}）", expanded=False):
        st.json(json.loads(stats_path.read_text()))

# --- meta によるフィルタ -------------------------------------------------------
def meta_of(rec: dict) -> dict:
    m = rec.get("meta") or {}
    if "reject_reason" in rec:
        m = {**m, "reject_reason": rec["reject_reason"]}
    return m

meta_keys = sorted({k for r in rows for k in meta_of(r) if isinstance(meta_of(r).get(k), (str, int, float, bool))})
filters: dict[str, str] = {}
with st.sidebar:
    for k in meta_keys:
        vals = sorted({str(meta_of(r)[k]) for r in rows if k in meta_of(r)})
        if 1 < len(vals) <= 30:
            choice = st.selectbox(k, ["(すべて)"] + vals)
            if choice != "(すべて)":
                filters[k] = choice

view = [
    r for r in rows
    if all(str(meta_of(r).get(k)) == v for k, v in filters.items())
    and (not query or query.lower() in searchable(r).lower())
]

st.caption(f"{sel} — {len(view)} / {len(rows)} 件表示（フィルタ適用後）")

# --- ページング + レコード表示 --------------------------------------------------
pages = max(1, -(-len(view) // PAGE_SIZE))
page = st.number_input("ページ", 1, pages, 1) - 1
for i, rec in enumerate(view[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], start=page * PAGE_SIZE):
    body = record_text(rec)
    head = body[:120].replace("\n", " ")
    reject = rec.get("reject_reason")
    label = f"#{i}  {'🚫 ' + reject + '  ' if reject else ''}{head}"
    with st.expander(label):
        left, right = st.columns([3, 2])
        with left:
            st.text_area("本文", body, height=280, key=f"body{sel}{i}", label_visibility="collapsed")
        with right:
            for k in ("label", "output", "ground_truth"):
                if k in rec:
                    v = rec[k]
                    if isinstance(v, str):
                        try:
                            v = json.loads(v)
                        except json.JSONDecodeError:
                            pass
                    st.markdown(f"**{k}**")
                    st.json(v, expanded=False)
            if meta_of(rec):
                st.markdown("**meta**")
                st.json(meta_of(rec), expanded=False)
