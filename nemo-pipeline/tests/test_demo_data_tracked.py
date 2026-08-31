"""README のクイックスタートが「クローン直後」に通ることを守る回帰テスト。

`.gitignore` は data/ 配下を丸ごと除外しているが、デモ・演習用の
`data/demo_raw/sample.jsonl` だけは例外的に追跡する必要がある。これが外れると
新規クローンで `make curate-demo` が kept 0 で落ちる（実際に踏んだ）。

同時に、除外の穴が広がって実データやチェックポイントが追跡対象に化けていないかも見る。
`.gitignore` のパターンは `data/` → `data/*` → `**/data/*` と二度書き換えて二度とも
挙動が変わったので、機械で押さえておく。
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

MUST_BE_TRACKED = ["data/demo_raw/sample.jsonl"]
MUST_BE_IGNORED = [
    "data/raw/sample.jsonl",          # 実コーパス（2,530文書）
    "data/curated/curated.jsonl",
    "data/distilled/train.jsonl",     # 蒸留済み学習データ
    "data/distilled/heldout.jsonl",   # 評価専用・不可侵
    "data/demo_curated/stats.json",   # curate-demo の出力（生成物）
    "data/models/dummy.safetensors",
]


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _skip_if_no_git():
    if _git("rev-parse", "--git-dir").returncode != 0:
        print("SKIP: git リポジトリではない")
        sys.exit(0)


def test_demo_sample_is_tracked():
    for rel in MUST_BE_TRACKED:
        out = _git("ls-files", "--error-unmatch", rel)
        assert out.returncode == 0, (
            f"{rel} が git 管理外。クローン直後に `make curate-demo` が kept 0 で落ちる。\n"
            f"  .gitignore の否定パターン（!**/data/demo_raw/）を確認すること。"
        )
        assert (ROOT / rel).exists(), f"{rel} が追跡されているのに実体が無い"


def test_real_data_stays_ignored():
    for rel in MUST_BE_IGNORED:
        assert _git("check-ignore", "-q", rel).returncode == 0, (
            f"{rel} が .gitignore から漏れている。実データ・成果物をコミットしかねない。\n"
            f"  `data/*` はスラッシュを含むためリポジトリ直下にしか掛からない。`**/data/*` が要る。"
        )


def test_demo_sample_reproduces_the_documented_counts():
    """台本の数字（6件入れて kept 2）の前提が崩れていないか。"""
    import json

    p = ROOT / "data/demo_raw/sample.jsonl"
    docs = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(docs) == 6, f"デモサンプルは6件のはず（37-handson-runbook Step 1-1）。実際 {len(docs)}件"
    licenses = [d["meta"].get("license") for d in docs]
    assert licenses.count("cc-by-nc") == 1, "ライセンス棄却を見せる cc-by-nc の1件が必要"
    assert sum("@" in d["text"] for d in docs) == 1, "PII マスクを見せるメールアドレス入り1件が必要"


if __name__ == "__main__":
    _skip_if_no_git()
    test_demo_sample_is_tracked()
    test_real_data_stays_ignored()
    test_demo_sample_reproduces_the_documented_counts()
    print("OK: test_demo_data_tracked")
