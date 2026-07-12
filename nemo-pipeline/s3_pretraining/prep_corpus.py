"""S3 DAPT用コーパス生成: S1出力(curated.jsonl) → 事前学習コーパス(corpus.jsonl)
除外ルール:
  - provenance.extraction == "skipped"（図/スキャンの未処理マーカー）は除外
  - 資料(source)単位で結合後、200字未満は除外
  - text / speaker-notes / table(Markdown) / figure-caption / ocr（API・Retrieverで処理済みのもの）を採用
"""
import argparse, json, pathlib

KEEP_MODALITIES = {"text", "speaker-notes", "table", "figure-caption", "ocr", None}
# 注: 未処理マーカーは modality ではなく provenance.extraction=="skipped" で除外する（本物のOCRテキストは採用）

def main(in_path: str, out_dir: str, min_chars: int):
    src = pathlib.Path(in_path)
    dst = pathlib.Path(out_dir); dst.mkdir(parents=True, exist_ok=True)
    n = kept = skipped_marker = 0
    chars = 0
    # ★資料(source)単位で結合してから長さ判定（スライド1枚は短くても、デッキ全体なら価値がある）
    from collections import OrderedDict
    docs = OrderedDict()
    for line in src.open(encoding="utf-8"):
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
    with (dst / "corpus.jsonl").open("w", encoding="utf-8") as w:
        for key, parts in docs.items():
            t = "\n\n".join(parts)
            if len(t) < min_chars:
                continue                                # 資料単位でも短ければDAPTに寄与しない
            w.write(json.dumps({"text": t, "source": key}, ensure_ascii=False) + "\n")
            kept += 1; chars += len(t)
    print(f"corpus: {kept}/{n} docs -> {dst/'corpus.jsonl'} (markers excluded: {skipped_marker})")
    print(f"概算文字数: {chars:,} （目安: DAPTの意味が出るのは数千万字〜。少なければS4直行を推奨）")
    return kept

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="/data/curated/curated.jsonl")
    ap.add_argument("--out", default="/data/pretrain")
    ap.add_argument("--min-chars", type=int, default=200)
    a = ap.parse_args()
    main(a.in_path, a.out, a.min_chars)
