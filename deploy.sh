#!/usr/bin/env bash
set -euo pipefail

# ── 配置（按实际情况修改） ────────────────────────────────────────────────
REMOTE_USER="ubuntu"
REMOTE_HOST="your.server.ip"
REMOTE_PORT="22"
CONTAINER_NAME="reflow"
IMAGE_NAME="reflow"
IMAGE_TAG="$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')"
HOST_PORT="8000"
DATA_VOLUME="reflow-data"
# ─────────────────────────────────────────────────────────────────────────

SSH="ssh -p ${REMOTE_PORT} ${REMOTE_USER}@${REMOTE_HOST}"
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"

echo "==> [1/3] 本地构建镜像 ${FULL_IMAGE}"
docker build -t "${FULL_IMAGE}" .

echo "==> [2/3] 传输镜像到 ${REMOTE_HOST}（管道直传，无中间文件）"
docker save "${FULL_IMAGE}" | ${SSH} docker load

echo "==> [3/3] 替换远端容器"
${SSH} bash -s -- "${CONTAINER_NAME}" "${FULL_IMAGE}" "${HOST_PORT}" "${DATA_VOLUME}" <<'REMOTE'
  CONTAINER_NAME=$1
  FULL_IMAGE=$2
  HOST_PORT=$3
  DATA_VOLUME=$4

  OLD_IMAGE=$(docker inspect --format='{{.Config.Image}}' "${CONTAINER_NAME}" 2>/dev/null || true)

  docker stop "${CONTAINER_NAME}" 2>/dev/null || true
  docker rm   "${CONTAINER_NAME}" 2>/dev/null || true

  docker run -d \
    --name "${CONTAINER_NAME}" \
    --restart unless-stopped \
    -p "${HOST_PORT}:8000" \
    -v "${DATA_VOLUME}:/data" \
    "${FULL_IMAGE}"

  # 清理旧镜像（tag 不同时才删，避免误删刚加载的新镜像）
  if [ -n "${OLD_IMAGE}" ] && [ "${OLD_IMAGE}" != "${FULL_IMAGE}" ]; then
    docker rmi "${OLD_IMAGE}" || true
  fi
  docker image prune -f
REMOTE

echo ""
echo "部署完成！访问 http://${REMOTE_HOST}:${HOST_PORT}/"
