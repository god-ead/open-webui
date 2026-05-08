#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME=sales-chatbot-image
IMAGE_TAG=local
DOCKERFILE_PATH="Dockerfile"
BUILD_CONTEXT="."

echo "==> Building image: ${IMAGE_NAME}:${IMAGE_TAG}"
docker build \
  -f "${DOCKERFILE_PATH}" \
  -t "${IMAGE_NAME}:${IMAGE_TAG}" \
  "${BUILD_CONTEXT}"

echo "==> Done"
echo "Built image: ${IMAGE_NAME}:${IMAGE_TAG}"