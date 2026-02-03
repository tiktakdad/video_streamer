#!/bin/sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE_NAME="${IMAGE_NAME:-video_streamer}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

STREAM_HOST="${STREAM_HOST:-0.0.0.0}"
STREAM_PORT="${STREAM_PORT:-5004}"

docker run --rm \
    -e STREAM_HOST="${STREAM_HOST}" \
    -e STREAM_PORT="${STREAM_PORT}" \
    -p "${STREAM_PORT}:${STREAM_PORT}/udp" \
    -v "${ROOT_DIR}:/app" \
    "${IMAGE_NAME}:${IMAGE_TAG}"

