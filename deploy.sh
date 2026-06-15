#!/usr/bin/env bash
set -euo pipefail

# ── 配置（按实际情况修改） ────────────────────────────────────────────────
SSH_HOST="your-host"           # ~/.ssh/config 中的 Host 别名
CONTAINER_NAME="reflow"
IMAGE_NAME="reflow"
IMAGE_TAG="$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')"
HOST_PORT="8000"
DATA_VOLUME="reflow-data"
# ─────────────────────────────────────────────────────────────────────────

SSH="ssh ${SSH_HOST}"
FULL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"

# ── 部署前自检 ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_NAME="$(basename "${BASH_SOURCE[0]}")"

# [#12] 本脚本（deploy.*.sh）是否与 deploy.sh 的非配置部分保持同步。
#       deploy.sh 自身无需自检（与模板恒等）；在其他工作树上编辑 deploy.sh 时
#       不会被 hook 同步到 deploy.*.sh，故运行前在此校验，不一致则提醒同步。
if [ "${SELF_NAME}" != "deploy.sh" ]; then
  echo "==> 自检：本脚本与 deploy.sh 是否同步"
  if ! bash "${SCRIPT_DIR}/scripts/sync-deploy.sh" --check "${SCRIPT_DIR}/${SELF_NAME}" 2>/dev/null; then
    echo "⚠ 本脚本与 deploy.sh 的非配置部分不一致（deploy.sh 可能已更新但未同步到本脚本）。" >&2
    echo "  请先运行：bash scripts/sync-deploy.sh" >&2
    read -r -p "  仍要继续部署吗？(y/N) " reply </dev/tty || reply=""
    case "${reply}" in [yY]) ;; *) echo "已中止。" >&2; exit 1 ;; esac
  fi
fi

# [#13] 当前代码是否基于 master 最新状态，防止误部署了错误的状态。
echo "==> 检查：当前代码是否基于 master 最新状态"
GIT_ISSUES=""
CURRENT_BRANCH="$(git -C "${SCRIPT_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
if [ "${CURRENT_BRANCH}" != "master" ]; then
  GIT_ISSUES="${GIT_ISSUES}\n  - 当前不在 master 分支（当前：${CURRENT_BRANCH:-detached HEAD}）"
fi
if git -C "${SCRIPT_DIR}" fetch -q origin master 2>/dev/null; then
  LOCAL_HEAD="$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || echo '')"
  REMOTE_HEAD="$(git -C "${SCRIPT_DIR}" rev-parse origin/master 2>/dev/null || echo '')"
  if [ "${LOCAL_HEAD}" != "${REMOTE_HEAD}" ]; then
    GIT_ISSUES="${GIT_ISSUES}\n  - HEAD 与 origin/master 不一致（HEAD ${LOCAL_HEAD:0:7} / origin/master ${REMOTE_HEAD:0:7}）"
  fi
else
  echo "  （无法 fetch origin/master，已跳过远端比较，仅基于本地信息判断）" >&2
fi
if [ -n "$(git -C "${SCRIPT_DIR}" status --porcelain 2>/dev/null)" ]; then
  GIT_ISSUES="${GIT_ISSUES}\n  - 工作区有未提交改动"
fi
if [ -n "${GIT_ISSUES}" ]; then
  echo "⚠ 当前代码状态可能不是 master 最新：" >&2
  printf '%b\n' "${GIT_ISSUES}" >&2
  read -r -p "  仍要继续部署吗？(y/N) " reply </dev/tty || reply=""
  case "${reply}" in [yY]) ;; *) echo "已中止。" >&2; exit 1 ;; esac
fi
# ──────────────────────────────────────────────────────────────────────────

echo "==> [1/4] 检查服务器 ${SSH_HOST} 连通性"
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "${SSH_HOST}" true 2>/dev/null; then
  echo "✗ 无法通过 SSH 连接到 ${SSH_HOST}，已中止部署（未浪费构建时间）。" >&2
  echo "  请检查：网络、服务器是否在线、~/.ssh/config 中的 ${SSH_HOST} 别名与免密配置。" >&2
  exit 1
fi

echo "==> [2/4] 本地构建镜像 ${FULL_IMAGE}"
docker build -t "${FULL_IMAGE}" .

echo "==> [3/4] 传输镜像到 ${SSH_HOST}（管道直传，无中间文件）"
docker save "${FULL_IMAGE}" | ${SSH} docker load

echo "==> [4/4] 替换远端容器"
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
echo "部署完成！访问 http://${SSH_HOST}:${HOST_PORT}/"
