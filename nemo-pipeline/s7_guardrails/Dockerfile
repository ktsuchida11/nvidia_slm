# loop15: nemoguardrails / garak を焼き込んだ検証用イメージ。
#
# 毎ターゲットで pip install すると数分 × 実行回数ぶん GPU ノードの課金時間を溶かす
# （実機で config_id の切り分けに何度も再実行が必要になり顕在化）。一度だけビルドして使い回す。
#   make guardrails-img   # 初回のみ（数分）
FROM python:3.12-slim

# nemoguardrails[all] は評価CLI(evaluate)を含む。garak は標準ベンチ。
# --uploaded-prior-to=P7D: 公開7日未満のバージョンを除外（サプライチェーン対策・CLAUDE.md 準拠）。
# pip v26.1+ が必要なため先に自身を更新する。
#
# バージョンは固定する。loop15 の実測値（garak 0%・良性 0.85・カスタム 9/12）と
# references/traps.md の罠（single_config_mode / config_id のネスト / evaluate→eval 改名）は
# すべて **nemoguardrails 0.23.0 の挙動**。未固定のままだと再ビルドで版が動き
# （2026-08 の再ビルドで実際に 0.24.0 が入った）、report と突き合わせられなくなる。
#
# GUARDRAILS_VER: loop15 で実測した版。ここを動かすなら loop15 の再測定とセットで。
# GARAK_VER: **loop15 当時の版が記録されていない**ため、これは「loop15 の再現」ではなく
#            ここから先の基準を固定するためのピン。garak の結果を版跨ぎで比べないこと。
ARG GUARDRAILS_VER=0.23.0
ARG GARAK_VER=0.16.0
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --uploaded-prior-to=P7D \
      "nemoguardrails[all]==${GUARDRAILS_VER}" "garak==${GARAK_VER}"

WORKDIR /pipeline
