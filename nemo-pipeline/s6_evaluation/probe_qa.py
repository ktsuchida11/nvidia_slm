"""loop10 P5: knowledge-probe QA — DAPTで注入した知識が取り出せるかの closed-book 検証。

probe種文書(corpus_probe.jsonl = DAPT学習コーパス**内**の追跡50文書)から教師で
検証可能な事実QAを生成し、base と dapt10 に**文書を見せずに**答えさせて比較する。
base は未見・dapt10 は学習済みなので、差分 = 重みに注入され取り出せる知識量。

使い方:
  生成(要 ANTHROPIC_API_KEY・~$3-8):  make probe-gen         (Mac から。LiteLLM経由可)
  評価(ノード・配信中のモデルに対して): make probe-eval TAG=dapt10
                                       make probe-eval TAG=base10

loop12 拡張(--rag): s8_retrieval のインデックスで検索した上位チャンクをプロンプトに
前置して open-book 評価する。closed-book(base=dapt10=0.0402)との差 = RAG の階段。
  RAG評価(ノード):   make rag-eval TAG=rag199
  配管検証(ローカル): make rag-eval-dry  (検索は実行・生成はgoldモック=正答率1.0が正常)
採点は完全ローカル(数値=相対誤差1%・文字列=正規化包含)。API不要。

loop14 拡張(--rerank): 検索を --rerank-k0 (既定50) で広く取り、s8_retrieval/reranker.py
（rerank NIM）で --rag-k 件に精密絞り込みしてからプロンプトに入れる。
  RAG+rerank評価(ノード): make rag-eval TAG=rag199_rr RERANK=1 RERANK_K0=50
  配管検証(ローカル):      make rag-rerank-dry
"""
from __future__ import annotations
import argparse, json, logging, os, pathlib, re, sys, unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "s2_distillation"))

logging.basicConfig(level=logging.INFO, format="[probe] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GEN_SYS = """あなたは日本の有価証券報告書から試験問題を作る出題者です。
与えられた抜粋から、この文書に固有で検証可能な事実質問を{n}問作成してください。

規約:
1. closed-book で答えられるよう、質問文に会社名（と分かれば年度・決算期）を必ず含める
2. 答えは短い事実のみ: 数値+単位（例: 1,485億円 / 12.3% / 27名）or 固有名詞（例: 名古屋市 / 塗料事業）
3. 抜粋に明記されている事実のみ。推測・計算が必要な問題は不可
4. 一般知識で答えられる問題（業界常識・有名な事実）は不可。この文書を読んだ者だけが答えられること
5. 出力は JSON 配列のみ: [{{"q": "...", "a": "..."}}, ...]"""

GEN_USER = """会社名: {company}
文書ID: {source}

--- 有価証券報告書 抜粋 ---
{text}
--- 抜粋ここまで ---

この文書に固有の事実質問を{n}問、JSON配列で出力してください。"""

EVAL_SYS = "日本語で簡潔に答えてください。答えの事実のみを短く出力し、説明は不要です。"

RAG_SYS = ("日本語で簡潔に答えてください。提示された参考資料から質問の答えとなる事実を"
           "探して答えてください。答えの事実のみを短く出力し、説明は不要です。")


def build_rag_user(q: str, hits: list[dict]) -> str:
    """検索チャンクを前置した open-book プロンプト（loop12）。"""
    ctx = "\n\n".join(f"【参考資料{i + 1}｜{h['source']}】\n{h['text']}"
                      for i, h in enumerate(hits))
    return f"{ctx}\n\n質問: {q}"


# ---- 採点（完全ローカル・純関数） --------------------------------------------
def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[\s。、．，「」『』()（）]", "", s).lower()


def score_answer(pred: str, gold: str) -> int:
    """数値goldは相対誤差1%以内で正解、非数値goldは正規化包含で判定。
    parse_number は数値なし時に None を返す（タプルではない）点に注意。"""
    from finqa import parse_number
    g = parse_number(gold)
    if g is not None and g[0] is not None:
        p = parse_number(pred)
        if p is None or p[0] is None:
            return 0
        denom = max(abs(g[0]), 1e-12)
        return 1 if abs(p[0] - g[0]) / denom <= 0.01 else 0
    ng, np_ = norm_text(gold), norm_text(pred)
    return 1 if ng and ng in np_ else 0


# ---- 生成（教師API） ---------------------------------------------------------
def run_generate(probe_path: str, out_path: str, n_per_doc: int, max_chars: int,
                 model: str) -> None:
    import anthropic
    from s2_finqa import call_teacher, extract_json_array
    client = anthropic.Anthropic()
    out_p = pathlib.Path(out_path); out_p.parent.mkdir(parents=True, exist_ok=True)
    done_sources = set()
    if out_p.exists():                        # 中断再開可（逐次ログ方式=loop9の教訓）
        for line in out_p.open(encoding="utf-8"):
            done_sources.add(json.loads(line)["source"])
        log.info("既存 %d sources を再利用（再開モード）", len(done_sources))
    n_docs = n_qa = 0
    with out_p.open("a", encoding="utf-8") as w:
        for line in pathlib.Path(probe_path).open(encoding="utf-8"):
            d = json.loads(line)
            source, text = d["source"], d["text"]
            if source in done_sources:
                continue
            company = text.split("/")[0].replace("会社名:", "").strip() if "会社名:" in text[:100] else ""
            try:
                raw = call_teacher(client, model, GEN_SYS.format(n=n_per_doc),
                                   GEN_USER.format(company=company, source=source,
                                                   text=text[:max_chars], n=n_per_doc),
                                   max_tokens=1500)
                qas = extract_json_array(raw)
            except Exception as e:
                log.warning("生成失敗 %s (%s) — スキップ", source, e); continue
            kept = 0
            for qa in qas:
                q, a = str(qa.get("q", "")).strip(), str(qa.get("a", "")).strip()
                if not q or not a or len(a) > 60:
                    continue
                w.write(json.dumps({"q": q, "a": a, "source": source},
                                   ensure_ascii=False) + "\n")
                kept += 1
            w.flush()
            n_docs += 1; n_qa += kept
            log.info("%s: %d問 (累計 %d docs / %d 問)", source, kept, n_docs, n_qa)
    log.info("done: %s", out_p)


# ---- 評価（配信モデルへ出題・ローカル採点。--rag で open-book） ---------------
def run_eval(qa_path: str, tag: str, out_dir: str, base_url: str, chat_kwargs: dict,
             max_tokens: int, rag_index: str = "", rag_k: int = 5,
             rerank_k0: int = 0, dry_run: bool = False) -> None:
    client = model = None
    if not dry_run:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=os.environ.get("OPENAI_API_KEY", "dummy"))
        model = client.models.list().data[0].id
        log.info("eval対象: %s (%s)", model, base_url)
    rows = [json.loads(l) for l in pathlib.Path(qa_path).open(encoding="utf-8") if l.strip()]

    hits_all = None
    gold_in_k0 = None
    rr = None
    if rag_index:                             # loop12: 検索を先に一括実行（埋め込みもバッチ）
        sys.path.insert(0, str(ROOT / "s8_retrieval"))
        from embedder import get_embedder
        from retrieve import Retriever
        retriever = Retriever(rag_index, get_embedder())
        k_search = rerank_k0 if rerank_k0 else rag_k
        hits_all = []
        for i in range(0, len(rows), 32):
            hits_all += retriever.search([r["q"] for r in rows[i:i + 32]], k=k_search)
        log.info("retrieval完了: %d問 × top%d (backend=%s)", len(rows), k_search,
                 retriever.meta["backend"])
        if rerank_k0:                         # loop14: 広い候補を rerank で rag_k に絞る
            from reranker import get_reranker
            rr = get_reranker()
            # rerank の天井の物差し: gold が k0 候補に入っていたか（入っていなければ回収不能）
            gold_in_k0 = [int(rows[i]["source"] in {h["source"] for h in hits_all[i]})
                          for i in range(len(rows))]
            hits_all = [rr.rerank(rows[i]["q"], hits_all[i], rag_k)
                        for i in range(len(rows))]
            log.info("rerank完了: top%d → top%d (backend=%s)", rerank_k0, rag_k, rr.name)

    n_ok = n_gold_ctx = 0
    details = []
    for i, qa in enumerate(rows):
        hits = hits_all[i] if hits_all is not None else []
        if dry_run:                           # 配管検証: 生成をgoldでモック(正答率1.0が正常)
            pred = qa["a"]
        else:
            r = client.chat.completions.create(
                model=model, temperature=0.0, max_tokens=max_tokens,
                messages=[{"role": "system", "content": RAG_SYS if hits else EVAL_SYS},
                          {"role": "user",
                           "content": build_rag_user(qa["q"], hits) if hits else qa["q"]}],
                extra_body={"chat_template_kwargs": chat_kwargs} if chat_kwargs else {})
            pred = (r.choices[0].message.content or "").strip()
        ok = score_answer(pred, qa["a"])
        n_ok += ok
        d = {**qa, "pred": pred, "ok": ok}
        if hits_all is not None:              # 誤答分析用: 検索ミスと読解ミスを切り分ける
            d["ctx_sources"] = [h["source"] for h in hits]
            d["gold_in_ctx"] = int(qa["source"] in d["ctx_sources"])
            n_gold_ctx += d["gold_in_ctx"]
            if gold_in_k0 is not None:
                d["gold_in_k0"] = gold_in_k0[i]
        details.append(d)
        if (i + 1) % 25 == 0:
            log.info("%d/%d 正答率 %.3f", i + 1, len(rows), n_ok / (i + 1))
    acc = n_ok / max(len(rows), 1)
    out_p = pathlib.Path(out_dir); out_p.mkdir(parents=True, exist_ok=True)
    report = {"tag": tag, "n": len(rows), "probe_acc": round(acc, 4)}
    if hits_all is not None:
        report |= {"rag_k": rag_k,
                   "gold_in_ctx_rate": round(n_gold_ctx / max(len(rows), 1), 4)}
    if gold_in_k0 is not None:
        report |= {"rerank_k0": rerank_k0, "rerank_backend": rr.name,
                   "gold_in_k0_rate": round(sum(gold_in_k0) / max(len(rows), 1), 4)}
    if dry_run:
        report["dry_run"] = True
    (out_p / f"eval_probe_{tag}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_p / f"eval_probe_{tag}_details.jsonl").open("w", encoding="utf-8") as w:
        for d in details:
            w.write(json.dumps(d, ensure_ascii=False) + "\n")
    log.info("report: %s", json.dumps(report, ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--eval", dest="eval_", action="store_true")
    ap.add_argument("--probe", default="/data/pretrain/corpus_probe.jsonl")
    ap.add_argument("--qa", default="/data/pretrain/probe_qa.jsonl")
    ap.add_argument("--out", default="/results")
    ap.add_argument("--tag", default="model")
    ap.add_argument("--n-per-doc", type=int, default=4)
    ap.add_argument("--max-chars", type=int, default=12000)
    ap.add_argument("--max-tokens", type=int, default=100)
    ap.add_argument("--rag", action="store_true", help="loop12: open-book(RAG)評価")
    ap.add_argument("--index", default="/data/retriever", help="s8_retrieval インデックス")
    ap.add_argument("--rag-k", type=int, default=5)
    ap.add_argument("--rerank", action="store_true", help="loop14: k0候補をrerankでrag-kに絞る")
    ap.add_argument("--rerank-k0", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true", help="生成をgoldでモック(配管検証)")
    a = ap.parse_args()
    if a.generate:
        run_generate(a.probe, a.qa, a.n_per_doc, a.max_chars,
                     os.environ.get("TEACHER_MODEL", "claude-sonnet-4-6"))
    if a.eval_:
        kwargs = json.loads(os.environ.get("EVAL_CHAT_KWARGS", "{}") or "{}")
        run_eval(a.qa, a.tag, a.out, os.environ.get("OPENAI_BASE_URL", ""), kwargs,
                 a.max_tokens, rag_index=a.index if a.rag else "", rag_k=a.rag_k,
                 rerank_k0=a.rerank_k0 if a.rerank else 0, dry_run=a.dry_run)
    if not (a.generate or a.eval_):
        log.error("--generate か --eval を指定"); sys.exit(1)


if __name__ == "__main__":
    main()
