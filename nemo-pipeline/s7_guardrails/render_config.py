"""loop15: guardrails config の環境変数を**自前で**展開してから配置する。

nemoguardrails 0.23.0 は config.yml 内の `${OPENAI_BASE_URL}` を展開せず、文字列のまま
エンドポイントに使う（実機で `endpoint=${OPENAI_BASE_URL}` → UnsupportedProtocol）。
版に依存しないよう、起動前にこちらで描画する。

未設定の変数は既定値が無ければ**エラーで止める**（黙って壊れた URL を渡さない）。

使い方: python render_config.py --in /s7/config --out /tmp/rails/config
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil

VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")
RENDER_SUFFIXES = {".yml", ".yaml"}


def render(text: str, env: dict) -> str:
    """`${VAR}` / `${VAR:-default}` を展開する純関数（テスト対象）。"""
    missing = []

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(3)
        val = env.get(name)
        if val:
            return val
        if default is not None:
            return default
        missing.append(name)
        return m.group(0)

    out = VAR.sub(sub, text)
    if missing:
        raise KeyError(f"未設定の環境変数: {sorted(set(missing))}（-e で渡すか既定値を書く）")
    return out


def render_dir(src: pathlib.Path, dst: pathlib.Path, env: dict) -> list[str]:
    """config ディレクトリを描画してコピーし、書き出したファイル名を返す。"""
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    for p in sorted(src.iterdir()):
        if not p.is_file():
            continue
        target = dst / p.name
        if p.suffix in RENDER_SUFFIXES:
            target.write_text(render(p.read_text(encoding="utf-8"), env), encoding="utf-8")
        else:
            shutil.copyfile(p, target)          # rails.co 等はそのまま
        written.append(p.name)
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    a = ap.parse_args()
    written = render_dir(pathlib.Path(a.src), pathlib.Path(a.dst), dict(os.environ))
    print(f"config を描画: {a.src} → {a.dst} ({', '.join(written)})")


if __name__ == "__main__":
    main()
