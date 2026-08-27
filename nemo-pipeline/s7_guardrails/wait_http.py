"""loop15: HTTP エンドポイントの起動待ち（stdlib のみ。python:3.12-slim に curl は無い）。

使い方: python wait_http.py http://127.0.0.1:8100/v1/rails/configs --timeout 300 --show
終了コード 0=応答あり / 1=タイムアウト。
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request


def probe(url: str, timeout: int = 5) -> str | None:
    """1回だけ叩いて本文を返す（応答が無ければ None）。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode(errors="replace")
    except Exception:                                # noqa: BLE001 — 起動待ちなので理由は問わない
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--interval", type=int, default=3)
    ap.add_argument("--show", action="store_true", help="応答本文を表示（config_id の確認用）")
    a = ap.parse_args()

    deadline = time.monotonic() + a.timeout
    n = 0
    while time.monotonic() < deadline:
        body = probe(a.url)
        if body is not None:
            print(f"起動確認 OK: {a.url}（{n * a.interval}秒待機）", flush=True)
            if a.show:
                print(body[:500], flush=True)
            sys.exit(0)
        n += 1
        if n % 10 == 0:
            print(f"待機中… {n * a.interval}秒経過 ({a.url})", flush=True)
        time.sleep(a.interval)
    print(f"タイムアウト({a.timeout}秒): {a.url} が応答しません", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
