"""S2 finqa — 汎用金融QAデータセットから SFT/GRPO/評価セットを構築(ループ9)。

入力: make fetch の出力(data/raw/*.jsonl)。QA構造保持には fetch の --keep-fields を使う
(例: --keep-fields user,assistant)。無い場合も代表キー(question/answer等)へフォールバック。

段階:
  --filter : ヒューリスティック(日本語率・長さ・重複) → 教師judge(既定claude-haiku-4-5、
             10件/呼び出し)で品質選別 → data/finqa/filtered.jsonl
             (Finance-Instruct-500k-JapaneseはGPT-4o-mini機械翻訳のため品質選別は必須)
  --build  : filtered → 数値答え候補 → 教師verify(一意な数値答えの検証・正規化。既定
             claude-sonnet-4-6) → GRPO/SFT/heldout の3分割 + リーク検査 → data/finqa/*.jsonl
  --dry-run: API無しで配管検証(judgeは全通過、verifyはルール抽出)

ループ8の教訓(必須設計):
  - GRPO訓練セットはSFT訓練セットと排他にする(同一だと暗記済み=advantage 0で無学習)
  - judge/verify結果は逐次 *_log.jsonl に追記し、中断後の再実行はログを再利用(API課金保護)

出力(--out 配下):
  filtered.jsonl                       {"q","a","meta"}              品質選別済みプール
  sft_finqa_{train,valid}.jsonl        {"input","output"}            NeMo-RL ResponseDataset形式
  grpo_finqa_train.jsonl               {"input","ground_truth"}      gt={"finqa":{value,unit,tolerance}}
  heldout_finqa.jsonl                  s6_eval形式(meta.task=finqa)  評価専用・学習不使用
  prompts/finqa_sys.txt, finqa_chat_sys.txt
  finqa_stats.json
"""
from __future__ import annotations
import argparse, hashlib, json, logging, os, pathlib, random, re, sys, time, unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "common"))
from finqa import parse_valued_number   # noqa: E402
from prompts import FINQA_SYS, FINQA_CHAT_SYS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[finqa] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

FILTER_MODEL = os.environ.get("FILTER_MODEL", "claude-haiku-4-5")
VERIFY_MODEL = os.environ.get("TEACHER_MODEL", "claude-sonnet-4-6")
Q_KEYS = ("user", "question", "instruction", "input")
A_KEYS = ("assistant", "answer", "output", "response")
SEED = 9

JUDGE_SYS = ("あなたは学習データの品質判定係。日本語の金融QAペアを判定する。"
             "keep=true の条件: (1)日本語として自然(機械翻訳の破綻・英語混入過多がない) "
             "(2)質問と回答が整合している (3)金融・経済・会計の内容である。"
             '出力はJSON配列のみ: [{"i":0,"keep":true}, ...]')

VERIFY_SYS = ("あなたは検証可能QAの正解正規化係。各QAペアについて、質問への最終的な答えが"
              "回答文から一意の数値として定まるかを判定し、定まる場合は正規化する。"
              "value は倍率適用済みの素の数値(例: 12.3億円→12300000000、12.5%→12.5)。"
              "unit は 円|ドル|%|倍|その他 のいずれか、無次元なら null。"
              "tolerance は相対誤差の満点閾値: 正確な値なら0.005、「約」等の概数なら0.02。"
              "答えが複数の数値・範囲・数値以外・回答から定まらない場合は ok=false。"
              '出力はJSON配列のみ: [{"i":0,"ok":true,"value":12300000000,"unit":"円","tolerance":0.005}, '
              '{"i":1,"ok":false}, ...]')


# ---- 教師API(s2_distill.pyと同じretry流儀) -----------------------------------
def call_teacher(client, model: str, system: str, user: str, max_tokens: int = 1000) -> str:
    for attempt in range(3):
        try:
            r = client.messages.create(model=model, max_tokens=max_tokens,
                                       system=system, messages=[{"role": "user", "content": user}])
            return r.content[0].text
        except Exception as e:
            wait = 2 ** attempt
            log.warning("teacher error (%s) retry in %ds", e, wait); time.sleep(wait)
    raise RuntimeError("teacher failed after retries")


def extract_json_array(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.S)
    if not m: raise ValueError("no json array in teacher output")
    return json.loads(m.group(0))


# ---- 正規化・ヒューリスティック ----------------------------------------------
def norm_q(q: str) -> str:
    """重複排除・リーク検査用の質問正規化(3分割の排他判定はすべてこの関数を通す)"""
    return re.sub(r"[\s。、．，!?！?？]", "", unicodedata.normalize("NFKC", q))


def qhash(q: str) -> str:
    return hashlib.sha1(norm_q(q).encode()).hexdigest()[:16]


def jp_ratio(s: str) -> float:
    if not s: return 0.0
    jp = sum(1 for c in s if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    return jp / len(s)


def heuristic_reject(q: str, a: str) -> str | None:
    if not q.strip() or not a.strip(): return "empty"
    if not (5 <= len(q) <= 500): return "q_len"
    if not (5 <= len(a) <= 2000): return "a_len"
    if jp_ratio(a) < 0.25: return "not_japanese"   # 機械翻訳の英語残り
    return None


def extract_qa(rec: dict) -> tuple[str, str] | None:
    src = rec.get("fields") or rec
    q = next((str(src[k]) for k in Q_KEYS if src.get(k)), "")
    a = next((str(src[k]) for k in A_KEYS if src.get(k)), "")
    return (q, a) if q and a else None


# ---- 逐次ログ(中断・再実行時のAPI課金保護) -----------------------------------
def load_log(path: pathlib.Path) -> dict[str, dict]:
    if not path.exists(): return {}
    return {r["h"]: r for line in open(path, encoding="utf-8")
            if line.strip() and (r := json.loads(line))}


# ---- --filter ----------------------------------------------------------------
def run_filter(a, client) -> None:
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    raw_files = sorted(pathlib.Path(a.src).glob(a.pattern))
    if not raw_files:
        log.error("入力なし: %s/%s — 先に make fetch (--keep-fields 推奨)", a.src, a.pattern); sys.exit(1)
    log.info("入力: %s", [f.name for f in raw_files])

    pool, seen, rejects = [], set(), {}
    for f in raw_files:
        for line in open(f, encoding="utf-8"):
            if not line.strip(): continue
            rec = json.loads(line)
            qa = extract_qa(rec)
            if not qa:
                rejects["no_qa"] = rejects.get("no_qa", 0) + 1; continue
            q, ans = qa
            why = heuristic_reject(q, ans)
            if why:
                rejects[why] = rejects.get(why, 0) + 1; continue
            h = qhash(q)
            if h in seen:
                rejects["dup"] = rejects.get("dup", 0) + 1; continue
            seen.add(h)
            pool.append({"h": h, "q": q, "a": ans, "meta": rec.get("meta", {})})
    log.info("ヒューリスティック通過 %d件 (棄却 %s)", len(pool), rejects)

    judge_log = out / "judge_log.jsonl"
    done = load_log(judge_log)
    target = a.target
    kept = []
    if a.dry_run:
        kept = pool[:target]
    else:
        est = (len(pool) - len(done)) / 10 * 0.7 / 1000 * 5  # 10件/呼び出し・~0.7Ktok・Haiku級$5/Mtok想定
        log.info("教師judge対象 ~%d件 (既judge %d件再利用) 概算 ~$%.1f ※実測で要確認",
                 len(pool) - len(done), len(done), est)
        with open(judge_log, "a", encoding="utf-8") as jl:
            for i in range(0, len(pool), 10):
                if len(kept) >= target: break
                batch = pool[i:i + 10]
                todo = [b for b in batch if b["h"] not in done]
                if todo:
                    user = "\n\n".join(f"### {j}\n質問: {b['q'][:300]}\n回答: {b['a'][:600]}"
                                       for j, b in enumerate(todo))
                    try:
                        verdicts = {v["i"]: bool(v.get("keep")) for v in extract_json_array(
                            call_teacher(client, FILTER_MODEL, JUDGE_SYS, user, 500))}
                    except (ValueError, json.JSONDecodeError) as e:
                        log.warning("judge応答パース失敗(バッチ棄却): %s", e); verdicts = {}
                    for j, b in enumerate(todo):
                        r = {"h": b["h"], "keep": verdicts.get(j, False)}
                        done[b["h"]] = r
                        jl.write(json.dumps(r) + "\n")
                    jl.flush()
                kept += [b for b in batch if done.get(b["h"], {}).get("keep")]
                if (i // 10) % 50 == 0:
                    log.info("judge進捗 %d/%d 採用%d", i + len(batch), len(pool), len(kept))
        kept = kept[:target]

    with (out / "filtered.jsonl").open("w", encoding="utf-8") as w:
        for b in kept:
            w.write(json.dumps(b, ensure_ascii=False) + "\n")
    stats = {"stage": "filter", "raw": sum(rejects.values()) + len(pool), "heuristic_pass": len(pool),
             "heuristic_rejects": rejects, "judged": len(done),
             "judge_keep_rate": round(sum(1 for r in done.values() if r.get("keep")) / len(done), 3) if done else None,
             "filtered": len(kept), "dry_run": a.dry_run}
    (out / "finqa_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("filtered %d件 -> %s", len(kept), out / "filtered.jsonl")


# ---- --build -----------------------------------------------------------------
def verify_numeric(a, client, cands: list[dict], need: int) -> list[dict]:
    """教師で数値答えの一意性を検証し正規化。verified(verify付きレコード)を返す。"""
    out = pathlib.Path(a.out)
    done = load_log(out / "verify_log.jsonl")
    verified = [dict(c, verify={k: done[c["h"]][k] for k in ("value", "unit", "tolerance")})
                for c in cands if done.get(c["h"], {}).get("ok")]
    todo = [c for c in cands if c["h"] not in done]
    if a.dry_run:
        for c in todo:
            got = parse_valued_number(c["a"])
            if got:
                verified.append(dict(c, verify={"value": got[0], "unit": got[1], "tolerance": 0.005}))
            if len(verified) >= need: break
        return verified[:need]
    est = min(len(todo), max(0, (need - len(verified)) * 2)) / 5 * 1.2 / 1000 * 18  # 5件/呼・~1.2Ktok・Sonnet級
    log.info("教師verify対象 最大~%d件 (既verify %d件再利用) 概算 ~$%.1f ※実測で要確認",
             len(todo), len(done), est)
    with (out / "verify_log.jsonl").open("a", encoding="utf-8") as vl:
        for i in range(0, len(todo), 5):
            if len(verified) >= need: break
            batch = todo[i:i + 5]
            user = "\n\n".join(f"### {j}\n質問: {c['q'][:300]}\n回答: {c['a'][:800]}"
                               for j, c in enumerate(batch))
            try:
                results = {v["i"]: v for v in extract_json_array(
                    call_teacher(client, VERIFY_MODEL, VERIFY_SYS, user, 800))}
            except (ValueError, json.JSONDecodeError) as e:
                log.warning("verify応答パース失敗(バッチ棄却): %s", e); results = {}
            for j, c in enumerate(batch):
                v = results.get(j, {})
                ok = bool(v.get("ok")) and isinstance(v.get("value"), (int, float))
                r = {"h": c["h"], "ok": ok}
                if ok:
                    tol = min(max(float(v.get("tolerance", 0.005)), 0.001), 0.05)
                    r.update(value=float(v["value"]), unit=v.get("unit"), tolerance=tol)
                    verified.append(dict(c, verify={"value": r["value"], "unit": r["unit"],
                                                    "tolerance": tol}))
                done[c["h"]] = r
                vl.write(json.dumps(r, ensure_ascii=False) + "\n")
            vl.flush()
            if (i // 5) % 50 == 0:
                log.info("verify進捗 %d/%d 検証済%d/%d", i + len(batch), len(todo), len(verified), need)
    return verified[:need]


def run_build(a, client) -> None:
    out = pathlib.Path(a.out)
    fp = out / "filtered.jsonl"
    if not fp.exists():
        log.error("filtered.jsonl なし — 先に --filter"); sys.exit(1)
    pool = [json.loads(l) for l in open(fp, encoding="utf-8") if l.strip()]
    rng = random.Random(SEED)
    rng.shuffle(pool)

    # 数値答え候補(回答に単位・倍率付きの数値があるもの)を教師verifyへ。目標 = GRPO + heldout
    cands = [c for c in pool if parse_valued_number(c["a"])]
    log.info("数値答え候補 %d/%d件", len(cands), len(pool))
    need = a.n_grpo + a.n_heldout
    verified = verify_numeric(a, client, cands, need)
    if len(verified) < need:
        log.warning("verify不足: %d/%d — n-grpo/n-heldoutを下げるか候補を増やす(fetch LIMIT拡大 or "
                    "EDINET-Bench数値からの教師生成を検討)", len(verified), need)
    rng.shuffle(verified)
    heldout = verified[:a.n_heldout]
    grpo = verified[a.n_heldout:a.n_heldout + a.n_grpo]

    # SFT = GRPO/heldoutに使った質問を除外(ループ8欠陥1: SFTと同一プロンプトのGRPOは無学習)
    used = {c["h"] for c in heldout} | {c["h"] for c in grpo}
    sft_pool = [c for c in pool if c["h"] not in used]
    sft = sft_pool[:a.n_sft]
    n_valid = min(max(50, int(len(sft) * 0.02)), max(1, len(sft) // 10))
    sft_valid, sft_train = sft[:n_valid], sft[n_valid:]

    # リーク検査: 3セットの正規化質問は互いに排他(違反はビルド失敗)
    sets = {"sft": {norm_q(c["q"]) for c in sft}, "grpo": {norm_q(c["q"]) for c in grpo},
            "heldout": {norm_q(c["q"]) for c in heldout}}
    for x, y in (("sft", "grpo"), ("sft", "heldout"), ("grpo", "heldout")):
        leak = sets[x] & sets[y]
        if leak:
            log.error("リーク検出 %s∩%s=%d件 (例: %s)", x, y, len(leak), list(leak)[:2]); sys.exit(1)

    (out / "prompts").mkdir(parents=True, exist_ok=True)
    (out / "prompts" / "finqa_sys.txt").write_text(FINQA_SYS, encoding="utf-8")
    (out / "prompts" / "finqa_chat_sys.txt").write_text(FINQA_CHAT_SYS, encoding="utf-8")
    with (out / "sft_finqa_train.jsonl").open("w", encoding="utf-8") as w:
        for c in sft_train:
            w.write(json.dumps({"input": c["q"], "output": c["a"]}, ensure_ascii=False) + "\n")
    with (out / "sft_finqa_valid.jsonl").open("w", encoding="utf-8") as w:
        for c in sft_valid:
            w.write(json.dumps({"input": c["q"], "output": c["a"]}, ensure_ascii=False) + "\n")
    with (out / "grpo_finqa_train.jsonl").open("w", encoding="utf-8") as w:
        for c in grpo:
            w.write(json.dumps({"input": c["q"],
                                "ground_truth": json.dumps({"finqa": c["verify"]}, ensure_ascii=False)},
                               ensure_ascii=False) + "\n")
    with (out / "heldout_finqa.jsonl").open("w", encoding="utf-8") as w:
        for c in heldout:
            w.write(json.dumps({"input": c["q"], "label": c["a"],
                                "meta": {"task": "finqa", "category": "numeric",
                                         "verify": c["verify"]}}, ensure_ascii=False) + "\n")
    stats = json.loads((out / "finqa_stats.json").read_text(encoding="utf-8")) \
        if (out / "finqa_stats.json").exists() else {}
    stats.update(stage="build", numeric_candidates=len(cands), verified=len(verified),
                 sft_train=len(sft_train), sft_valid=len(sft_valid),
                 grpo=len(grpo), heldout=len(heldout), dry_run=a.dry_run, seed=SEED)
    (out / "finqa_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("build done: %s", {k: stats[k] for k in ("sft_train", "sft_valid", "grpo", "heldout")})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/data/raw")
    ap.add_argument("--pattern", default="*Finance-Instruct*.jsonl", help="rawファイルのglob")
    ap.add_argument("--out", default="/data/finqa")
    ap.add_argument("--filter", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--target", type=int, default=14000, help="filterの採用目標(SFT1万+GRPO候補余裕)")
    ap.add_argument("--n-sft", type=int, default=10000)
    ap.add_argument("--n-grpo", type=int, default=2500)
    ap.add_argument("--n-heldout", type=int, default=300)
    a = ap.parse_args()
    client = None
    if not a.dry_run:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            log.error("ANTHROPIC_API_KEY 未設定(--dry-run なら不要)"); sys.exit(1)
        import anthropic
        client = anthropic.Anthropic()
    if a.filter:
        run_filter(a, client)
    if a.build:
        run_build(a, client)
    if not (a.filter or a.build):
        log.error("--filter か --build を指定"); sys.exit(1)


if __name__ == "__main__":
    main()
