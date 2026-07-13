#!/usr/bin/env bash
# =============================================================================
# リモートGPUホスト（Ubuntu 22.04/24.04, x86_64, NVIDIA GPU）初期セットアップ
#   対象: EC2 g6e系 / Brev / 自前GPUサーバ（docs/00-environment.md の手順を自動化）
#   使い方: このリポをGPUホストへ持ち込み（git clone / scp）、
#           bash remote/setup-gpu-host.sh
#   その後: docker login nvcr.io（user=$oauthtoken, pass=NGC APIキー）→ make sft 等
# =============================================================================
set -euo pipefail

echo "== [1/5] 前提確認 =="
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "ERROR: NVIDIAドライバ未導入（nvidia-smiなし）。" >&2
  echo "  Ubuntu: sudo ubuntu-drivers install --gpgpu / EC2はDeep Learning AMI推奨" >&2
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

echo "== [2/5] Docker =="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "NOTE: docker グループ反映のため再ログインが必要な場合があります"
fi
docker --version

echo "== [3/5] NVIDIA Container Toolkit =="
if ! command -v nvidia-ctk >/dev/null 2>&1; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq nvidia-container-toolkit
fi
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

echo "== [4/5] GPUコンテナ疎通 =="
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

echo "== [5/5] パイプライン初期化 =="
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
make setup
mkdir -p data/distilled

cat <<'EOS'
========================================================================
セットアップ完了。次の手順:
 1. docker login nvcr.io        # Username: $oauthtoken / Password: <NGC APIキー>
 2. ローカルから data/distilled/{train,valid}.jsonl を scp で配置
 3. make tracking               # MLflow(:5000, 閉域)
 4. make sft   → make eval TAG=sft   （run_sft.sh のレシピ確定が前提: docs/02 §3）
 5. make grpo  → make eval TAG=grpo
 6. 終わったら必ずインスタンス停止（spot なら checkpoint を S3 等へ退避）
========================================================================
EOS
