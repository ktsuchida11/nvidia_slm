"""loop15: guardrails → vLLM の間に挟む極小プロキシ（stdlib のみ）。

nemoguardrails は self-check の LLM 呼び出しに `chat_template_kwargs` も `max_tokens` も
渡さない。Nemotron は既定で思考モードに入るため、

  * 判定が数分かかる（1問=LLM3回 × 長考 → タイムアウト）
  * 長い思考文に "yes" が紛れ、既定の出力パーサ（is_content_safe）が
    **良性まで全部ブロック**する（loop15 実機: 良性 B01/B02 が refused）

の2つが同時に起きる。loop8 以来の「学習と評価で思考設定を揃える」原則をレールにも適用する。
config 側の書き方に依存しないよう、通り道で注入するのが最も確実。

環境変数:
  UPSTREAM_BASE_URL  転送先（既定 http://127.0.0.1:8000/v1）
  PROXY_MAX_TOKENS   max_tokens を持たないリクエストに付ける上限（既定 256）
  PROXY_CHAT_KWARGS  JSON。既定 {"enable_thinking": false}
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_CHAT_KWARGS = {"enable_thinking": False}


def inject(payload: dict, chat_kwargs: dict, max_tokens: int) -> dict:
    """リクエスト体に思考オフと上限トークンを差し込む純関数（テスト対象）。

    呼び出し側が既に指定していれば尊重する（上書きしない）。
    """
    out = copy.deepcopy(payload)
    if chat_kwargs and "chat_template_kwargs" not in out:
        out["chat_template_kwargs"] = chat_kwargs
    if max_tokens and not out.get("max_tokens"):
        out["max_tokens"] = max_tokens
    return out


class Handler(BaseHTTPRequestHandler):
    def _upstream(self) -> str:
        return os.environ.get("UPSTREAM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")

    def do_POST(self) -> None:                       # noqa: N802 — 基底の規約
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        kwargs = json.loads(os.environ.get("PROXY_CHAT_KWARGS") or json.dumps(DEFAULT_CHAT_KWARGS))
        body = inject(body, kwargs, int(os.environ.get("PROXY_MAX_TOKENS", "256")))

        url = self._upstream() + self.path.replace("/v1", "", 1)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": self.headers.get("Authorization", "Bearer dummy")},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                out, code = r.read(), r.status
        except Exception as e:                       # noqa: BLE001 — 上流の失敗をそのまま返す
            out, code = json.dumps({"error": str(e)}).encode(), 502
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self) -> None:                        # noqa: N802 — /v1/models のヘルスチェック
        url = self._upstream() + self.path.replace("/v1", "", 1)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                out, code = r.read(), r.status
        except Exception as e:                       # noqa: BLE001
            out, code = json.dumps({"error": str(e)}).encode(), 502
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — 基底の引数名
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8004)
    a = ap.parse_args()
    print(f"llm-proxy :{a.port} → {os.environ.get('UPSTREAM_BASE_URL')} "
          f"(思考オフ / max_tokens={os.environ.get('PROXY_MAX_TOKENS', '256')})", flush=True)
    HTTPServer(("0.0.0.0", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
