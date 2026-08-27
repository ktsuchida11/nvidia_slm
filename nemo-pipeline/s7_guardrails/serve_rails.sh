#!/usr/bin/env bash
# loop15: guardrails サーバ起動。config を描画してから渡す（環境変数展開は自前で行う）。
#
# 出力先のフォルダ名がそのまま config_id になる（0.23.0 の single-config モード）。
# /tmp/rails/config に置くので config_id は "config"。
set -euo pipefail

S7=${S7:-/s7}
SRC=${SRC:-$S7/config}
DST=${DST:-/tmp/rails/config}
PORT=${PORT:-8100}

: "${OPENAI_BASE_URL:?OPENAI_BASE_URL が未設定（-e で渡す。例 http://<vLLMのIP>:8000/v1）}"

python "$S7/render_config.py" --in "$SRC" --out "$DST"
echo "config_id = $(basename "$DST") / LLM = $OPENAI_BASE_URL"
exec nemoguardrails server --config "$DST" --port "$PORT"
