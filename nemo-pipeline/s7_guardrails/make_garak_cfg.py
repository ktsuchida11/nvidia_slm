"""loop15: garak の RestGenerator 設定をテンプレートから実行時に生成する（sed 置換の置き換え）。

ポート・モデル名・応答 JSONPath は実機で変わりうる（nemoguardrails は版により
messages[] / OpenAI互換 choices[] のどちらでも返す）。sed の正規表現置換は JSONPath の
`$` や `[-1]` と相性が悪いため、JSON として読み書きする。

使い方:
  python make_garak_cfg.py --template garak_rest.json --out /tmp/garak_rest.json \
      --port 8100 --model nemotron-gen --resp-field '$.messages[-1].content'
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re


def patch_cfg(cfg: dict, port: int = 0, model: str = "", resp_field: str = "") -> dict:
    """RestGenerator 設定にポート/モデル/応答フィールドを反映する純関数（テスト対象）。"""
    out = copy.deepcopy(cfg)
    gen = out["rest"]["RestGenerator"]
    if port:
        gen["uri"] = re.sub(r":\d+/", f":{port}/", gen["uri"])
    if model and "model" in gen.get("req_template_json_object", {}):
        gen["req_template_json_object"]["model"] = model
    if resp_field:
        gen["response_json_field"] = resp_field
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--model", default="")
    ap.add_argument("--resp-field", default="")
    a = ap.parse_args()
    cfg = json.loads(pathlib.Path(a.template).read_text(encoding="utf-8"))
    pathlib.Path(a.out).write_text(
        json.dumps(patch_cfg(cfg, a.port, a.model, a.resp_field), ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"garak 設定を生成: {a.out}")


if __name__ == "__main__":
    main()
