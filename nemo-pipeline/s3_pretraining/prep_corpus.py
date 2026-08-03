"""S3 DAPT用コーパス生成: S1出力(curated.jsonl) → 事前学習コーパス（loop10 P3で拡張）
処理:
  1. 除外ルール（従来どおり）:
     - provenance.extraction == "skipped"（図/スキャンの未処理マーカー）は除外
     - 資料(source)単位で結合後、min_chars 未満は除外
  2. リーク検査（must・docs/loop-10-plan.md §5）:
     - 既存評価 heldout（analysis/finqa）の文字シングルがコーパス文書に現れたら当該文書を除外
  3. 決定的3分割（sha1(source) 順・再実行しても同じ割当）:
     - corpus_probe.jsonl : knowledge-probe QA の種文書（学習に使わない・P5 でQA化）
     - corpus_val.jsonl   : held-out loss 測定用（学習に使わない）
     - corpus_train.jsonl : DAPT 学習本体
"""
import argparse, glob, hashlib, json, pathlib
from collections import OrderedDict

KEEP_MODALITIES = {"text", "speaker-notes", "table", "figure-caption", "ocr", None}
# 注: 未処理マーカーは modality ではなく provenance.extraction=="skipped" で除外する（本物のOCRテキストは採用）

SHINGLE, STRIDE = 30, 15   # リーク検査: 30字窓・15字ストライド（誤爆しにくい長さ・定型句は下で除外）


def build_docs(in_path: pathlib.Path, min_chars: int):
    """S1出力を資料(source)単位で結合。返り値: [(source, text)], 除外統計"""
    n = skipped_marker = 0
    docs: OrderedDict[str, list[str]] = OrderedDict()
    for line in in_path.open(encoding="utf-8"):
        n += 1
        d = json.loads(line)
        meta = d.get("meta", {})
        if meta.get("provenance", {}).get("extraction") == "skipped":
            skipped_marker += 1; continue              # 図/スキャンの未処理マーカー（S1でも隔離済のはず）
        if meta.get("modality") not in KEEP_MODALITIES:
            continue
        t = d.get("text", "").strip()
        if not t:
            continue
        key = meta.get("source") or f"_solo_{n}"
        docs.setdefault(key, []).append(t)
    joined = [(k, "\n\n".join(parts)) for k, parts in docs.items()]
    kept = [(k, t) for k, t in joined if len(t) >= min_chars]
    return kept, {"rows": n, "markers": skipped_marker, "short_docs": len(joined) - len(kept)}


def heldout_shingles(patterns: list[str]) -> tuple[set[str], list[str]]:
    """heldoutファイル群から文字シングル集合を作る。値は再帰的に文字列を回収"""
    def strings(v):
        if isinstance(v, str):
            return [v]
        if isinstance(v, dict):
            return [s for x in v.values() for s in strings(x)]
        if isinstance(v, list):
            return [s for x in v for s in strings(x)]
        return []
    shingles: set[str] = set()
    found: list[str] = []
    for pat in patterns:
        for fp in sorted(glob.glob(pat)):
            found.append(fp)
            for line in pathlib.Path(fp).open(encoding="utf-8"):
                if not line.strip():
                    continue
                for s in strings(json.loads(line)):
                    s = "".join(s.split())              # 空白差で取り逃さないよう正規化
                    if len(s) < SHINGLE:
                        continue
                    # ストライド窓 + 末尾窓（短い文字列でも末尾までカバー）
                    pos = set(range(0, len(s) - SHINGLE + 1, STRIDE)) | {len(s) - SHINGLE}
                    for i in pos:
                        shingles.add(s[i:i + SHINGLE])
    return shingles, found


def find_leaks(docs: list[tuple[str, str]], shingles: set[str]) -> set[str]:
    """コーパス文書に heldout シングルが現れる source 集合（空白正規化して比較）"""
    leaked = set()
    for key, text in docs:
        t = "".join(text.split())
        if any(sh in t for sh in shingles):
            leaked.add(key)
    return leaked


def split_docs(docs: list[tuple[str, str]], n_probe: int, n_val: int):
    """sha1(source)順の決定的分割。probe→val→trainの優先で割当"""
    ordered = sorted(docs, key=lambda kt: hashlib.sha1(kt[0].encode()).hexdigest())
    probe, val, train = ordered[:n_probe], ordered[n_probe:n_probe + n_val], ordered[n_probe + n_val:]
    return train, val, probe


def write(docs: list[tuple[str, str]], path: pathlib.Path) -> int:
    chars = 0
    with path.open("w", encoding="utf-8") as w:
        for key, text in docs:
            w.write(json.dumps({"text": text, "source": key}, ensure_ascii=False) + "\n")
            chars += len(text)
    return chars


def main(in_path: str, out_dir: str, min_chars: int, heldout_pats: list[str],
         n_probe: int, n_val: int) -> None:
    src = pathlib.Path(in_path)
    dst = pathlib.Path(out_dir); dst.mkdir(parents=True, exist_ok=True)

    docs, stats = build_docs(src, min_chars)
    shingles, files = heldout_shingles(heldout_pats)
    if not files:
        print(f"⚠ heldoutファイルが見つからない（{heldout_pats}）— リーク検査をスキップ。"
              "評価データのある環境で必ず再実行すること")
    leaked = find_leaks(docs, shingles) if shingles else set()
    docs = [(k, t) for k, t in docs if k not in leaked]

    train, val, probe = split_docs(docs, n_probe, n_val)
    c_train = write(train, dst / "corpus_train.jsonl")
    c_val = write(val, dst / "corpus_val.jsonl")
    c_probe = write(probe, dst / "corpus_probe.jsonl")

    print(f"leak-check: heldout {len(files)}ファイル・シングル{len(shingles):,} → 除外 {len(leaked)} 文書")
    print(f"corpus_train: {len(train):,} docs / {c_train:,} 字")
    print(f"corpus_val  : {len(val):,} docs / {c_val:,} 字（held-out loss用・学習不使用）")
    print(f"corpus_probe: {len(probe):,} docs / {c_probe:,} 字（knowledge-probe種文書・学習不使用）")
    print(f"除外: 未処理マーカー{stats['markers']} / 短文書{stats['short_docs']} / リーク{len(leaked)}")
    if c_train < 10_000_000:
        print("⚠ train が1千万字未満 — DAPT の効果が出にくい規模。収集の拡充を検討")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="/data/curated/curated.jsonl")
    ap.add_argument("--out", default="/data/pretrain")
    ap.add_argument("--min-chars", type=int, default=200)
    ap.add_argument("--heldout", default="/data/distilled/heldout*.jsonl,/data/finqa/*heldout*.jsonl",
                    help="リーク検査対象のCSVグロブ")
    ap.add_argument("--n-probe", type=int, default=50)
    ap.add_argument("--n-val", type=int, default=25)
    a = ap.parse_args()
    main(a.in_path, a.out, a.min_chars, [p.strip() for p in a.heldout.split(",") if p.strip()],
         a.n_probe, a.n_val)
