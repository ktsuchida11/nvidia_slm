# DevContainer (CPU/API面 開発用) — GPU不要。学習・推論はここで行わない。
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

# uv（Python管理） + Python 3.12
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN uv python install 3.12

# SOPS/age（秘匿情報の復号運用。平文.envを置かない）— バージョン固定で確実に導入
ARG SOPS_VERSION=3.10.2
RUN apt-get update && apt-get install -y --no-install-recommends age curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${ARCH}" \
       -o /usr/local/bin/sops \
    && chmod +x /usr/local/bin/sops \
    && sops --version

USER vscode
WORKDIR /workspace

# 依存は postCreateCommand(uv sync) で同期。Node/Claude Code も同タイミングで導入。
