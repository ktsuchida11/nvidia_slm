# 閉域運用用: 実行時pipを不要にするツールイメージ（オンライン環境で一度buildして持ち込む）
# build: docker build -t nemo-tools:latest -f docker/tools.Dockerfile .
#        （ベース差し替え: --build-arg BASE_IMAGE=python:3.12-bookworm 等）
# 使用: make <target> PY_IMG=nemo-tools:latest   （閉域では必須）
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
RUN pip install --no-cache-dir \
      anthropic pyyaml datasets huggingface_hub \
      "nemoguardrails[all]" garak \
      pypdf python-pptx python-docx \
      mlflow
