"""S4/S5 学習データ準備 — distilled/{train,valid}.jsonl を NeMo-RL の ResponseDataset 形式へ変換。

出力（--out 配下）:
  sft_analysis_{train,valid}.jsonl   {"input": 質問文, "output": ラベルJSON文字列}
  sft_generation_{train,valid}.jsonl {"input": ctx+質問, "output": 出典付き回答}
  grpo_generation_train.jsonl        {"input": ctx+質問, "ground_truth": {"chunk_labels":[...]} のJSON}
  prompts/label_sys.txt, answer_sys.txt  （system_prompt_file 用。s6_eval と同一のプロンプト）

プロンプト描画は s6_evaluation/s6_eval.py と完全一致させる（学習と評価の整合が目的）:
  analysis:   system=LABEL_SYS({today}置換), user=質問文
  generation: system=ANSWER_SYS, user="[SOURCE: label]\\ntext..." + "\\n\\n質問: ..."
heldout.jsonl は絶対に変換しない（評価専用・不可侵）。
"""
import argparse, datetime, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "common"))
from prompts import ANSWER_SYS, LABEL_SYS  # noqa: E402


def render_ctx(chunks: list[dict]) -> str:
    return "\n---\n".join(f"[SOURCE: {c['label']}]\n{c['text']}" for c in chunks)


def convert(split_path: pathlib.Path, out_dir: pathlib.Path, split: str, with_grpo: bool) -> dict:
    stats = {"analysis": 0, "generation": 0, "skipped": 0}
    fa = open(out_dir / f"sft_analysis_{split}.jsonl", "w", encoding="utf-8")
    fg = open(out_dir / f"sft_generation_{split}.jsonl", "w", encoding="utf-8")
    fr = open(out_dir / "grpo_generation_train.jsonl", "w", encoding="utf-8") if with_grpo else None
    for line in open(split_path, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        task = rec.get("meta", {}).get("task")
        if task == "analysis":
            fa.write(json.dumps({"input": rec["input"],
                                 "output": json.dumps(rec["label"], ensure_ascii=False)},
                                ensure_ascii=False) + "\n")
            stats["analysis"] += 1
        elif task == "generation":
            q = rec["input"]["question"]
            chunks = rec["input"]["chunks"]
            user = f"{render_ctx(chunks)}\n\n質問: {q}"
            fg.write(json.dumps({"input": user, "output": rec["label"]}, ensure_ascii=False) + "\n")
            if fr is not None:
                labels = [c["label"] for c in chunks]
                fr.write(json.dumps({"input": user,
                                     "ground_truth": json.dumps({"chunk_labels": labels},
                                                                ensure_ascii=False)},
                                    ensure_ascii=False) + "\n")
            stats["generation"] += 1
        else:
            stats["skipped"] += 1
    for f in (fa, fg, fr):
        if f:
            f.close()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="/data/distilled")
    ap.add_argument("--out", default="/data/distilled/rl")
    ap.add_argument("--today", default=datetime.date.today().isoformat(),
                    help="LABEL_SYSの{today}置換値（評価時と同様に実行日を既定とする）")
    a = ap.parse_args()
    src, out = pathlib.Path(a.src), pathlib.Path(a.out)
    (out / "prompts").mkdir(parents=True, exist_ok=True)

    (out / "prompts" / "label_sys.txt").write_text(
        LABEL_SYS.replace("{today}", a.today), encoding="utf-8")
    (out / "prompts" / "answer_sys.txt").write_text(ANSWER_SYS, encoding="utf-8")

    total = {}
    for split in ("train", "valid"):
        p = src / f"{split}.jsonl"
        if not p.exists():
            print(f"[prep_rl] WARN {p} なし（スキップ）", file=sys.stderr)
            continue
        total[split] = convert(p, out, split, with_grpo=(split == "train"))
    print("[prep_rl] done:", json.dumps(total, ensure_ascii=False))
    if any(s["generation"] == 0 for s in total.values()):
        print("[prep_rl] WARN generationレコードが少ない/無い split があります。"
              "S2の N_GEN を増やして再蒸留を検討（GRPOは generation データが主対象）",
              file=sys.stderr)


if __name__ == "__main__":
    main()
