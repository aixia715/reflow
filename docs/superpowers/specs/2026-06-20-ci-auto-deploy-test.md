# 设计：合并到 master 后自动部署到测试服务器

日期：2026-06-20

## 背景与目标

目前部署是开发者本地手动跑 `deploy.sh` / `deploy.white-studio.sh`（SSH 推送式：本地 `docker build`
→ `docker save | ssh ... docker load` → 远端替换容器）。希望在**每次代码合并到 master 后，自动把最新
master 部署到测试服务器**，让测试机始终反映 master 当前状态，无需人工操作。

生产部署维持现状（仍走本地手动 `deploy.sh`），本设计只新增"测试环境自动部署"。

## 决策汇总（已与用户逐项确认）

| 项 | 决策 | 理由 |
|---|---|---|
| 触发 | `on: push: branches: [master]` | "PR 合并"在 git 层即 push master；测试机应反映 master 当前状态，不管代码经 PR 还是直接 push 进入 |
| 部署门控 | 部署前先重跑 `tests` + `docker-smoke`，全绿才部署 | 合并产生的新 master 提交本身未被直接测过 |
| 镜像 tag | commit 短 SHA（`reflow:test-<sha>`）+ 浮动 `reflow:test` | 唯一、可追溯，知道测试机跑的是哪次提交；版本号 tag 不每次变、会撞 |
| 部署机制 | 复用 SSH 推送模型（测试服务器公网可达） | 与现有 deploy.sh 一致，无需引入 registry |
| 远端替换逻辑 | **内联**进 deploy.yml（非交互版），`deploy.sh` 不改 | 可复用部分仅 ~10 行 docker 命令；deploy.sh 大半身体（git 自检、交互确认、别名配置）CI 都用不上，强行共享会耦合不同关注点 |
| workflow 结构 | 抽 `_checks.yml`（`workflow_call`），ci.yml 与 deploy.yml 共用 | 测试/冒烟逻辑只一份，改一处两边生效 |

## 架构

```
_checks.yml   (on: workflow_call)   ← 可复用：tests + docker-smoke
   ▲ uses                    ▲ uses
ci.yml (pull_request)    deploy.yml (push: master)
   → review (needs checks)    → deploy-test (needs checks)
```

三个 workflow 文件：

### 1. `.github/workflows/_checks.yml`（新增，可复用）

- `on: workflow_call`
- job `tests`：装 chromium 后全量 `pytest`（Python 3.12，对齐生产镜像）
- job `docker-smoke`（`needs: tests`）：`docker build` + 起容器 + curl 首页
- 内容即从 #38 的 ci.yml 中原样迁出的两个 job。

### 2. `.github/workflows/ci.yml`（改造现有）

- `on: pull_request: types: [opened, reopened, synchronize]`
- job `checks`：`uses: ./.github/workflows/_checks.yml`
- job `review`（`needs: checks`，`if: github.event.action != 'synchronize'`）：opencode 评审，冒烟过才评、每 PR 一次。

### 3. `.github/workflows/deploy.yml`（新增）

- `on: push: branches: [master]`
- `concurrency: { group: deploy-test, cancel-in-progress: false }`（串行化，防两次快速合并互相踩）
- job `checks`：`uses: ./.github/workflows/_checks.yml`
- job `deploy-test`（`needs: checks`）：见下数据流。

## 部署数据流（deploy.yml 的 `deploy-test` job）

```
push master → checks 通过
  1. checkout
  2. SHORT_SHA=${GITHUB_SHA::7}
  3. docker build -t reflow:test-$SHORT_SHA -t reflow:test .
  4. 写入 SSH 私钥（来自 Secret），ssh-keyscan $TEST_SSH_HOST >> known_hosts
  5. docker save reflow:test-$SHORT_SHA reflow:test | ssh $TEST_SSH_USER@$TEST_SSH_HOST -p $TEST_SSH_PORT docker load
       （同时传两个 tag，使远端也有浮动 reflow:test）
  6. ssh 远端执行（内联 heredoc，复刻 deploy.sh ⑥ 的非交互版）：
       OLD_IMAGE=$(docker inspect --format='{{.Config.Image}}' $TEST_CONTAINER_NAME || true)
       docker stop  $TEST_CONTAINER_NAME || true
       docker rm    $TEST_CONTAINER_NAME || true
       docker run -d --name $TEST_CONTAINER_NAME --restart unless-stopped \
         -p $TEST_HOST_PORT:8000 -v $TEST_DATA_VOLUME:/data reflow:test-$SHORT_SHA
       [ -n "$OLD_IMAGE" ] && [ "$OLD_IMAGE" != "reflow:test-$SHORT_SHA" ] && docker rmi "$OLD_IMAGE" || true
       docker image prune -f
```

镜像同时打 `reflow:test-$SHORT_SHA`（部署用，唯一）与 `reflow:test`（浮动）；步骤 5 两个 tag 一起
`docker save`，故测试服务器上两个 tag 都在，便于人工 `docker run reflow:test`。容器始终以唯一 SHA tag
运行，故 `OLD_IMAGE` 为上一次的 `reflow:test-<旧sha>`，与浮动 tag 不冲突；`docker image prune -f` 只清
悬空镜像，不会误删带 tag 的 `reflow:test`。

## 配置（GitHub Secrets / Variables，用户填写）

| 名称 | 类型 | 用途 |
|---|---|---|
| `TEST_SSH_HOST` | Secret | 测试服务器公网地址 |
| `TEST_SSH_USER` | Secret | SSH 用户名 |
| `TEST_SSH_KEY` | Secret | SSH 私钥（runner 登录用） |
| `TEST_SSH_PORT` | Variable | SSH 端口（默认 22） |
| `TEST_CONTAINER_NAME` | Variable | 测试容器名（**须 ≠ 生产容器名**，防误覆盖） |
| `TEST_HOST_PORT` | Variable | 测试容器对外端口 |
| `TEST_DATA_VOLUME` | Variable | `/data` 挂载源（命名卷或主机路径均可） |

## 错误处理 / 安全

- **测试不过不部署**：`deploy-test` `needs: checks`，tests 或 docker-smoke 红了则部署自动跳过。
- **并发串行化**：`concurrency` 组保证同一时刻只有一个测试部署在跑，避免两次快速合并的部署交错。
- **SSH 主机校验**：用 `ssh-keyscan` 显式写 known_hosts，不用 `StrictHostKeyChecking=no`。私钥仅以 Secret 注入，job 结束即随 runner 销毁。
- **已知权衡**：远端是"先 stop/rm 再 run"，若 `docker run` 失败，容器会处于停止态（旧容器已删）。测试环境可接受；如需零停机另说（非本期目标）。

## 测试 / 验证

部署链路无法本地完整跑，验证靠：

1. 三个 workflow YAML 过 `yaml.safe_load`。
2. `_checks.yml` 的复用在 PR 上可见生效（`ci / checks / tests`、`ci / checks / docker-smoke`）。
3. 首次真实合并到 master 触发 `deploy.yml`，部署后 `curl` 测试服务器公网地址确认新容器在跑、首页 200。
4. 故意推一个会让测试失败的提交到 master，确认 `deploy-test` 被跳过（即测试不过不部署）。

## 依赖与顺序

`_checks.yml` 是从 #38 的 `ci.yml` 拆出来的，本工作**建在 #38 之上**：建议 #38 先合并，再在 master 上做
本设计的改造与新增（实现计划会处理好顺序）。

## 非目标（本期不做）

- 不动生产部署：`deploy.sh` / `deploy.white-studio.sh` 保持不变，生产仍走本地手动。
- 不抽共享部署脚本（拒绝为 ~10 行 DRY 去改两个稳定脚本 + 同步机制）。
- 不引入容器 registry / 拉取式部署（self-hosted runner、watchtower 等）。
- 不做零停机 / 蓝绿 / 回滚自动化。
