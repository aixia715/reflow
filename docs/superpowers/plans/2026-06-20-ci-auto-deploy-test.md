# 合并后自动部署到测试服务器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每次代码合并到 master 后，CI 自动把最新 master 部署到测试服务器（测试通过才部署）。

**Architecture:** 把现有 `ci.yml` 里的 `tests` + `docker-smoke` 抽到可复用 workflow `_checks.yml`（`workflow_call`）；`ci.yml`（PR）与新增的 `deploy.yml`（push master）都 `uses:` 它。`deploy.yml` 复用 SSH 推送模型（GitHub 云 runner `docker build` → `docker save | ssh docker load` → 远端替换容器），远端替换逻辑内联进 workflow，不改 `deploy.sh`。

**Tech Stack:** GitHub Actions（reusable workflow / `workflow_call`）、Docker、SSH、Python 3.12、pytest。

**Spec:** `docs/superpowers/specs/2026-06-20-ci-auto-deploy-test.md`

## Global Constraints

- `_checks.yml` 的 `tests` job 用 **Python 3.12**（对齐生产镜像 `python:3.12-slim`）。
- 部署门控：`deploy.yml` 的部署 job 必须 `needs: checks`，测试/冒烟不过不部署。
- 镜像 tag：`reflow:test-<short-sha>`（部署用，唯一）+ `reflow:test`（浮动）；`docker save` 两个 tag 一起传。
- 触发：`deploy.yml` 用 `on: push: branches: [master]`。
- 并发：`deploy.yml` 顶层 `concurrency: { group: deploy-test, cancel-in-progress: false }`。
- 远端替换逻辑**内联**进 `deploy.yml`；**不修改** `deploy.sh` / `deploy.white-studio.sh`。
- SSH 主机校验用 `ssh-keyscan` 写 known_hosts，**不**用 `StrictHostKeyChecking=no`。
- Secrets：`TEST_SSH_HOST`、`TEST_SSH_USER`、`TEST_SSH_KEY`。Variables：`TEST_SSH_PORT`（默认 22）、`TEST_CONTAINER_NAME`（须 ≠ 生产容器名）、`TEST_HOST_PORT`、`TEST_DATA_VOLUME`。

---

## File Structure

| 文件 | 操作 | 职责 |
|---|---|---|
| `.github/workflows/_checks.yml` | 创建 | 可复用：`tests` + `docker-smoke` 两个 job（`on: workflow_call`） |
| `.github/workflows/ci.yml` | 改造 | PR 触发；`checks` 调 `_checks.yml`，`review` `needs: checks` |
| `.github/workflows/deploy.yml` | 创建 | push master 触发；`checks` 调 `_checks.yml`，`deploy-test` `needs: checks` |
| `docs/部署-测试环境-CI.md` | 创建 | 记录所需 Secrets/Variables 及设置/验证方法 |

校验工具：用 Python `yaml.safe_load` 解析所有 workflow（环境已有 PyYAML，`. .venv/bin/activate` 后可用）。如本机装了 `actionlint` 可额外跑一遍（非必需）。

---

## Task 1: 抽取可复用 `_checks.yml` 并改造 `ci.yml`

把 PR 门禁的测试/冒烟逻辑抽到可复用 workflow，`ci.yml` 改为调用它。两件事必须一起做：只抽不改 `ci.yml` 会留下重复，只改不抽 `ci.yml` 会引用不存在的文件。

**Files:**
- Create: `.github/workflows/_checks.yml`
- Modify: `.github/workflows/ci.yml`（整文件重写）

**Interfaces:**
- Produces: 可复用 workflow `./.github/workflows/_checks.yml`（`on: workflow_call`，无输入、无 secrets），含 job `tests` 与 `docker-smoke`。Task 2 也 `uses:` 它。
- Consumes: 无（基于已合并 #38 的 `ci.yml`）。

- [ ] **Step 1: 记录改造前 ci.yml 现状（基线）**

Run: `grep -nE 'python-version|pytest -q|docker build|needs:|uses:' .github/workflows/ci.yml`
Expected: 看到当前 `tests`、`docker-smoke`、`review` 三个 job 的内联定义，`review` 为 `needs: docker-smoke`，无任何 `uses:`。这是改造前状态，用于对照。

- [ ] **Step 2: 创建 `_checks.yml`**

创建 `.github/workflows/_checks.yml`，内容如下（`tests` 与 `docker-smoke` 即从现 `ci.yml` 原样迁出，python 维持 3.12）：

```yaml
name: checks

on:
  workflow_call:

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"          # 对齐生产镜像 python:3.12-slim
      - run: pip install -e ".[dev]"
      - run: playwright install --with-deps chromium   # e2e/UI 测试需浏览器
      - run: pytest -q

  docker-smoke:
    needs: tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - name: 构建镜像
        run: docker build -t reflow:ci .
      - name: 起容器并健康检查首页
        run: |
          docker run -d --name reflow-ci -p 8000:8000 reflow:ci
          for i in $(seq 1 30); do
            if curl -fsS http://localhost:8000/ >/dev/null; then
              echo "首页 200，构建冒烟通过"; docker rm -f reflow-ci; exit 0
            fi
            sleep 1
          done
          echo "容器 30 秒内未就绪"; docker logs reflow-ci; docker rm -f reflow-ci; exit 1
```

- [ ] **Step 3: 重写 `ci.yml` 改为调用 `_checks.yml`**

整文件替换为：

```yaml
name: ci

on:
  pull_request:
    types: [opened, reopened, synchronize]

jobs:
  checks:
    uses: ./.github/workflows/_checks.yml

  review:
    needs: checks
    if: github.actor != 'opencode-agent[bot]' && github.event.action != 'synchronize'
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 1
          persist-credentials: false
      - uses: anomalyco/opencode/github@latest
        env:
          OPENCODE_API_KEY: ${{ secrets.OPENCODE_API_KEY }}
        with:
          model: ${{ vars.OPENCODE_MODEL }}
          prompt: "审阅这个 PR 的改动：聚焦正确性、潜在 bug、与本仓库约定（见 CLAUDE.md）的一致性。用中文把结论作为评论发表。"
```

- [ ] **Step 4: 校验两个 YAML 解析通过**

Run:
```bash
. .venv/bin/activate
python -c "import yaml; yaml.safe_load(open('.github/workflows/_checks.yml')); yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"
```
Expected: 打印 `yaml ok`，无异常。

- [ ] **Step 5: 结构断言（确认改造正确）**

Run:
```bash
grep -nE "uses: ./.github/workflows/_checks.yml|needs: checks" .github/workflows/ci.yml
grep -nE "workflow_call|pytest -q|docker build -t reflow:ci" .github/workflows/_checks.yml
```
Expected:
- `ci.yml` 命中 `uses: ./.github/workflows/_checks.yml`（在 `checks` job）和 `needs: checks`（在 `review` job）。
- `_checks.yml` 命中 `workflow_call`、`pytest -q`、`docker build -t reflow:ci`。
- `ci.yml` 中**不再**出现 `pytest -q` 或 `docker build`（已迁出）：`grep -c "pytest -q" .github/workflows/ci.yml` 应为 `0`。

- [ ] **Step 6: 提交**

```bash
git add .github/workflows/_checks.yml .github/workflows/ci.yml
git commit -m "CI：抽取可复用 _checks.yml，ci.yml 改为调用它

将 tests + docker-smoke 提取到 _checks.yml（workflow_call），供 ci.yml 与后续 deploy.yml 共用，
测试/冒烟逻辑只维护一份。ci.yml 的 review 改为 needs: checks。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 新增 `deploy.yml` 与配置文档

新增 push-master 触发的部署 workflow，复用 `_checks.yml` 门控，远端替换逻辑内联。并写一份配置文档列清所需 Secrets/Variables 与验证步骤。

**Files:**
- Create: `.github/workflows/deploy.yml`
- Create: `docs/部署-测试环境-CI.md`

**Interfaces:**
- Consumes: `./.github/workflows/_checks.yml`（Task 1 产出）。
- Produces: 无（终端交付，部署到外部服务器）。

- [ ] **Step 1: 创建 `deploy.yml`**

创建 `.github/workflows/deploy.yml`，内容如下。注意远端 heredoc 用 `<<'REMOTE'`（单引号）防止 runner 端展开，`${{ }}` 由 Actions 在运行前替换、作为位置参数传给远端 bash（与 `deploy.sh` ⑥ 同款模式）：

```yaml
name: deploy-test

on:
  push:
    branches: [master]

concurrency:
  group: deploy-test
  cancel-in-progress: false

jobs:
  checks:
    uses: ./.github/workflows/_checks.yml

  deploy-test:
    needs: checks
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - name: 计算短 SHA
        id: sha
        run: echo "short=${GITHUB_SHA::7}" >> "$GITHUB_OUTPUT"

      - name: 构建镜像
        run: docker build -t reflow:test-${{ steps.sha.outputs.short }} -t reflow:test .

      - name: 配置 SSH
        run: |
          mkdir -p ~/.ssh
          printf '%s\n' "${{ secrets.TEST_SSH_KEY }}" > ~/.ssh/id_deploy
          chmod 600 ~/.ssh/id_deploy
          ssh-keyscan -p "${{ vars.TEST_SSH_PORT || '22' }}" -H "${{ secrets.TEST_SSH_HOST }}" >> ~/.ssh/known_hosts 2>/dev/null

      - name: 传输镜像到测试服务器
        run: |
          docker save reflow:test-${{ steps.sha.outputs.short }} reflow:test \
            | ssh -i ~/.ssh/id_deploy -p "${{ vars.TEST_SSH_PORT || '22' }}" \
                "${{ secrets.TEST_SSH_USER }}@${{ secrets.TEST_SSH_HOST }}" docker load

      - name: 替换远端容器
        run: |
          ssh -i ~/.ssh/id_deploy -p "${{ vars.TEST_SSH_PORT || '22' }}" \
            "${{ secrets.TEST_SSH_USER }}@${{ secrets.TEST_SSH_HOST }}" \
            bash -s -- \
              "${{ vars.TEST_CONTAINER_NAME }}" \
              "reflow:test-${{ steps.sha.outputs.short }}" \
              "${{ vars.TEST_HOST_PORT }}" \
              "${{ vars.TEST_DATA_VOLUME }}" <<'REMOTE'
            set -euo pipefail
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

            if [ -n "${OLD_IMAGE}" ] && [ "${OLD_IMAGE}" != "${FULL_IMAGE}" ]; then
              docker rmi "${OLD_IMAGE}" || true
            fi
            docker image prune -f
          REMOTE
```

- [ ] **Step 2: 校验 deploy.yml YAML 解析通过**

Run:
```bash
. .venv/bin/activate
python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml')); print('deploy.yml yaml ok')"
```
Expected: 打印 `deploy.yml yaml ok`，无异常。

- [ ] **Step 3: 结构断言**

Run:
```bash
grep -nE "branches: \[master\]|group: deploy-test|uses: ./.github/workflows/_checks.yml|needs: checks|reflow:test-" .github/workflows/deploy.yml
```
Expected: 命中 push master 触发、`concurrency` 组、`uses: _checks.yml`、`needs: checks`、SHA tag 构建——五项齐全。

- [ ] **Step 4: 创建配置文档**

创建 `docs/部署-测试环境-CI.md`：

````markdown
# 测试环境 CI 自动部署

每次合并到 `master`（含直接 push master）后，`deploy.yml` 在测试通过后自动把最新 master
部署到测试服务器。机制：GitHub 云 runner `docker build` → `docker save | ssh docker load` →
远端替换容器。镜像 tag 为 `reflow:test-<short-sha>`（+ 浮动 `reflow:test`）。

## 需要在仓库设置的 Secrets / Variables

Secrets（Settings → Secrets and variables → Actions → Secrets）：

| 名称 | 用途 |
|---|---|
| `TEST_SSH_HOST` | 测试服务器公网地址 |
| `TEST_SSH_USER` | SSH 用户名 |
| `TEST_SSH_KEY` | SSH 私钥（含 `-----BEGIN ... END-----` 全文） |

Variables（同页 → Variables）：

| 名称 | 示例 | 用途 |
|---|---|---|
| `TEST_SSH_PORT` | `22` | SSH 端口（缺省按 22） |
| `TEST_CONTAINER_NAME` | `reflow-test` | 测试容器名，**必须 ≠ 生产容器名** |
| `TEST_HOST_PORT` | `8001` | 测试容器对外端口 |
| `TEST_DATA_VOLUME` | `reflow-test-data` | `/data` 挂载源（命名卷或主机路径） |

用 gh CLI 设置示例：

```bash
gh secret set TEST_SSH_HOST  --body "your.test.host"
gh secret set TEST_SSH_USER  --body "deploy"
gh secret set TEST_SSH_KEY   < ~/.ssh/id_test_deploy   # 私钥文件
gh variable set TEST_SSH_PORT       --body "22"
gh variable set TEST_CONTAINER_NAME --body "reflow-test"
gh variable set TEST_HOST_PORT      --body "8001"
gh variable set TEST_DATA_VOLUME    --body "reflow-test-data"
```

服务器侧前置：`TEST_SSH_KEY` 对应的公钥已加入测试服务器 `~/.ssh/authorized_keys`；服务器已装 Docker。

## 验证

1. 设好上述 Secrets/Variables。
2. 合并任一 PR 到 master（或直接 push master）。
3. 在 Actions 看 `deploy-test` 工作流：`checks` → `deploy-test` 顺序执行。
4. 部署完成后 `curl http://<TEST_SSH_HOST>:<TEST_HOST_PORT>/` 应返回首页（200）。
5. 在测试服务器 `docker ps` 应看到名为 `<TEST_CONTAINER_NAME>` 的容器，镜像为 `reflow:test-<short-sha>`。

## 已知权衡

远端"先 stop/rm 旧容器再 run 新容器"，若 `docker run` 失败容器会处于停止态（测试环境可接受）。
零停机/回滚自动化非本期目标。
````

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/deploy.yml docs/部署-测试环境-CI.md
git commit -m "CI：新增合并后自动部署到测试服务器的 deploy.yml

push master 触发，needs checks（测试+冒烟通过才部署），复用 _checks.yml。
镜像打 reflow:test-<short-sha> + 浮动 reflow:test，docker save | ssh docker load 推送，
远端替换容器逻辑内联（不改 deploy.sh）。附配置文档说明所需 Secrets/Variables 与验证步骤。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 集成验证（需要用户配合）

workflow 的真实行为只能在 GitHub 上跑出来。本任务是一次端到端验证，需要用户先配置 Secrets/Variables。

**Files:** 无（仅验证）

- [ ] **Step 1: 确认 Secrets/Variables 已配置**

按 `docs/部署-测试环境-CI.md` 设好 7 项。确认：`gh secret list` 含 3 个 `TEST_SSH_*`；`gh variable list` 含 4 个 `TEST_*`。

- [ ] **Step 2: 开 PR 验证 `_checks.yml` 复用生效**

把本计划的分支开成 PR。观察 Actions：`ci` 工作流出现 `checks / tests`、`checks / docker-smoke`，随后 `review`。确认复用 workflow 正常被调用、冒烟通过后才评审。

- [ ] **Step 3: 合并后验证自动部署**

合并该 PR 到 master。观察 `deploy-test` 工作流：`checks` 通过 → `deploy-test` 执行。

- [ ] **Step 4: 验证测试服务器**

Run（本机或任意能访问测试服务器公网地址处）：
```bash
curl -fsS http://<TEST_SSH_HOST>:<TEST_HOST_PORT>/ >/dev/null && echo "测试环境部署成功"
```
Expected: 打印 `测试环境部署成功`。并在测试服务器 `docker ps` 看到 `<TEST_CONTAINER_NAME>` 容器跑着 `reflow:test-<short-sha>`。

---

## Self-Review

**Spec 覆盖：**
- push master 触发 → Task 2 deploy.yml ✓
- 部署前重跑 tests+docker-smoke 门控 → Task 2 `needs: checks` ✓
- 镜像 SHA tag + 浮动 tag、两 tag 一起 save → Task 2 Step 1 ✓
- SSH 推送模型、ssh-keyscan → Task 2 Step 1 ✓
- 远端替换内联、deploy.sh 不动 → Task 2 内联 heredoc，无 deploy.sh 改动 ✓
- 抽 `_checks.yml` 供两方复用 → Task 1 + Task 2 均 `uses:` ✓
- 并发串行化 → Task 2 `concurrency` ✓
- Secrets/Variables 清单 → Task 2 Step 4 文档 ✓
- 错误处理（测试不过不部署、stop/run 权衡）→ `needs` + 文档已知权衡 ✓
- 验证方式 → Task 3 ✓

**占位符扫描：** 无 TBD/TODO；所有 workflow/文档内容均为完整可用文本。

**类型/名称一致性：** `_checks.yml` 文件路径在 Task 1 产出、Task 2 消费一致；job 名 `checks`/`tests`/`docker-smoke`/`review`/`deploy-test` 全程一致；Secrets/Variables 名称与 Global Constraints 及文档逐字一致；SHA tag `reflow:test-<short-sha>` 在构建/传输/运行/验证各处一致。
