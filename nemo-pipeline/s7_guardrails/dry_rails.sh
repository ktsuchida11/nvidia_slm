#!/usr/bin/env bash
# loop15: レール込みの $0 配管検証。1コンテナ内で「スタブLLM → nemoguardrails server → 攻撃セット」を通す。
#
# Makefile のワンライナーだと `A && B & C & D` の結合が弱く、順序が守られない（実機で踏んだ）。
# 順序が要件なのでスクリプトに切り出す。本番の起動経路（serve_rails.sh）をそのまま使い、
# config 描画・config_id の決まり方まで同じ条件で潰す。
set -euo pipefail

PIPELINE=${PIPELINE:-/pipeline}
S7="$PIPELINE/s7_guardrails"
GUARD_PORT=${GUARD_PORT:-8100}
STUB_PORT=${STUB_PORT:-8002}
DST=${DST:-/tmp/rails/config}
OUT=${OUT:-/results}

echo "== 1/4 config 探索の診断（版差はここで確定させる） =="
python "$S7/diag_rails.py" "$S7"

echo "== 2/4 スタブLLM 起動 :$STUB_PORT（block モード = self-check が必ず yes を返す） =="
# block モードにすると入力レールが必ず発火する。つまり本 dry は
# 「config描画 → config_id解決 → レールがLLMを呼べる → 判定を解釈できる → 拒否文が返る →
#  こちらの拒否検出が一致する」までの鎖を丸ごと検証できる（期待 ok_rate = 1.0）
STUB_MODE=block python "$S7/stub_llm.py" --port "$STUB_PORT" &
python "$S7/wait_http.py" "http://127.0.0.1:$STUB_PORT/v1/models" --timeout 60

echo "== 3/4 nemoguardrails server 起動 :$GUARD_PORT（本番と同じ serve_rails.sh 経由） =="
export OPENAI_BASE_URL="http://127.0.0.1:$STUB_PORT/v1" OPENAI_API_KEY=dummy
S7="$S7" DST="$DST" PORT="$GUARD_PORT" bash "$S7/serve_rails.sh" &
# 本文に config_id が出る。空リストなら --config の指し先が誤り（診断の出力と突き合わせる）
python "$S7/wait_http.py" "http://127.0.0.1:$GUARD_PORT/v1/rails/configs" --timeout 300 --show

echo "== 4/4 攻撃セットを流す（レール込み） =="
python "$S7/check_rails.py" --api guardrails --set attack --config-id "$(basename "$DST")" \
  --base-url "http://127.0.0.1:$GUARD_PORT" --tag dry_rails --out "$OUT"

echo "期待: ok_rate 1.0 かつ errored 0（errored>0 はレールがLLMを呼べていない＝測定不能）"
