"""dry ターゲットが実データの出力先を上書きしないことの回帰テスト。

2026-08-27 に `make distill-dry` が `/data/distilled` へ直接ダミーを書き、
実データ（train/valid/heldout）を壊す事故を踏んだ。heldout は評価専用の不可侵資産で
あり、ハンズオンでは参加者が最初に叩くターゲットでもあるため、Makefile 側で
「dry の出力先は実データと別ディレクトリ」を機械的に保証する。
"""

import pathlib
import re

MAKEFILE = pathlib.Path(__file__).resolve().parents[1] / "Makefile"

# dry ターゲット名 -> 触れてはいけない実データの出力先
FORBIDDEN = {
    "distill-dry": "/data/distilled",
    "finqa-dry": "/data/finqa",
    "retriever-index-dry": "/data/retriever",
    "grounded-dry": "/data/pretrain/grounded",
}


def _recipe(name: str) -> str:
    """Makefile から指定ターゲットのレシピ行（タブ始まり）を取り出す。"""
    text = MAKEFILE.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(name)}:.*?$", text, re.MULTILINE)
    assert m, f"ターゲットが見つからない: {name}"
    lines = []
    for line in text[m.end():].splitlines()[1:]:
        if line.startswith("\t"):
            lines.append(line)
        elif line.strip() == "" or line.startswith("#"):
            continue
        else:
            break
    assert lines, f"レシピが空: {name}"
    return "\n".join(lines)


def test_dry_targets_do_not_write_into_real_data_dirs():
    for target, forbidden in FORBIDDEN.items():
        recipe = _recipe(target)
        # `--out /data/distilled` のように「実データの出力先ちょうど」を指していたらNG。
        # `/data/distilled_dry` は末尾が続くので許容する。
        bad = re.search(rf"--out\s+{re.escape(forbidden)}(?![\w/_-])", recipe)
        assert bad is None, (
            f"{target} が実データの出力先 {forbidden} へ書こうとしている。"
            f"dry の出力は別ディレクトリ（例 {forbidden}_dry）にすること"
        )


def test_dry_targets_still_declare_an_output():
    """出力先を消して「何も書かない」で誤魔化していないことの確認。"""
    for target in FORBIDDEN:
        assert "--out" in _recipe(target), f"{target} に --out が無い"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(fns)} passed")
