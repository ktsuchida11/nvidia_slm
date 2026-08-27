# loop15: nemoguardrails / garak を焼き込んだ検証用イメージ。
#
# 毎ターゲットで pip install すると数分 × 実行回数ぶん GPU ノードの課金時間を溶かす
# （実機で config_id の切り分けに何度も再実行が必要になり顕在化）。一度だけビルドして使い回す。
#   make guardrails-img   # 初回のみ（数分）
FROM python:3.12-slim

# nemoguardrails[all] は評価CLI(evaluate)を含む。garak は標準ベンチ。
# --uploaded-prior-to=P7D: 公開7日未満のバージョンを除外（サプライチェーン対策・CLAUDE.md 準拠）。
# pip v26.1+ が必要なため先に自身を更新する。
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --uploaded-prior-to=P7D 'nemoguardrails[all]' garak

WORKDIR /pipeline
