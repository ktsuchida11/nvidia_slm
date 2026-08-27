"""loop15: $0 配管検証用のスタブ LLM（OpenAI 互換・stdlib のみ・GPU/API 不要）。

nemoguardrails は self-check の判定も本文生成も同じ OpenAI 互換エンドポイントへ投げる。
本スタブはプロンプトを見て役割を切り替え、レール配管（config 読み込み・環境変数の伝搬・
config_id 解決・応答スキーマ）を GPU を1秒も使わずに通せるようにする。

  * 判定プロンプト（"判定:" を含む）→ "yes"/"no" を返す。
    既定は "no"（違反なし）。STUB_MODE=block なら入力判定を "yes" にして拒否経路を通す
  * それ以外（本文生成）→ 固定の日本語回答（出典付き）

使い方: python stub_llm.py --port 8002    → http://localhost:8002/v1/chat/completions
"""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

ANSWER = "売上高は1,485億円です。【出典: EDINET-Bench/E00472/S100QZNJ】"


def decide(prompt: str, mode: str) -> str:
    """スタブの応答を決める純関数（テスト対象）。"""
    if "判定" in prompt or "yes/no" in prompt.lower():
        # self_check_input / self_check_output の判定要求
        if mode == "block":
            return "yes"
        return "no"
    return ANSWER


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:                       # noqa: N802 — BaseHTTPRequestHandler の規約
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        prompt = "\n".join(m.get("content", "") for m in body.get("messages", []))
        text = decide(prompt, os.environ.get("STUB_MODE", "pass"))
        payload = {"id": "stub", "object": "chat.completion", "model": body.get("model", "stub"),
                   "choices": [{"index": 0, "finish_reason": "stop",
                                "message": {"role": "assistant", "content": text}}],
                   "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        out = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self) -> None:                        # noqa: N802 — /v1/models のヘルスチェック用
        out = json.dumps({"object": "list",
                          "data": [{"id": "nemotron-gen", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — 基底の引数名に合わせる
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8002)
    a = ap.parse_args()
    print(f"stub LLM listening on :{a.port} (mode={os.environ.get('STUB_MODE', 'pass')})",
          flush=True)
    HTTPServer(("0.0.0.0", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
