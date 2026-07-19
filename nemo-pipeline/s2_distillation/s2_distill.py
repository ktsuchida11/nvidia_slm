"""S2 蒸留・メタデータ — 実装版
S1のcuratedデータを種に、(A)解析タスク: 合成問合せ→教師(Claude)が構造化JSONラベル、
(B)生成タスク: (チャンク,質問)→教師が出典【出典:】付き回答、を生成しスキーマ検証・重複排除・
層別分割(train/valid/heldout)して /data/distilled へ。heldoutは学習・GRPO報酬に使わない。

オフライン検証: --dry-run でAPI無しにテンプレ生成し、配管(検証/分割/統計)を通せる。
"""
from __future__ import annotations
import argparse, hashlib, json, logging, os, pathlib, random, re, sys, time
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "common"))
from reward import source_exists   # 単一の忠実性概念を蒸留品質ゲートでも共有
from schema import validate_analysis
from prompts import SECTORS, SYN_QUERY_SYS, CHUNK_QUERY_SYS, LABEL_SYS, ANSWER_SYS

logging.basicConfig(level=logging.INFO, format="[distill] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TEACHER = os.environ.get("TEACHER_MODEL", "claude-sonnet-4-6")
CATEGORIES = {  # 既存テストカタログの分布に沿った層別(合計1.0)
    "single_sector_single_day": 0.30, "trend": 0.15, "comparison": 0.10,
    "summary": 0.10, "ambiguous": 0.20, "edge": 0.15,
}
CAT_HINT = {
    "single_sector_single_day": "特定セクター・特定日(または日付なし)の事実照会。例:「昨日の原油は？」",
    "trend": "期間指定のトレンド。例:「先週の天然ガスの流れ」",
    "comparison": "セクター横断比較。例:「エネルギーと貴金属を比較して」",
    "summary": "全体要約。例:「今日のマーケット全体をまとめて」",
    "ambiguous": "曖昧・フィルタ無し。例:「最近どう？」「何か注目ある？」",
    "edge": "エッジ: 存在しないセクター/古すぎる日付/レポート外の話題",
}


# ---- 教師API -----------------------------------------------------------------
def call_teacher(client, system: str, user: str, max_tokens: int = 700) -> str:
    for attempt in range(3):
        try:
            r = client.messages.create(model=TEACHER, max_tokens=max_tokens,
                                       system=system, messages=[{"role":"user","content":user}])
            return r.content[0].text
        except Exception as e:
            wait = 2 ** attempt
            log.warning("teacher error (%s) retry in %ds", e, wait); time.sleep(wait)
    raise RuntimeError("teacher failed after retries")

def extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m: raise ValueError("no json in teacher output")
    return json.loads(m.group(0))


# ---- 生成器 ------------------------------------------------------------------
def gen_queries(client, cat: str, n: int, dry: bool) -> list[str]:
    if dry:
        return [f"[{cat}] ダミー質問{i}: 原油の見通しは？" for i in range(n)]
    out: list[str] = []
    while len(out) < n:
        batch = min(20, n - len(out))
        txt = call_teacher(client, SYN_QUERY_SYS,
                           f"カテゴリ「{cat}」({CAT_HINT[cat]}) の質問を{batch}個。互いに表現・商品・言い回しを変えること。")
        out += [l.strip() for l in txt.splitlines() if l.strip()][:batch]
    return out[:n]

def label_query(client, q: str, today: str, dry: bool) -> dict:
    if dry:
        return validate_analysis({"sectors": ["crude_oil"], "date_range": None,
                                  "query_type": "single", "semantic_query": q})
    raw = extract_json(call_teacher(client, LABEL_SYS.replace("{today}", today), q, 400))
    return validate_analysis(raw)

def make_chunks(docs: list[dict], k: int, chars: int, rng: random.Random) -> list[dict]:
    # ラベルは curated 内インデックスで一意化する。全件同一ラベル(データセット名)だと
    # source_exists の引用チェックが形骸化するため(ループ1の教訓)
    idxs = rng.sample(range(len(docs)), min(k, len(docs)))
    return [{"label": f'{docs[i]["meta"].get("source", "DOC")}#{i}', "text": docs[i]["text"][:chars]}
            for i in idxs]

def gen_chunk_query(client, chunk: dict, dry: bool) -> str:
    """チャンクの実記載から答えられる質問を1つ生成(grounded QAの回答可能側)"""
    if dry:
        return f"ダミー質問: {chunk['label']} の会社の沿革の要点は？"
    txt = call_teacher(client, CHUNK_QUERY_SYS, f"{chunk['text']}\n\n上の抜粋から答えられる質問を1個。", 200)
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    if not lines: raise ValueError("no query in teacher output")
    return lines[0]

def gen_answer(client, chunks: list[dict], q: str, dry: bool, refuse: bool = False) -> str:
    labels = [c["label"] for c in chunks]
    if dry:
        if refuse:
            return f"レポートに記載がありません。\n【出典: {labels[0]}】"
        return f"ダミー回答です。{q} に対する要点。\n【出典: {labels[0]}】"
    ctx = "\n---\n".join(f"[SOURCE: {c['label']}]\n{c['text']}" for c in chunks)
    return call_teacher(client, ANSWER_SYS, f"{ctx}\n\n質問: {q}", 900)

# ---- 分割・出力 ---------------------------------------------------------------
def stratified_split(items: list[dict], heldout: float, rng: random.Random):
    by_key: dict[tuple, list[dict]] = {}
    for it in items:
        by_key.setdefault((it["meta"]["task"], it["meta"]["category"]), []).append(it)
    train, valid, hold = [], [], []
    for _, group in sorted(by_key.items()):
        rng.shuffle(group)
        n = len(group); nh = max(1, int(n * heldout)); nv = max(1, int(n * heldout))
        hold += group[:nh]; valid += group[nh:nh+nv]; train += group[nh+nv:]
    return train, valid, hold

def dedup(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        src = it["input"] if isinstance(it["input"], str) \
              else json.dumps(it["input"], ensure_ascii=False, sort_keys=True)
        k = hashlib.sha1(re.sub(r"\s+", "", src).encode()).hexdigest()
        if k in seen: continue
        seen.add(k); out.append(it)
    return out

def main(inp: str, out: str, heldout_ratio: float, n_analysis: int, n_generation: int,
         gen_chunks: int, chunk_chars: int, dry: bool):
    rng = random.Random(42)
    out_p = pathlib.Path(out); out_p.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")

    client = None
    if not dry:
        if not os.getenv("ANTHROPIC_API_KEY"):
            log.error("ANTHROPIC_API_KEY 未設定。配管確認だけなら --dry-run"); sys.exit(1)
        import anthropic
        client = anthropic.Anthropic()
        est = (n_analysis * 0.6 + n_generation * 2.2)  # kトークン概算/件(質問生成の1呼び出し分を含む)
        log.info("概算教師コスト: ~$%.1f (入出力%.0fKtok想定・実測で要確認)", est * 0.015, est)

    items, rejects = [], Counter()

    # (A) 解析タスク
    for cat, ratio in CATEGORIES.items():
        for q in gen_queries(client, cat, max(1, int(n_analysis * ratio)), dry):
            try:
                label = label_query(client, q, today, dry)
            except (ValueError, json.JSONDecodeError) as e:
                rejects[f"analysis:{type(e).__name__}"] += 1; continue
            items.append({"input": q, "label": label,
                          "meta": {"task": "analysis", "category": cat,
                                   "difficulty": "easy" if cat == "single_sector_single_day" else "normal",
                                   "teacher": TEACHER}})

    # (B) 生成タスク(蒸留)
    # 80%: チャンク実記載から答えられる質問(grounded_qa) / 20%: チャンク外の質問に
    # 正しく「記載がありません」と答える見本(unanswerable)。ループ1では質問テンプレが
    # チャンク内容(EDINET開示)とミスマッチで全件が記載なし回答になった(loop-01-report.md)
    docs = [json.loads(l) for l in (pathlib.Path(inp) / "curated.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    fin_docs = [d for d in docs if d["meta"].get("domain") == "finance"] or docs
    n_unans = int(n_generation * 0.2)
    for i in range(n_generation):
        chunks = make_chunks(fin_docs, k=gen_chunks, chars=chunk_chars, rng=rng)
        labels = [c["label"] for c in chunks]
        unanswerable = i < n_unans
        try:
            q = gen_queries(client, "single_sector_single_day", 1, dry)[0] if unanswerable \
                else gen_chunk_query(client, chunks[0], dry)
        except ValueError:
            rejects["generation:query_gen"] += 1; continue
        ans = gen_answer(client, chunks, q, dry, refuse=unanswerable)
        refused = "記載がありません" in ans
        if refused != unanswerable:                # 回答可能性と実回答の不整合は棄却
            rejects["generation:answerability_mismatch"] += 1; continue
        # 忠実性ゲート(報酬と同一関数)。記載なし回答は引用なしでも可、引用があるなら正しいこと
        cite_ok = source_exists(ans, labels) >= 1.0 or (refused and "【出典:" not in ans)
        if not cite_ok:
            rejects["generation:source_exists"] += 1; continue
        items.append({"input": {"question": q, "chunks": chunks}, "label": ans,
                      "meta": {"task": "generation",
                               "category": "unanswerable" if unanswerable else "grounded_qa",
                               "difficulty": "normal", "teacher": TEACHER}})

    items = dedup(items)
    train, valid, hold = stratified_split(items, heldout_ratio, rng)
    for name, part in [("train", train), ("valid", valid), ("heldout", hold)]:
        with (out_p / f"{name}.jsonl").open("w", encoding="utf-8") as w:
            for it in part: w.write(json.dumps(it, ensure_ascii=False) + "\n")
    stats = {"total": len(items), "train": len(train), "valid": len(valid), "heldout": len(hold),
             "rejects": dict(rejects),
             "by": dict(Counter(f'{i["meta"]["task"]}/{i["meta"]["category"]}' for i in items))}
    (out_p / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("done: %s", stats)
    log.info("★ heldout.jsonl は学習・GRPO報酬に使わないこと(評価専用)")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--heldout-ratio", type=float, default=0.1)
    p.add_argument("--n-analysis", type=int, default=300)
    p.add_argument("--n-generation", type=int, default=100)
    p.add_argument("--gen-chunks", type=int, default=2,
                   help="生成タスクのチャンク数(SFT seq2048に収める: 2×700字+システム+回答で概ね上限)")
    p.add_argument("--chunk-chars", type=int, default=700)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    main(a.inp, a.out, a.heldout_ratio, a.n_analysis, a.n_generation, a.gen_chunks, a.chunk_chars, a.dry_run)
