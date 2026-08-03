"""S1 前段: NeMo Curator 1.x パイプラインで言語ID + 品質フィルタ（loop10 P2）。

/data/raw/*.jsonl → [JsonlReader → FastTextLangId → 品質ヒューリスティック → JsonlWriter]
→ /data/raw_curator/*.jsonl（S1入力形式を維持・meta.lang_score 付与）→ s1_curate.py へ。

Curator 1.3.0 (pip, CPU) の実像（2026-07-29 実測）:
  - 使える: Pipeline / JsonlReader / JsonlWriter / ScoreFilter / FastTextLangId /
            heuristic フィルタ群（char 系は日本語可）
  - 使えない(GPU専用): fuzzy dedup（deduplication-cuda12 extra）・PII modifier（pip非同梱）
    → dedup は s1_curate.py の高速化 minhash、PII は EDINET 実データ校正済み regex を維持。

注意:
  - meta(dict) は pyarrow 経由で崩れる可能性があるため JSON 文字列に畳んでから通し、出口で復元する。
  - 語(空白区切り)ベースのフィルタは日本語で機能しないため char 系のみ採用。
  - fastText lid.176.ftz は初回に自動ダウンロード（--model-dir にキャッシュ）。
  - Curator が import できない環境では exit 3（Makefile 側で警告して素通しにできるよう区別）。
"""
from __future__ import annotations
import argparse, json, logging, pathlib, shutil, sys, urllib.request

logging.basicConfig(level=logging.INFO, format="[curator-stage] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

LID_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz"
MIN_LANGID_SCORE = 0.3      # Curator 公式例の既定値
KEEP_LANGS = {"JA"}         # FastTextLangId は大文字の言語コードを返す
# NonAlphaNumericFilter は不採用: ソースに "Intended to be applied only to English text" と
# 明記されており日本語は74%が非英数扱いで全滅する（2026-07-29 実測）。
# 記号過多の検査は s1_curate.quality() の symbol_heavy（Unicode isalnum・日本語安全）が本段で行う。
MIN_UNIQUE_LINES_CHAR_RATIO = 0.8       # RepeatedLinesByCharFilter 既定（ユニーク行文字率がこれ未満なら除外・文字ベースで日本語可）


def ensure_model(model_dir: str) -> str:
    d = pathlib.Path(model_dir); d.mkdir(parents=True, exist_ok=True)
    dst = d / "lid.176.ftz"
    if not dst.exists():
        log.info("fastText 言語IDモデルを取得: %s", LID_URL)
        with urllib.request.urlopen(LID_URL, timeout=60) as r, dst.open("wb") as w:
            shutil.copyfileobj(r, w)
    return str(dst)


def fold_meta(src_dir: pathlib.Path, work_dir: pathlib.Path) -> int:
    """meta(dict) を meta_json(str) に畳んだ作業コピーを作る（pyarrow 破損対策）"""
    work_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src_dir.glob("*.jsonl")):
        with (work_dir / f.name).open("w", encoding="utf-8") as w:
            for line in f.open(encoding="utf-8"):
                if not line.strip():
                    continue
                d = json.loads(line)
                w.write(json.dumps({"text": d.get("text", ""),
                                    "meta_json": json.dumps(d.get("meta", {}), ensure_ascii=False)},
                                   ensure_ascii=False) + "\n")
                n += 1
    return n


def unfold_meta(curator_out: pathlib.Path, dst_dir: pathlib.Path) -> tuple[int, int]:
    """Curator 出力から S1 入力形式へ復元。言語判定の内訳も返す"""
    dst_dir.mkdir(parents=True, exist_ok=True)
    kept = dropped_lang = 0
    sampled = False
    with (dst_dir / "curator_passed.jsonl").open("w", encoding="utf-8") as w:
        for f in sorted(curator_out.glob("*.jsonl")):
            for line in f.open(encoding="utf-8"):
                if not line.strip():
                    continue
                d = json.loads(line)
                lang_field = d.get("language")          # ScoreFilter が [score, "__label__xx"] 等を格納
                lang, score = parse_lang(lang_field)
                if not sampled:                         # 形式ゆれの一次診断用に実形式を必ず1件残す
                    log.info("language field sample: %r → parsed=(%s, %s)", lang_field, lang, score)
                    sampled = True
                if lang not in KEEP_LANGS:
                    dropped_lang += 1; continue
                meta = json.loads(d.get("meta_json") or "{}")
                meta["lang"] = lang; meta["lang_score"] = score
                w.write(json.dumps({"text": d["text"], "meta": meta}, ensure_ascii=False) + "\n")
                kept += 1
    return kept, dropped_lang


def parse_lang(v) -> tuple[str | None, float | None]:
    """FastTextLangId のスコア表現ゆれを吸収して (言語, score) へ。
    実形式は版・シリアライズ経路(pyarrow/JsonlWriter)で揺れる: [score, label] / 文字列化された
    リスト "['0.99', '__label__ja']" / "label" / dict をすべて受ける。"""
    if isinstance(v, str) and v.strip()[:1] in "[({":
        import ast, re
        for loader in (json.loads, ast.literal_eval):
            try:
                v = loader(v); break
            except (ValueError, SyntaxError):
                continue
        if isinstance(v, str):                  # np.float64(...) 等、リテラルでない表現の最終手段
            m = re.search(r"__label__(\w+)", v)
            s = re.search(r"\d+\.\d+(?:e-?\d+)?", v)
            return ((m.group(1).upper() if m else None),
                    (float(s.group(0)) if s else None))
    label, score = None, None
    if isinstance(v, (list, tuple)) and v:
        for x in v:
            sx = str(x)
            if sx.replace(".", "", 1).replace("e-", "", 1).lstrip("-").isdigit():
                score = x
            else:
                label = sx
    elif isinstance(v, str):
        label = v
    elif isinstance(v, dict):
        label, score = v.get("lang") or v.get("label"), v.get("score")
    if isinstance(label, str):
        label = label.strip("'\" ").replace("__label__", "").upper()
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return label, score


def run_curator(work_dir: str, out_dir: str, model_path: str) -> None:
    from nemo_curator.pipeline import Pipeline
    from nemo_curator.stages.text.filters import ScoreFilter
    from nemo_curator.stages.text.filters.fasttext import FastTextLangId
    from nemo_curator.stages.text.filters.heuristic.repetition import RepeatedLinesByCharFilter
    from nemo_curator.stages.text.io.reader import JsonlReader
    from nemo_curator.stages.text.io.writer import JsonlWriter

    pipe = Pipeline(name="s1_curator_stage", description="言語ID+品質フィルタ（loop10 P2）")
    pipe.add_stage(JsonlReader(file_paths=work_dir))
    pipe.add_stage(ScoreFilter(FastTextLangId(model_path=model_path,
                                              min_langid_score=MIN_LANGID_SCORE),
                               text_field="text", score_field="language"))
    pipe.add_stage(ScoreFilter(RepeatedLinesByCharFilter(
        max_repeated_lines_char_ratio=MIN_UNIQUE_LINES_CHAR_RATIO), text_field="text"))
    pipe.add_stage(JsonlWriter(path=out_dir))
    pipe.run()


def main(inp: str, out: str, model_dir: str) -> None:
    try:
        import nemo_curator  # noqa: F401
    except ImportError as e:
        log.error("nemo-curator が import できません（%s）。"
                  "pip install 'nemo-curator[text-cpu]' か、素通し運用(--in を直接 s1_curate へ)", e)
        sys.exit(3)
    src, dst = pathlib.Path(inp), pathlib.Path(out)
    work = dst / "_work"; curator_out = dst / "_curator_out"
    for p in (work, curator_out):
        if p.exists():
            shutil.rmtree(p)                 # 中間物のみ削除（入出力の実体データは対象外）
    n_in = fold_meta(src, work)
    if n_in == 0:
        log.error("入力なし: %s/*.jsonl", src); sys.exit(1)
    model = ensure_model(model_dir)
    run_curator(str(work), str(curator_out), model)
    kept, dropped_lang = unfold_meta(curator_out, dst)
    shutil.rmtree(work); shutil.rmtree(curator_out)
    log.info("done: in=%d → kept=%d (非日本語=%d, 品質落ち=%d) → %s/curator_passed.jsonl",
             n_in, kept, dropped_lang, n_in - kept - dropped_lang, dst)
    if kept == 0:
        log.error("全滅 — フィルタ閾値かモデルを確認"); sys.exit(1)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", default="/data/raw")
    p.add_argument("--out", default="/data/raw_curator")
    p.add_argument("--model-dir", default="/data/models")
    a = p.parse_args()
    main(a.inp, a.out, a.model_dir)
