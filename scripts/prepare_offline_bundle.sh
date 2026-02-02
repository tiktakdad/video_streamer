#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEB_DIR="${ROOT_DIR}/offline/debs"
WHEEL_DIR="${ROOT_DIR}/offline/wheels"

mkdir -p "${DEB_DIR}" "${WHEEL_DIR}"

sudo apt-get update
sudo apt-get install -y --download-only \
    ffmpeg \
    libgl1 \
    libsm6 \
    libxext6

cp /var/cache/apt/archives/*.deb "${DEB_DIR}/"

python3 -m pip download -r "${ROOT_DIR}/requirements.txt" -d "${WHEEL_DIR}"

echo "오프라인 번들 준비 완료:"
echo "- ${DEB_DIR}"
echo "- ${WHEEL_DIR}"
