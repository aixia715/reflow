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
