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

# 发版镜像 CI（推送到 GHCR）

`publish-image.yml` 与上面的 `deploy-test` 是独立流水线：推 `v*.*.*` 格式的 git tag（如
`v1.2.0`）时触发，跑通 `_checks.yml`（pytest + docker 冒烟测试）后构建镜像并推送到
GitHub Container Registry，产物为 `ghcr.io/aixia715/reflow:<tag>` 与浮动的
`ghcr.io/aixia715/reflow:latest`。同一 tag 重推或短时间连发多个 tag 时靠
`concurrency` 串行执行，避免旧版本覆盖新版本的 `latest`。

登录用内置 `GITHUB_TOKEN`（`packages: write` 权限），不需要额外配置 Secrets/Variables。

发版方式：

```bash
git tag v1.2.0
git push origin v1.2.0
```

GHCR 包默认 private，仓库协作者可直接 `docker pull`；若要让外部匿名 `docker pull`，
需去仓库 Packages 页面把该包手动设为 public。

## 确认拉到的镜像对应哪个版本

`latest` 是浮动 tag，光看这个名字不知道对应的是哪次发版。构建时通过
`--build-arg VERSION=<tag>` 把版本号烘焙进镜像（`ARG VERSION=dev` 放在
`RUN pip install` 之后，不影响依赖层缓存；未传该参数的构建——如 `_checks.yml`
的冒烟测试、`deploy.yml` 的测试构建——落回 `dev`），运行时可查：

```bash
curl http://<host>:<port>/version   # {"version": "v1.2.0"}
```

镜像 metadata 里也带了 `org.opencontainers.image.version` label，命令行或
GHCR 网页均可查看：

```bash
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' ghcr.io/aixia715/reflow:latest
```
