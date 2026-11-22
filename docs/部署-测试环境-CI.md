# 分支与测试环境部署

开发与验收走 `test` 和 `master` 两条长期分支。`test` 从 `master` 拉出。

1. 开发 PR 的 base 一律是 `test`。
2. PR 上的 `ci.yml` 通过后，把 PR 合并进 `test`。
3. push 到 `test` 触发 `deploy.yml`（工作流名 `deploy-test`）：先跑 `_checks.yml`（pytest + Docker 冒烟），通过才构建镜像并部署测试服务器。Actions 页面也可以 `workflow_dispatch` 手动跑同一条流水线。手动触发时选择 `test`；选了其他 ref，部署步骤会失败，不会替换容器。
4. 在测试服务器上验收。通过后从 `test` 开 PR 合并到 `master`，这一批改动整批进入 `master`。
5. 合并到 `master` 不部署、不打 tag、不发版。发版仍是之后手动推 `v*.*.*` tag，由 `publish-image.yml` 推 GHCR。该 workflow 不因这次改动而变化。

`ci.yml` 的 `pull_request` 没有 `branches` 过滤，指向 `test` 和指向 `master` 的 PR 都会跑检查。

## 部署在做什么

机制与 PR #154 删除前的 `deploy.yml` 相同：GitHub 云 runner `docker build` → `docker save | ssh docker load` → 远端替换容器。镜像 tag 为 `reflow:test-<short-sha>`，另加浮动 tag `reflow:test`。`concurrency` 组 `deploy-test` 串行执行，不取消进行中的部署。

远端只 `docker stop` / `docker rm` 旧容器，再用同一个 `TEST_DATA_VOLUME` 挂到 `/data` 重新 `docker run`。不执行 `docker volume rm`，不删库。卷里已有的 SQLite 和上传文件会留下来。

端口映射是 `-p 127.0.0.1:${TEST_HOST_PORT}:8000`。容器只听服务器本机。`TEST_HOST_PORT` 仍由仓库 Variable 原样传入，workflow 不另加缺省值。

## 访问测试服务器

在自己的电脑上开一条 SSH 隧道，把本机 `8001` 转到服务器的 `127.0.0.1:8001`（`8001` 即 `TEST_HOST_PORT`）：

```bash
ssh -N -L 8001:127.0.0.1:8001 <host>
```

`<host>` 用测试服务器的 SSH 地址（`TEST_SSH_USER@TEST_SSH_HOST`；SSH 端口不是 22 时加上 `-p`）。隧道保持开着，浏览器打开 http://127.0.0.1:8001/ 。

workflow 不创建管理员。带登录的版本第一次部署后，库里还没有用户时，在测试服务器上手动执行：

```bash
docker exec -it reflow python -m app.cli create-admin --username admin
```

容器名要和 Variable `TEST_CONTAINER_NAME` 一致。上面按 `reflow` 写；若变量是别的名字，换成那个名字。之后的部署继续挂同一个数据卷，已有账号还在，不必再跑。

升到按单板授权之后，重启会把还没有负责人的 BOM 版本交给最早的未停用管理员。若这次升级前库里已经有管理员，一般不用再手工改。还要补「没有所有者的单板」或「启动前就想指定负责人」时：

```bash
docker exec -it reflow python -m app.cli assign-orphans --username admin
```

普通用户：

```bash
docker exec -it reflow python -m app.cli create-user --username huaqing
```

再由所有者在网页「成员」页把人加进单板。这条命令不会让人看见全部单板。

管理员登录之后，也可以在网页 ⋯ 菜单的「用户管理」里新建账号、停用或启用、重置密码。所有者被停用的单板、负责人被停用的 BOM 版本，只在这一页按名称列出，管理员可以强制转让，但不会因此看到板的内容，也不会自动成为成员。用户管理那一版不需要迁移命令：没有新表，启动时沿用现有账号和单板。

审计操作人同样不用手工迁移，也不清库。容器起来时若 `edit_log` 还没有 `actor_user_id` 就补一列，已有记录留空、不删行，重复部署不会再改这些行。日志页上这些空行显示「（升级前）」。账号停用之后，历史里仍显示其登录名。

BOM 版本页同样不用手工迁移，也不清库。`initial_bom` 和已经有负责人的 `bom_versions` 原样保留，重复部署不会改这些行。容器起来之后，从单板的根节点进入 BOM 版本页，才能改这份共用的初始清单。单板页上改根节点会被拒绝。提交前列出每一块板的变化和冲突，确认后一次写完。两人同时改同一个普通节点，这一版仍是后保存的覆盖先保存的；BOM 版本页上的修正会拒绝后一次提交并提示刷新。

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
| `TEST_HOST_PORT` | `8001` | 服务器本机监听端口（只绑 `127.0.0.1`） |
| `TEST_DATA_VOLUME` | `reflow-test-data` | `/data` 挂载源（命名卷或主机路径）。每次部署都挂这一个卷 |

删除 workflow 文件不会删掉这些 Secrets / Variables。用 gh CLI 设置示例：

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

1. 确认上面的 Secrets / Variables 还在。
2. 本改动合并进 `master` 之后，从 `master` 建立并推送 `test`。这一下 push 就会把当时的 `master` 部署到测试服务器。
3. 之后把开发 PR 合并进 `test`，或在 Actions 里对 `test` 手动 `workflow_dispatch`。
4. 看 `deploy-test`：`checks` 通过后才跑 `deploy-test`。检查失败则不替换容器。
5. 在测试服务器上 `curl -fsS http://127.0.0.1:<TEST_HOST_PORT>/healthz` 应返回 `{"ok":true}`。未登录访问 `/` 是 303 到 `/login`，不是 200。自己的电脑上按上面的 SSH 隧道打开 http://127.0.0.1:8001/ ，用管理员登录后应能看到升级前就有的单板。
6. 测试服务器上 `docker ps` 应看到名为 `TEST_CONTAINER_NAME` 的容器，镜像为 `reflow:test-<short-sha>`，数据卷仍是 `TEST_DATA_VOLUME`。

## 已知权衡

远端先停掉并删除旧容器再启动新容器。`docker run` 失败时容器会处于停止态（测试环境可接受）。零停机和自动回滚不在这条流水线里。

# 发版镜像 CI（推送到 GHCR）

`publish-image.yml` 与上面的 `deploy-test` 是独立流水线。推 `v*.*.*` 格式的 git tag（如 `v1.2.0`）时触发，跑通
`_checks.yml`（pytest + docker 冒烟测试）后构建镜像并推送到
GitHub Container Registry，产物为 `ghcr.io/aixia715/reflow:<tag>` 与浮动的
`ghcr.io/aixia715/reflow:latest`。同一 tag 重推或短时间连发多个 tag 时靠
`concurrency` 串行执行，避免旧版本覆盖新版本的 `latest`。

登录用内置 `GITHUB_TOKEN`（`packages: write` 权限），不需要额外配置 Secrets/Variables。

**前提：tag 指向的提交必须已经在 `master` 分支历史里。** 流水线第一个 job
`verify-tag-on-master` 会用 `git merge-base --is-ancestor` 校验这一点，tag 打在
还没合并的分支/PR 提交上会直接报错终止，不会构建也不会发布镜像。

发版方式（先把验收过的 `test` 整批合并到 master，再对 master 上的提交打 tag；合并本身不打 tag）：

```bash
git checkout master && git pull
git tag v1.2.0
git push origin v1.2.0
```

GHCR 包默认 private，仓库协作者可直接 `docker pull`；若要让外部匿名 `docker pull`，
需去仓库 Packages 页面把该包手动设为 public。

## 确认拉到的镜像对应哪个版本

`latest` 是浮动 tag，光看这个名字不知道对应的是哪次发版。构建时通过
`--build-arg VERSION=<tag>` 把版本号烘焙进镜像（`ARG VERSION=dev` 放在
`RUN pip install` 之后，不影响依赖层缓存；未传该参数的构建——如 `_checks.yml`
的冒烟测试、`deploy.yml` 的测试环境构建——落回 `dev`），运行时可查：

```bash
curl http://<host>:<port>/version   # {"version": "v1.2.0"}
```

镜像 metadata 里也带了 `org.opencontainers.image.version` label，命令行或
GHCR 网页均可查看：

```bash
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' ghcr.io/aixia715/reflow:latest
```
