#!/usr/bin/env bash
# loop15: レール込みの $0 配管検証。1コンテナ内で「スタブLLM → nemoguardrails server → 攻撃セット」を通す。
#
# Makefile のワンライナーだと `A && B & C & D` の結合が弱く、pip install の完了前に
# サーバ起動へ進んでしまう（実機で command not found を踏んだ）。順序が要件なのでスクリプトに切り出す。
set -euo pipefail

PIPELINE=${PIPELINE:-/pipeline}
S7="$PIPELINE/s7_guardrails"
GUARD_PORT=${GUARD_PORT:-8100}
STUB_PORT=${STUB_PORT:-8002}
GUARD_CONFIG=${GUARD_CONFIG:-/configs}
OUT=${OUT:-/results}

echo "== 1/4 config 探索の診断（版差はここで確定させる） =="
python "$S7/diag_rails.py" "$GUARD_CONFIG"

echo "== 2/4 スタブLLM 起動 :$STUB_PORT =="
python "$S7/stub_llm.py" --port "$STUB_PORT" &
python "$S7/wait_http.py" "http://127.0.0.1:$STUB_PORT/v1/models" --timeout 60

echo "== 3/4 nemoguardrails server 起動 :$GUARD_PORT（--config $GUARD_CONFIG） =="
export OPENAI_BASE_URL="http://127.0.0.1:$STUB_PORT/v1" OPENAI_API_KEY=dummy
nemoguardrails server --config "$GUARD_CONFIG" --port "$GUARD_PORT" &
# 本文に config_id が出る。空リストなら --config の指し先が誤り（診断の出力と突き合わせる）
python "$S7/wait_http.py" "http://127.0.0.1:$GUARD_PORT/v1/rails/configs" --timeout 300 --show

echo "== 4/4 攻撃セットを流す（レール込み） =="
python "$S7/check_rails.py" --api guardrails --set attack \
  --base-url "http://127.0.0.1:$GUARD_PORT" --tag dry_rails --out "$OUT"

echo "配管OK: レールが応答している。実機は make guardrails / guardrails-check へ"
