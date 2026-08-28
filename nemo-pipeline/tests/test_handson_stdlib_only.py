"""ハンズオン Step 1〜4 のスクリプトが「素の Python だけで動く」ことを機械的に守る。

当日は参加者が `python:3.12-bookworm` をネットワーク遮断で動かす（docs/35-handson-plan.md §1）。
誰かが実行経路のトップレベルに `import numpy` のような外部依存を足すと、
**ハンズオン当日にだけ** ImportError で全員が止まる。ユニットテストは nemo-tools 上で
通ってしまうので気づけない。それを静的に検出する。

許すもの:
  - 標準ライブラリ
  - リポジトリ内の別モジュール（sibling import）
  - `try: import X / except ImportError:` で囲われた任意依存（フォールバックがある）
  - 関数・メソッドの中の遅延 import

禁じるもの:
  - モジュールのトップレベルで無防備に import される外部パッケージ
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ハンズオンで実際に `python <script>` として起動されるもの（Makefile の *-dry 経路）
ENTRYPOINTS = [
    "s1_curation/s1_curate.py",          # Step 1  make curate
    "s2_distillation/s2_distill.py",     # Step 2  make distill-dry
    "s8_retrieval/build_index.py",       # Step 3  make retriever-index-dry
    "s8_retrieval/recall_eval.py",       # Step 3  make retriever-recall-dry
    "s6_evaluation/probe_qa.py",         # Step 3  make rag-eval-dry / rag-rerank-dry
    "s7_guardrails/stub_llm.py",         # Step 4  make guardrails-dry
    "s7_guardrails/wait_http.py",        # Step 4  make guardrails-dry
    "s7_guardrails/check_rails.py",      # Step 4  make guardrails-dry
]

_LOCAL_MODULES = {p.stem for p in ROOT.rglob("*.py")}


def _unguarded_toplevel_imports(path: pathlib.Path) -> list[tuple[int, str]]:
    """トップレベルかつ try で囲われていない import の (行番号, 先頭モジュール名) を返す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in tree.body:                      # Module 直下のみ = トップレベル
        if isinstance(node, ast.Import):        # try 配下・関数配下はここに現れない
            for a in node.names:
                found.append((node.lineno, a.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.level:                      # 相対 import はリポジトリ内
                continue
            if node.module:
                found.append((node.lineno, node.module.split(".")[0]))
    return found


def _external(path: pathlib.Path) -> list[str]:
    bad = []
    for lineno, mod in _unguarded_toplevel_imports(path):
        if mod == "__future__" or mod in sys.stdlib_module_names or mod in _LOCAL_MODULES:
            continue
        bad.append(f"{path.relative_to(ROOT)}:{lineno} {mod}")
    return bad


def test_handson_entrypoints_import_only_stdlib_at_toplevel():
    offenders = []
    for rel in ENTRYPOINTS:
        p = ROOT / rel
        assert p.exists(), f"ハンズオンの実行対象が見つからない: {rel}"
        offenders += _external(p)
    assert not offenders, (
        "ハンズオンの実行経路がトップレベルで外部パッケージに依存している。\n"
        "当日はネットワーク遮断の素の python イメージで動かすため、ここが増えると全員が止まる。\n"
        "遅延 import か try/except ImportError のフォールバックにすること:\n  "
        + "\n  ".join(offenders)
    )


def test_entrypoint_list_matches_dry_targets():
    """Makefile の dry ターゲットが叩くスクリプトが ENTRYPOINTS から漏れていないこと。"""
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for rel in ENTRYPOINTS:
        assert f"/pipeline/{rel}" in mk, f"Makefile から参照されていない: {rel}（パス変更の取り残し？）"


if __name__ == "__main__":
    test_handson_entrypoints_import_only_stdlib_at_toplevel()
    test_entrypoint_list_matches_dry_targets()
    print("2 passed")
