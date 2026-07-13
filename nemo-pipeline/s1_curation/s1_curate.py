"""S1 データ収集・管理 — 実装版
生テキスト(官公庁公開/EDINET/合成など商用可データ)を 正規化→品質フィルタ→PIIマスク→ライセンスゲート→
exact/fuzzy重複排除→メタデータ付与 して /data/curated へ。

エンジン:
  --engine python  : 純Python(既定)。数万件規模までCPUで完結、どのコンテナでも動く。
  --engine curator : NeMo Curator が import できれば exact dedup を委譲(APIドリフト時は自動fallback)。
                     ※ fuzzy(MinHash)のGPU/Dask構成は大規模時にCuratorドキュメントに従って有効化。

入力:  /data/raw/*.jsonl ({"text":..., "meta":{...}})  /data/raw/*.txt (1ファイル=1文書)
出力:  /data/curated/curated.jsonl  /data/curated/rejected.jsonl  /data/curated/stats.json
"""
from __future__ import annotations
import argparse, hashlib, json, logging, pathlib, re, sys, unicodedata
from collections import Counter

logging.basicConfig(level=logging.INFO, format="[curate] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---- 設定 -------------------------------------------------------------------
# 商用利用可のみ通す。NC(非商用)/ND(改変禁止)は明示的に除外。
LICENSE_EXPLICIT_OK = {"own", "synthetic", "mit", "cc0", "pdl-1.0", "gov-jp"}

def license_ok(lic: str) -> bool:
    lic = (lic or "own").lower().strip()
    if "nc" in lic or "-nd" in lic or lic.endswith("nd"):
        return False                       # 非商用・改変禁止は商用パイプライン不可
    if lic in LICENSE_EXPLICIT_OK:
        return True
    if lic.startswith("apache") or lic.startswith("cc-by") or lic.startswith("cc0"):
        return True                        # cc-by / cc-by-sa / cc-by-4.0 等（NCは上で除外済）
    return False
# 金融ドメイン判定用の汎用キーワード（対象ドメインに合わせて調整可）
FIN_KEYWORDS = ("市場","相場","先物","価格","需給","ヘッジ","金利","為替","金融","商品",
                "エネルギー","金属","穀物","指標","見通し","リスク","投資","取引")
MIN_CHARS, MAX_CHARS = 20, 20000
NGRAM, NUM_PERM, FUZZY_THRESHOLD = 4, 64, 0.75  # 実測校正: 近似重複J≈0.82を捕捉、独立文書はJ≈0

# 区切り文字(ハイフン/括弧/スペース)を必須にしている: 財務諸表は数値セルが連結した
# 長い数字列を含み、区切り任意のパターンだと金額を大量誤マスクして本文を破壊するため
# (EDINET実データで実測: PHONE_JP誤検知4,669件)。
# 平文連番PII(区切りなしマイナンバー等)が想定される独自データでは presidio 併用を推奨
# (local-rag-llm/templates の presidio-analyzer/anonymizer)。
PII_PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("PHONE_JP", re.compile(r"(?<!\d)(0\d{1,4}[-(]\d{1,4}[-)]\d{3,4})(?!\d)")),
    ("MYNUMBER", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")),
    ("CREDIT", re.compile(r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)")),
]

# ---- 基本処理 ----------------------------------------------------------------
def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def mask_pii(text: str) -> tuple[str, Counter]:
    hits: Counter = Counter()
    for name, pat in PII_PATTERNS:
        text, n = pat.subn(f"[{name}]", text)
        if n: hits[name] += n
    return text, hits

def quality(text: str) -> tuple[float, list[str]]:
    """0-1の簡易品質スコア。metaに残し、S2の層別サンプリングで利用する。"""
    reasons, score = [], 1.0
    n = len(text)
    if n < MIN_CHARS: return 0.0, ["too_short"]
    if n > MAX_CHARS: reasons.append("too_long"); score -= 0.3
    jp = sum(1 for c in text if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    if jp / n < 0.2: reasons.append("low_japanese"); score -= 0.4
    sym = sum(1 for c in text if not c.isalnum() and not c.isspace()) / n
    if sym > 0.35: reasons.append("symbol_heavy"); score -= 0.3
    lines = [l for l in text.splitlines() if l.strip()]
    if lines and len(set(lines)) / len(lines) < 0.5: reasons.append("repetitive"); score -= 0.3
    return max(score, 0.0), reasons

def classify_domain(text: str) -> str:
    return "finance" if sum(text.count(k) for k in FIN_KEYWORDS) >= 2 else "general"

# ---- 重複排除 ----------------------------------------------------------------
def exact_key(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", "", text).encode()).hexdigest()

def minhash(text: str) -> list[int]:
    grams = {text[i:i+NGRAM] for i in range(max(len(text) - NGRAM + 1, 1))}
    sig = []
    for p in range(NUM_PERM):
        sig.append(min(int.from_bytes(hashlib.blake2b(f"{p}:{g}".encode(), digest_size=8).digest(), "big")
                       for g in grams))
    return sig

def fuzzy_dedup(docs: list[dict]) -> tuple[list[dict], int]:
    """品質降順で走査し、既採用とJaccard推定>=閾値なら落とす。LSHバンドで候補を絞る。"""
    docs = sorted(docs, key=lambda d: -d["meta"]["quality"])
    bands: dict[tuple, list[int]] = {}
    kept, dropped = [], 0
    BAND = 8  # 64perm / 8 = 8バンド
    for d in docs:
        sig = d.pop("_sig")
        cand = set()
        keys = [tuple(sig[i:i+BAND]) + (i,) for i in range(0, NUM_PERM, BAND)]
        for k in keys: cand.update(bands.get(k, []))
        dup = any(sum(a == b for a, b in zip(sig, kept[j]["_sig2"])) / NUM_PERM >= FUZZY_THRESHOLD
                  for j in cand)
        if dup:
            dropped += 1; continue
        d["_sig2"] = sig
        idx = len(kept); kept.append(d)
        for k in keys: bands.setdefault(k, []).append(idx)
    for d in kept: d.pop("_sig2")
    return kept, dropped

def curator_exact_dedup(docs: list[dict]) -> list[dict] | None:
    """NeMo Curator委譲(利用可能な場合)。APIドリフト時はNoneを返してfallback。"""
    try:
        import pandas as pd
        from nemo_curator.datasets import DocumentDataset  # type: ignore
        from nemo_curator.modules import ExactDuplicates   # type: ignore
        import dask.dataframe as dd
        df = dd.from_pandas(pd.DataFrame({"id": range(len(docs)),
                                          "text": [d["text"] for d in docs]}), npartitions=1)
        dups = ExactDuplicates(id_field="id", text_field="text")(DocumentDataset(df))
        dup_ids = set(dups.df["id"].compute().tolist()) if dups is not None else set()
        log.info("curator exact-dedup: %d dup rows", len(dup_ids))
        return [d for i, d in enumerate(docs) if i not in dup_ids]
    except Exception as e:  # ImportError含む
        log.warning("curator engine unavailable (%s) → python fallback", e)
        return None

# ---- I/O ---------------------------------------------------------------------
def load_docs(inp: pathlib.Path):
    for f in sorted(inp.glob("*.jsonl")):
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines()):
            if not line.strip(): continue
            try:
                o = json.loads(line)
                meta = o.get("meta", {})
                meta.setdefault("source", f"{f.name}#{i}")   # 元文書のsourceを維持（来歴保全）
                yield {"text": o.get("text", ""), "meta": meta}
            except json.JSONDecodeError:
                log.warning("bad json: %s#%d", f.name, i)
    for f in sorted(inp.glob("*.txt")):
        yield {"text": f.read_text(encoding="utf-8"), "meta": {"source": f.name}}

def main(inp: str, out: str, engine: str) -> None:
    inp_p, out_p = pathlib.Path(inp), pathlib.Path(out)
    out_p.mkdir(parents=True, exist_ok=True)
    kept, rejected, pii_total = [], [], Counter()
    seen: set[str] = set()

    for doc in load_docs(inp_p):
        text = normalize(doc["text"])
        if doc["meta"].get("provenance", {}).get("extraction") == "skipped":
            # 図/スキャンの未処理マーカーは学習系に流さない（NeMo RetrieverでOCR/VLM処理後に再投入）
            rejected.append({**doc, "reject_reason": "needs_extraction"}); continue
        lic = str(doc["meta"].get("license", "own")).lower()   # 自前/合成データ既定=own
        if not license_ok(lic):                                # NC/ND/unknown はここで遮断（商用可のみ）
            rejected.append({**doc, "reject_reason": f"license:{lic}"}); continue
        score, reasons = quality(text)
        if score <= 0:
            rejected.append({**doc, "reject_reason": ",".join(reasons)}); continue
        text, hits = mask_pii(text); pii_total.update(hits)
        k = exact_key(text)
        if k in seen:
            rejected.append({**doc, "reject_reason": "exact_dup"}); continue
        seen.add(k)
        kept.append({"text": text,
                     "meta": {**doc["meta"], "license": lic, "quality": round(score, 2),
                              "domain": classify_domain(text), "quality_flags": reasons},
                     "_sig": minhash(text)})

    if engine == "curator":
        res = curator_exact_dedup([{k: v for k, v in d.items() if k != "_sig"} for d in kept])
        if res is not None:
            sig_map = {exact_key(d["text"]): d["_sig"] for d in kept}
            kept = [{**d, "_sig": sig_map[exact_key(d["text"])]} for d in res]

    kept, fuzzy_dropped = fuzzy_dedup(kept)

    with (out_p / "curated.jsonl").open("w", encoding="utf-8") as w:
        for d in kept: w.write(json.dumps(d, ensure_ascii=False) + "\n")
    with (out_p / "rejected.jsonl").open("w", encoding="utf-8") as w:
        for d in rejected: w.write(json.dumps(d, ensure_ascii=False) + "\n")
    stats = {"kept": len(kept), "rejected": len(rejected), "fuzzy_dropped": fuzzy_dropped,
             "pii_masked": dict(pii_total),
             "by_domain": dict(Counter(d["meta"]["domain"] for d in kept))}
    (out_p / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("done: %s", stats)
    if not kept:
        log.error("no documents kept — check /data/raw"); sys.exit(1)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--engine", choices=["python", "curator"], default="python")
    a = p.parse_args(); main(a.inp, a.out, a.engine)
