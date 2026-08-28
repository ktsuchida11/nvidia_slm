# 閉域運用用: 実行時pipを不要にするツールイメージ（オンライン環境で一度buildして持ち込む）
# build: docker build -t nemo-tools:latest -f docker/tools.Dockerfile .
#        （ベース差し替え: --build-arg BASE_IMAGE=python:3.12-bookworm 等）
# 使用: make <target> PY_IMG=nemo-tools:latest   （閉域では必須）
#
# バージョンは全て固定する。未固定だとビルド時期で中身が変わり、loop report の実測値と
# 突き合わせられなくなる（2026-08 の再ビルドで nemoguardrails が 0.23.0→0.24.0 に動いた）。
# 更新するときは「上げてから loop を回す」のではなく「上げた版で dry を通してから」ピンを動かす。
# 版を上げる: --build-arg 経由ではなく、この行を編集して PR にする（差分が履歴に残る）
#
# 注: ハンズオン（S1 クレンジング / S2 蒸留 dry / S8 RAG dry / S7 ガードレール dry）は
#     このイメージを必要としない。素の python:3.12-bookworm・ネットワーク遮断で通る
#     （docs/35-handson-plan.md §1 で実測確認済み）。このイメージが要るのは
#     実課金の distill / fetch / mlflow など外部ライブラリを実際に使う経路だけ。
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
RUN pip install --no-cache-dir \
      anthropic==1.1.0 \
      PyYAML==6.0.3 \
      datasets==3.6.0 \
      huggingface_hub==1.28.0 \
      "nemoguardrails[all]==0.24.0" \
      garak==0.16.0 \
      pypdf==6.16.2 \
      python-pptx==1.0.2 \
      python-docx==1.2.0 \
      mlflow==3.15.2 \
      numpy==2.4.6
