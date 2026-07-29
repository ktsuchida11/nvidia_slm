"""EDINET-Bench 全config の有価証券報告書本文を平文化して S1 入力形式へ（loop10 P1 Tier1）。

既存 fetch_dataset.py との違い（DAPT コーパス用の要件）:
  - row["text"] は {セクション名: {キー: 本文}} の JSON 文字列 → json.dumps のまま流すと
    エスケープ済み JSON が学習データになる。ここで平文（セクション見出し + 本文）へ展開する。
  - meta.source を EDINET-Bench/{edinet_code}/{doc_id} で一意化する。
    （全行同一 source だと prep_corpus の資料単位結合で全件が 1 文書に潰れる）
  - 3config（earnings_forecast / fraud_detection / industry_prediction）は同一有報を共有するため
    doc_id で横断 dedup する。

使い方:
  make fetch-edinet-bench            # 全config・全split
  make fetch-edinet-bench LIMIT=10   # 動作確認
"""
from __future__ import annotations
import argparse, json, pathlib

LICENSE = "pdl-1.0"          # 金融庁EDINETの公開開示（docs/03-accounts-and-datasets.md）
REPO = "SakanaAI/EDINET-Bench"
CONFIGS = ["earnings_forecast", "fraud_detection", "industry_prediction"]
META_HEADER_KEYS = ["会社名", "EDINETコード", "証券コード", "決算期", "提出日"]


def _collect_strings(v) -> list[str]:
    """ネストした dict/list から文字列を出現順に回収（内側キー構造は版により揺れるため汎用に歩く）"""
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    if isinstance(v, dict):
        return [s for x in v.values() for s in _collect_strings(x)]
    if isinstance(v, list):
        return [s for x in v for s in _collect_strings(x)]
    return []


def flatten_report(row: dict) -> dict | None:
    """1行(1有報) → {"text": 平文, "meta": {...}}。本文が空なら None。"""
    try:
        sections = json.loads(row.get("text") or "{}")
    except json.JSONDecodeError:
        sections = {}
    parts: list[str] = []
    try:
        info = json.loads(row.get("meta") or "{}")
    except json.JSONDecodeError:
        info = {}
    header = " / ".join(f"{k}: {info[k]}" for k in META_HEADER_KEYS
                        if str(info.get(k, "")).strip() not in ("", "－", "-"))
    if header:
        parts.append(header)
    for name, body in (sections.items() if isinstance(sections, dict) else []):
        texts = _collect_strings(body)
        if texts:
            parts.append(f"【{name}】\n" + "\n".join(texts))
    if len(parts) <= (1 if header else 0):        # 本文セクションが無い行は捨てる
        return None
    doc_id = row.get("doc_id") or "unknown"
    code = row.get("edinet_code") or "unknown"
    return {"text": "\n\n".join(parts),
            "meta": {"license": LICENSE, "source": f"EDINET-Bench/{code}/{doc_id}",
                     "doc_id": doc_id, "edinet_code": code,
                     "company": info.get("会社名"), "domain_hint": "finance"}}


def main(configs: list[str], out: str, limit: int) -> None:
    from datasets import get_dataset_split_names, load_dataset  # pip install datasets
    out_p = pathlib.Path(out); out_p.mkdir(parents=True, exist_ok=True)
    dst = out_p / "SakanaAI__EDINET-Bench__full.jsonl"
    seen: set[str] = set()
    n_rows = n_kept = n_dup = chars = 0
    with dst.open("w", encoding="utf-8") as w:
        for cfg in configs:
            for split in get_dataset_split_names(REPO, cfg):
                ds = load_dataset(REPO, cfg, split=split, streaming=True)
                for row in ds:
                    n_rows += 1
                    doc_id = row.get("doc_id")
                    if doc_id and doc_id in seen:
                        n_dup += 1; continue
                    rec = flatten_report(row)
                    if rec is None:
                        continue
                    if doc_id:
                        seen.add(doc_id)
                    rec["meta"]["bench_config"] = cfg
                    w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_kept += 1; chars += len(rec["text"])
                    if limit and n_kept >= limit:
                        break
                if limit and n_kept >= limit:
                    break
            print(f"[{cfg}] 累計 kept={n_kept} dup={n_dup} rows={n_rows}")
            if limit and n_kept >= limit:
                break
    print(f"wrote {n_kept} docs -> {dst} (doc_id dedup: {n_dup})")
    print(f"概算文字数: {chars:,}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--configs", default=",".join(CONFIGS), help="CSV（既定: 全3config）")
    p.add_argument("--out", default="/data/raw")
    p.add_argument("--limit", type=int, default=0, help="動作確認用の件数上限（doc単位）")
    a = p.parse_args()
    main([c.strip() for c in a.configs.split(",") if c.strip()], a.out, a.limit)
