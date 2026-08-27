"""loop15: guardrails サーバの config 探索を実機で診断する（課金時間を溶かさないための道具）。

`Invalid configuration ids: ['config']` を実機で踏んだ。--config が何を期待するかは版で異なり、
ドキュメントだけでは確定できないため、**インストール済みの実物のソースを読んで**答えを出す。

使い方: make guardrails-diag  （引数で探索対象を変えられる）
"""
from __future__ import annotations

import inspect
import os
import re
import sys


def discovered_ids(path: str) -> list[str]:
    """サーバ側の探索条件（config.yml を持つサブディレクトリ）を再現した純関数。"""
    if not os.path.isdir(path):
        return []
    return sorted(
        f for f in os.listdir(path)
        if os.path.isdir(os.path.join(path, f)) and f[:1] not in (".", "_")
        and any(os.path.exists(os.path.join(path, f, n))
                for n in ("config.yml", "config.yaml")))


def show_source_around(src: str, needle: str, before: int = 12, after: int = 12) -> None:
    """インストール済みソースから該当箇所を抜粋表示（版差の正解はここにしか無い）。"""
    lines = src.splitlines()
    hits = [i for i, l in enumerate(lines) if needle in l]
    if not hits:
        print(f"  （'{needle}' は見つからず）")
        return
    for i in hits[:2]:
        lo, hi = max(0, i - before), min(len(lines), i + after)
        print(f"  --- {needle} 周辺 (L{lo + 1}-{hi}) ---")
        for n in range(lo, hi):
            print(f"  {n + 1:5d}| {lines[n]}")


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/pipeline/s7_guardrails"
    import nemoguardrails
    from nemoguardrails import RailsConfig

    print(f"== nemoguardrails {nemoguardrails.__version__} ==")
    print(f"探索対象: {path}  (isdir={os.path.isdir(path)})")
    if os.path.isdir(path):
        print(f"直下: {sorted(os.listdir(path))}")
    print(f"config.yml を持つサブディレクトリ: {discovered_ids(path)}")

    for cand in (path, os.path.join(path, "config")):
        if not os.path.isdir(cand):
            continue
        try:
            cfg = RailsConfig.from_path(cand)
            print(f"[OK ] RailsConfig.from_path({cand})"
                  f" models={[m.model for m in cfg.models]}"
                  f" input_flows={cfg.rails.input.flows}")
        except Exception as e:                       # noqa: BLE001 — 診断なので全部拾う
            print(f"[NG ] RailsConfig.from_path({cand}): {type(e).__name__}: {e}")

    print("\n== サーバ実装の該当箇所（この版の仕様が正） ==")
    from nemoguardrails.server import api as sapi
    src = inspect.getsource(sapi)
    show_source_around(src, "Invalid configuration ids")
    show_source_around(src, "/v1/rails/configs")
    for m in re.finditer(r"^\s*(app\.rails_config_path\s*=.*)$", src, re.M):
        print(f"  config path 設定: {m.group(1).strip()}")

    # 0.23.0 は single-config モードを持ち、その場合 config_id = --config のフォルダ名になる。
    # 判定条件がどこで決まるかを CLI 側から確認する（config_id をどう渡すかが決まる）
    print("\n== single-config モードの判定（config_id の決まり方） ==")
    try:
        from nemoguardrails import cli
        show_source_around(inspect.getsource(cli), "single_config_mode", before=10, after=8)
    except Exception as e:                           # noqa: BLE001 — 版により配置が違う
        print(f"  CLI ソースを読めず: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
