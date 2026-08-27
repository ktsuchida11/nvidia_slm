"""loop15: garak レポートの中身を確認する（応答が空のまま採点していないか）。

garak は応答が空文字でも「攻撃失敗＝防御成功」として数えるため、
`response_json_field` を誤ると**偽の完璧な防御率**が静かに出る。走らせる前後に必ず中身を見る。

使い方: python inspect_garak.py /results/garak_rails.report.jsonl
"""
from __future__ import annotations

import argparse
import json
import pathlib


def summarize_attempts(rows: list[dict], n_show: int = 3) -> dict:
    """attempt 行から出力の空率と例を取り出す純関数（テスト対象）。"""
    outs = []
    for r in rows:
        if r.get("entry_type") != "attempt":
            continue
        for o in r.get("outputs") or []:
            outs.append("" if o is None else str(o))
    empty = sum(1 for o in outs if not o.strip())
    return {"n_outputs": len(outs),
            "empty": empty,
            "empty_rate": round(empty / len(outs), 4) if outs else None,
            "samples": [o[:200] for o in outs[:n_show] if o.strip()]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    a = ap.parse_args()
    rows = [json.loads(l) for l in pathlib.Path(a.report).read_text(encoding="utf-8").splitlines() if l.strip()]
    s = summarize_attempts(rows)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    if s["empty_rate"]:
        print(f"\n[警告] 出力の {s['empty_rate']:.0%} が空。response_json_field の指定を疑うこと"
              "（0.23.0 の応答は choices[]）。空応答は『攻撃失敗』と数えられるため結果は無効。")


if __name__ == "__main__":
    main()
