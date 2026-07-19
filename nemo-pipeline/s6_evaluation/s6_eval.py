"""S6 評価 — held-out を推論エンドポイント(OpenAI互換)に流し、検証可能指標を集計して合否判定。
- analysis: schema_valid / sectors・query_type・date_range 一致 / 全一致(analysis_match)
- generation: source_exists / citation_format
- --mode dry : エンドポイント無しで教師ラベルを正answerとみなし配管検証（全指標=満点になるはず）
- 合否: eval.yaml の thresholds と比較し、exit 0(合格)/2(不合格)
使い方(base測定の例): OPENAI_BASE_URL=http://llm-gen:8000/v1 python s6_eval.py --config eval.yaml --tag base
"""
from __future__ import annotations
import argparse, json, logging, os, pathlib, sys, time, urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "common"))
from reward import source_exists, citation_format
from schema import validate_analysis
from prompts import LABEL_SYS, ANSWER_SYS, format_today

logging.basicConfig(level=logging.INFO, format="[eval] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

def load_yaml(path: str) -> dict:
    import yaml
    return yaml.safe_load(open(path, encoding="utf-8"))

def chat(base_url: str, model: str, system: str, user: str, max_tokens: int = 700) -> str:
    # EVAL_SYS_PREFIX: モデル固有の制御語をシステムプロンプト先頭に注入する
    # （例: Nemotron-Nano-v2 は "/no_think" で思考トレースを抑止 — 思考で
    #   max_tokens を使い切り最終回答に到達しない事象への対策）
    prefix = os.getenv("EVAL_SYS_PREFIX", "")
    if prefix:
        system = f"{prefix}\n{system}"
    payload = {"model": model, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    # EVAL_CHAT_KWARGS: vLLMのchat_template_kwargsをJSONで注入する
    # （Nemotron-Nano-v2-Japanese は /no_think を無視するため
    #   '{"enable_thinking": false}' が唯一有効な思考抑止手段 — 実機検証済み）
    kwargs = os.getenv("EVAL_CHAT_KWARGS", "")
    if kwargs:
        payload["chat_template_kwargs"] = json.loads(kwargs)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY','sk-local')}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)["choices"][0]["message"]["content"]
        except Exception as e:
            wait = 2 ** attempt
            log.warning("endpoint error (%s) retry in %ds", e, wait); time.sleep(wait)
    raise RuntimeError("endpoint failed")

def strip_reasoning(text: str) -> str:
    """Nemotron等のreasoningモデル対策: 最終回答だけを残す安全網。
    vLLMのreasoning-parserを有効化していれば message.content は既に最終回答のみ。
    未使用でトレースが本文に混ざる場合に備え <think>...</think> 等を除去する。"""
    if not text:
        return text
    import re as _re
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.S | _re.I)
    text = _re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=_re.S | _re.I)
    # チャットテンプレートが<think>を開いた状態で生成を始めるモデル（Nemotron等）は
    # 本文に閉じタグしか現れない。最後の</think>より前をすべて思考として落とす
    if _re.search(r"</think>", text, _re.I):
        text = _re.split(r"</think>", text, flags=_re.I)[-1]
    return text.strip()

def extract_json(text: str) -> dict:
    text = strip_reasoning(text)
    import re
    m = re.search(r"\{.*\}", text, re.S)
    if not m: raise ValueError("no json")
    return json.loads(m.group(0))

def eval_analysis(item: dict, pred_raw: str) -> dict:
    m = {"schema_valid": 0.0, "sectors_match": 0.0, "query_type_match": 0.0,
         "date_range_match": 0.0, "analysis_match": 0.0}
    try:
        pred = validate_analysis(extract_json(pred_raw)); m["schema_valid"] = 1.0
    except (ValueError, json.JSONDecodeError):
        return m
    gold = item["label"]
    m["sectors_match"] = 1.0 if set(pred["sectors"]) == set(gold["sectors"]) else 0.0
    m["query_type_match"] = 1.0 if pred["query_type"] == gold["query_type"] else 0.0
    m["date_range_match"] = 1.0 if pred["date_range"] == gold["date_range"] else 0.0
    m["analysis_match"] = min(m["sectors_match"], m["query_type_match"], m["date_range_match"])
    return m

def eval_generation(item: dict, pred: str) -> dict:
    labels = [c["label"] for c in item["input"]["chunks"]]
    return {"source_exists": source_exists(pred, labels), "citation_format": citation_format(pred)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--heldout", default=None, help="既定: configのsuites[0].path")
    ap.add_argument("--out", default="/results")
    ap.add_argument("--tag", default="model", help="レポート名(base/sft/grpo等)")
    ap.add_argument("--mode", choices=["live", "dry"], default="live")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    cfg = load_yaml(a.config)
    heldout = a.heldout or cfg["suites"][0]["path"]
    thresholds = cfg["suites"][0].get("thresholds", {})
    base_url = os.getenv("OPENAI_BASE_URL", cfg.get("endpoint", {}).get("base_url", ""))
    model = cfg.get("endpoint", {}).get("model", "qwen3-gen")

    items = [json.loads(l) for l in open(heldout, encoding="utf-8") if l.strip()]
    if a.limit: items = items[: a.limit]

    # 基準日: ラベル付け時のtoday(meta.label_today)があればそれを使う。
    # ゴールドの相対日付(先週=7日前〜等)はラベル付け日基準のため、評価実行日と
    # ずれると date_range が全て不一致になる
    lt = {it["meta"].get("label_today") for it in items if it["meta"]["task"] == "analysis"}
    lt.discard(None)
    today = lt.pop() if len(lt) == 1 else time.strftime("%Y-%m-%d")
    if lt: log.warning("label_today が複数混在。実行日を基準日に使用")
    log.info("基準日(today)=%s", today)
    if a.mode == "live" and not base_url:
        log.error("OPENAI_BASE_URL が未設定(liveモード)"); sys.exit(1)

    sums, counts = {}, {}
    cat_sums: dict[str, dict] = {}
    details = []
    for idx, it in enumerate(items):
        task = it["meta"]["task"]
        if task == "analysis":
            pred = json.dumps(it["label"], ensure_ascii=False) if a.mode == "dry" else \
                   chat(base_url, model, LABEL_SYS.replace("{today}", format_today(today)), it["input"], 400)
            m = eval_analysis(it, pred)
        else:
            if a.mode == "dry":
                pred = it["label"]
            else:
                ctx = "\n---\n".join(f"[SOURCE: {c['label']}]\n{c['text']}" for c in it["input"]["chunks"])
                pred = chat(base_url, model, ANSWER_SYS, f"{ctx}\n\n質問: {it['input']['question']}", 900)
            m = eval_generation(it, strip_reasoning(pred) if isinstance(pred, str) else pred)
        for k, v in m.items():
            sums[k] = sums.get(k, 0.0) + v; counts[k] = counts.get(k, 0) + 1
        cat = f'{task}/{it["meta"].get("category", "?")}'
        cs = cat_sums.setdefault(cat, {"n": 0})
        cs["n"] += 1
        for k, v in m.items():
            cs[k] = cs.get(k, 0.0) + v
        # 失敗内訳分析用のper-item記録(ループ2で集計値しか残らず内訳不明だった教訓)
        details.append({"i": idx, "task": task, "category": it["meta"].get("category"),
                        "metrics": m,
                        "input": (it["input"] if task == "analysis" else it["input"]["question"]),
                        "gold": it["label"] if task == "analysis" else None,
                        "pred": pred if isinstance(pred, str) else None})

    means = {k: round(sums[k] / counts[k], 4) for k in sums}
    by_category = {c: {k: (v if k == "n" else round(v / cs["n"], 4)) for k, v in cs.items()}
                   for c, cs in cat_sums.items()}
    verdict = {k: (means.get(k, 0.0) >= th) for k, th in thresholds.items()}
    report = {"tag": a.tag, "mode": a.mode, "n": len(items), "metrics": means,
              "by_category": by_category,
              "thresholds": thresholds, "pass": all(verdict.values()) if verdict else None,
              "verdict": verdict}
    out_p = pathlib.Path(a.out); out_p.mkdir(parents=True, exist_ok=True)
    (out_p / f"eval_{a.tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_p / f"eval_{a.tag}_details.jsonl").open("w", encoding="utf-8") as w:
        for d in details:
            w.write(json.dumps(d, ensure_ascii=False) + "\n")
    log.info("report: %s", json.dumps(report, ensure_ascii=False))
    sys.exit(0 if report["pass"] in (True, None) else 2)

if __name__ == "__main__":
    main()
