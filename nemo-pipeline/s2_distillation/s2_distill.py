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
from reward import source_exists, refusal_without_citation   # 単一の忠実性概念を蒸留品質ゲートでも共有
from schema import validate_analysis
from prompts import SECTORS, SYN_QUERY_SYS, CHUNK_QUERY_SYS, LABEL_SYS, ANSWER_SYS, format_today
from label_lint import lint_label

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
# ループ7の的絞り増強: SFTが訓練分布の疑似相関を増幅する(ループ3・4・6で再現)ため、
# 不足パターンの訓練例をtrain/validにのみ追記して増幅方向を反転させる。
# 件数は過学習を避けるため控えめ(計45件≒trainの15%)。categoryは既存分類に
# 合わせて評価のby_category集計と揃え、由来はmeta.augmentで追跡する
AUGMENT_PATTERNS = {
    "summary_dated": {"category": "summary", "n": 15,
                      # 既存summaryはほぼ日付なし(date_range=null)のみ→「summary=null」疑似相関の源
                      "hint": "「本日」「今日」「昨日」「先週」など日付・期間の言葉を必ず含む全体要約。"
                              "例:「本日の相場全体をまとめて」「先週のマーケット総括は？」"},
    "week_range": {"category": "trend", "n": 15,
                   # 「今週」は訓練に2件のみで規約(3)の月曜始まりが上書きできなかった
                   "hint": "「今週」という言葉を必ず含む期間トレンド。"
                           "例:「今週の金の動きは？」「今週に入ってからの原油はどう？」"},
    "comparison_pair": {"category": "comparison", "n": 15,
                        # 商品名ペア比較でsectors=["overall"]逃げが再発した
                        "hint": "セクター名ではなく具体的な商品名2つの比較。"
                                "例:「金と原油はどちらが上がった？」「銅と天然ガスの値動きを比べて」"},
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
def gen_queries(client, cat: str, n: int, dry: bool, hint: str | None = None) -> list[str]:
    if dry:
        return [f"[{cat}] ダミー質問{i}: 原油の見通しは？" for i in range(n)]
    out: list[str] = []
    while len(out) < n:
        batch = min(20, n - len(out))
        txt = call_teacher(client, SYN_QUERY_SYS,
                           f"カテゴリ「{cat}」({hint or CAT_HINT[cat]}) の質問を{batch}個。互いに表現・商品・言い回しを変えること。")
        out += [l.strip() for l in txt.splitlines() if l.strip()][:batch]
    return out[:n]

def label_query(client, q: str, today: str, dry: bool) -> dict:
    if dry:
        return validate_analysis({"sectors": ["crude_oil"], "date_range": None,
                                  "query_type": "single", "semantic_query": q})
    raw = extract_json(call_teacher(client, LABEL_SYS.replace("{today}", format_today(today)), q, 400))
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

def lint_splits(out_p: pathlib.Path, today: str) -> dict:
    """既存分割のanalysisゴールドを規約リント(label_lint)にかけ、違反一覧を
    lint_report.json へ書く。API不要。基準日は各itemのmeta.label_todayを優先
    (相対日付ゴールドはラベル付け日基準のため)。"""
    report: dict = {"violations": {}, "counts": Counter()}
    for name in ["train", "valid", "heldout"]:
        p = out_p / f"{name}.jsonl"
        items = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        vs = []
        for i, it in enumerate(items):
            if it["meta"]["task"] != "analysis":
                continue
            codes = lint_label(it["input"], it["label"], it["meta"].get("label_today") or today)
            if codes:
                vs.append({"i": i, "codes": codes, "input": it["input"], "label": it["label"]})
                report["counts"].update(codes)
        report["violations"][name] = vs
        log.info("lint %s: 違反 %d件 / analysis %d件", name, len(vs),
                 sum(1 for it in items if it["meta"]["task"] == "analysis"))
    report["counts"] = dict(report["counts"])
    (out_p / "lint_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("違反コード内訳: %s", report["counts"])
    return report

def relabel_splits(client, out_p: pathlib.Path, today: str, dry: bool,
                   only: dict[str, set[int]] | None = None) -> dict:
    """既存分割(train/valid/heldout)のanalysisラベルのみをLABEL_SYSの現行規約で再生成。
    質問・generation・分割は保持するためリークは発生しない。ラベル規約の変更は
    heldoutのゴールドも再定義するので、評価はbaseから再測定すること。
    基準日は meta.label_today に記録し、評価側の相対日付計算と揃える。
    only指定時(規約リント違反のみ等)は該当インデックスだけ再ラベルする —
    このとき基準日は既存itemのlabel_todayと揃えること(混在するとs6_evalが警告し
    実行日にフォールバックして相対日付ゴールドが崩れる)。"""
    backup = out_p / "pre-relabel-backup"
    backup.mkdir(exist_ok=True)
    stats: dict = {"today": today, "changed": {}, "failed": {}, "analysis": {}}
    for name in ["train", "valid", "heldout"]:
        p = out_p / f"{name}.jsonl"
        items = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        (backup / f"{name}.jsonl").write_text(
            "".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")
        changed = failed = n_ana = 0
        for i, it in enumerate(items):
            if it["meta"]["task"] != "analysis":
                continue
            if only is not None and i not in only.get(name, set()):
                continue
            n_ana += 1
            try:
                new = label_query(client, it["input"], today, dry)
            except (ValueError, json.JSONDecodeError):
                failed += 1; continue      # 失敗時は旧ラベル維持(件数・分割を不変に保つ)
            if new != it["label"]:
                changed += 1
            it["label"] = new
            it["meta"]["label_today"] = today
        p.write_text("".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items),
                     encoding="utf-8")
        stats["changed"][name] = changed; stats["failed"][name] = failed
        stats["analysis"][name] = n_ana
        log.info("relabel %s: analysis %d件中 変更%d / 失敗(旧維持)%d", name, n_ana, changed, failed)
    (out_p / "relabel_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats

def augment_splits(client, out_p: pathlib.Path, today: str, dry: bool,
                   rng: random.Random) -> dict:
    """的絞りデータ増強(ループ7): AUGMENT_PATTERNSの質問を教師生成→現行規約でラベル→
    リント合格分のみを train/valid に90/10で追記する。heldoutは重複排除の参照のみで
    一切書き換えない(評価専用・不可侵)。基準日は既存itemのlabel_todayに揃えること
    (mainで整合検証済み。混在するとs6_evalが実行日フォールバックしゴールドが崩れる)。"""
    backup = out_p / "pre-augment-backup"
    backup.mkdir(exist_ok=True)
    splits: dict[str, list[dict]] = {}
    seen: set[str] = set()   # 全split(heldout含む)との重複を排除 — heldout類似問の混入防止
    for name in ["train", "valid", "heldout"]:
        p = out_p / f"{name}.jsonl"
        items = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        splits[name] = items
        seen |= {dedup_key(it["input"]) for it in items}
        if name != "heldout":
            (backup / f"{name}.jsonl").write_text(
                "".join(json.dumps(it, ensure_ascii=False) + "\n" for it in items), encoding="utf-8")

    stats: dict = {"today": today, "added": {}, "rejects": {}}
    for pat, spec in AUGMENT_PATTERNS.items():
        added: list[dict] = []
        rejects: Counter = Counter()
        for q in gen_queries(client, pat, spec["n"], dry, hint=spec["hint"]):
            k = dedup_key(q)
            if k in seen:
                rejects["dup"] += 1; continue
            try:
                label = label_query(client, q, today, dry)
            except (ValueError, json.JSONDecodeError) as e:
                rejects[f"label:{type(e).__name__}"] += 1; continue
            # 増強の目的はゴールド分布の矯正なので、規約違反ラベルは即棄却(再ラベル不要に保つ)。
            # dryのダミーラベルは配管検証用のためリント対象外
            codes = lint_label(q, label, today)
            if codes and not dry:
                rejects["lint:" + ",".join(codes)] += 1; continue
            seen.add(k)
            added.append({"input": q, "label": label,
                          "meta": {"task": "analysis", "category": spec["category"],
                                   "difficulty": "normal", "teacher": TEACHER,
                                   "label_today": today, "augment": pat}})
        rng.shuffle(added)
        nv = max(1, int(len(added) * 0.1)) if added else 0
        splits["valid"] += added[:nv]
        splits["train"] += added[nv:]
        stats["added"][pat] = {"total": len(added), "train": len(added) - nv, "valid": nv}
        stats["rejects"][pat] = dict(rejects)
        log.info("augment %s: 追加%d件 (train %d / valid %d) 棄却%s",
                 pat, len(added), len(added) - nv, nv, dict(rejects))

    for name in ["train", "valid"]:      # heldoutは書き換えない
        (out_p / f"{name}.jsonl").write_text(
            "".join(json.dumps(it, ensure_ascii=False) + "\n" for it in splits[name]), encoding="utf-8")
    (out_p / "augment_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


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

def dedup_key(inp) -> str:
    src = inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(re.sub(r"\s+", "", src).encode()).hexdigest()

def dedup(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        k = dedup_key(it["input"])
        if k in seen: continue
        seen.add(k); out.append(it)
    return out

def main(inp: str, out: str, heldout_ratio: float, n_analysis: int, n_generation: int,
         gen_chunks: int, chunk_chars: int, dry: bool, relabel: bool = False,
         lint: bool = False, only_lint: bool = False, today_arg: str | None = None,
         augment: bool = False):
    rng = random.Random(42)
    out_p = pathlib.Path(out); out_p.mkdir(parents=True, exist_ok=True)
    today = today_arg or time.strftime("%Y-%m-%d")

    # リント・部分再ラベル・増強の基準日は既存データの label_today に揃える。
    # 基準日が混在すると s6_eval が実行日フォールバックし相対日付ゴールドが崩れる
    if lint or augment or (relabel and only_lint):
        lts: set = set()
        for name in ["train", "valid", "heldout"]:
            p = out_p / f"{name}.jsonl"
            if not p.exists(): continue
            for l in p.read_text(encoding="utf-8").splitlines():
                if not l.strip(): continue
                it = json.loads(l)
                if it["meta"]["task"] == "analysis":
                    lts.add(it["meta"].get("label_today"))
        lts.discard(None)
        if not today_arg and len(lts) == 1:
            today = next(iter(lts))
        elif len(lts) == 1 and today not in lts:
            log.error("--today=%s が既存の label_today=%s と不一致。基準日を揃えること", today, lts)
            sys.exit(1)
        log.info("基準日(today)=%s", today)

    if lint:
        lint_splits(out_p, today)
        return

    only = None
    n_teacher = n_analysis
    if augment:
        n_teacher = sum(spec["n"] for spec in AUGMENT_PATTERNS.values())
    if relabel and only_lint:
        rp = out_p / "lint_report.json"
        if not rp.exists():
            log.error("lint_report.json が無い。先に --lint を実行"); sys.exit(1)
        rep = json.loads(rp.read_text(encoding="utf-8"))
        only = {name: {v["i"] for v in vs} for name, vs in rep["violations"].items()}
        n_teacher = sum(len(s) for s in only.values())
        if n_teacher == 0:
            log.info("リント違反0件 — 再ラベル不要"); return

    client = None
    if not dry:
        if not os.getenv("ANTHROPIC_API_KEY"):
            log.error("ANTHROPIC_API_KEY 未設定。配管確認だけなら --dry-run"); sys.exit(1)
        import anthropic
        client = anthropic.Anthropic()
        est = (n_teacher * 0.6) if (relabel or augment) else (n_teacher * 0.6 + n_generation * 2.2)  # kトークン概算/件
        log.info("概算教師コスト: ~$%.1f (入出力%.0fKtok想定・実測で要確認)", est * 0.015, est)

    if augment:
        augment_splits(client, out_p, today, dry, rng)
        log.info("★ train/valid が変わったため prep-rl の再実行が必要(heldoutは不変・再評価はbaseから)")
        return

    if relabel:
        relabel_splits(client, out_p, today, dry, only)
        log.info("★ ラベル規約が変わったため prep-rl の再実行と base からの再評価が必要")
        return

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
        cite_ok = source_exists(ans, labels) >= 1.0 or refusal_without_citation(ans)
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
    p.add_argument("--relabel", action="store_true",
                   help="既存分割のanalysisラベルのみLABEL_SYS現行規約で再生成(質問・分割・generation保持)")
    p.add_argument("--lint", action="store_true",
                   help="既存ゴールドの規約リント(API不要)。違反を lint_report.json へ")
    p.add_argument("--only-lint", action="store_true",
                   help="--relabel と併用: lint_report.json の違反itemのみ再ラベル")
    p.add_argument("--today", default=None,
                   help="基準日の上書き(YYYY-MM-DD)。既定: 既存label_today→実行日")
    p.add_argument("--augment", action="store_true",
                   help="的絞りデータ増強: AUGMENT_PATTERNSの新規analysis例をtrain/validにのみ追記(heldout不可侵)")
    a = p.parse_args()
    main(a.inp, a.out, a.heldout_ratio, a.n_analysis, a.n_generation, a.gen_chunks, a.chunk_chars,
         a.dry_run, a.relabel, a.lint, a.only_lint, a.today, a.augment)
