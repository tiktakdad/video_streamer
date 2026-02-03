#!/bin/sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE_NAME="${IMAGE_NAME:-video_streamer}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

docker build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    "${ROOT_DIR}"

echo "빌드 완료: ${IMAGE_NAME}:${IMAGE_TAG}"
