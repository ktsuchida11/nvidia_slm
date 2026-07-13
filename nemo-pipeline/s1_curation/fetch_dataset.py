"""金融データセットをHugging Faceから取得し、S1入力形式(/data/raw/*.jsonl)へ変換。

⚠ ライセンスは必ずHFのデータセットカードで確認し、--license に正確に渡すこと。
   S1のライセンスゲートが非商用(NC)・改変禁止(ND)を自動で弾く。

商用利用できる推奨データ(詳細は docs/03-accounts-and-datasets.md):
  - 官公庁の公開情報を自前収集 : 政府標準利用規約(CC BY 4.0互換)  --license gov-jp
  - EDINET / EDINET-Bench       : PDL 1.0（金融庁EDINETの公開開示）  --license pdl-1.0
  - 合成データ(自社生成)         : own / synthetic
  - 既製の商用可データ           : Apache-2.0 / MIT / CC-BY / CC-BY-SA をカードで確認
避けるもの(商用不可):
  - JaFIn (CC BY-NC-SA), financial-lakehouse(商用禁止), OCR_Task_JA(NC) など

使い方:
  pip install huggingface_hub datasets
  python fetch_dataset.py --repo <HFのdataset id> --license <カード記載> --out /data/raw
"""
import argparse, json, pathlib

def main(repo: str, license_: str, out: str, limit: int, config: str | None = None):
    from datasets import get_dataset_split_names, load_dataset  # pip install datasets
    try:
        ds = load_dataset(repo, config, split="train")
    except ValueError:
        splits = get_dataset_split_names(repo, config)
        print(f"⚠ split 'train' なし → '{splits[0]}' を使用 (候補: {splits})")
        ds = load_dataset(repo, config, split=splits[0])
    out_p = pathlib.Path(out); out_p.mkdir(parents=True, exist_ok=True)
    n = 0
    safe = repo.replace("/", "__") + (f"__{config}" if config else "")
    with (out_p / f"{safe}.jsonl").open("w", encoding="utf-8") as w:
        for row in ds:
            # 代表的なキーに対応（実カラム名はデータセットカードで確認）
            text = "\n".join(str(row[k]) for k in ("instruction", "input", "output", "text", "question", "answer") if row.get(k))
            if not text.strip():
                text = json.dumps(row, ensure_ascii=False)
            w.write(json.dumps({"text": text,
                                "meta": {"license": license_, "source": repo}},
                               ensure_ascii=False) + "\n")
            n += 1
            if limit and n >= limit: break
    print(f"wrote {n} docs -> {out_p/(safe+'.jsonl')} (license={license_})")
    print("⚠ このlicense表記がS1ゲートを通るか確認: NC/NDは弾かれる（商用不可のため）")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--license", dest="license_", required=True,
                   help="HFカードの表記を正確に (例: apache-2.0 / cc-by-4.0 / cc-by-sa-4.0 / pdl-1.0 / gov-jp)")
    p.add_argument("--out", default="/data/raw")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--config", default=None, help="データセットのconfig名（必要な場合のみ。例: EDINET-Benchのearnings_forecast）")
    a = p.parse_args(); main(a.repo, a.license_, a.out, a.limit, a.config)
