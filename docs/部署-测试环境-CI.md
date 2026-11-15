# CI 与发版

合并到 `master` 不再自动部署测试服务器（原 `deploy.yml` 已移除）。测试环境如需更新，
按 README 的 Docker 部署方式在服务器上手动处理。

PR 仍由 `ci.yml` 调用 `_checks.yml` 自动跑 pytest 与 Docker 冒烟。

# 发版镜像 CI（推送到 GHCR）

`publish-image.yml` 在推 `v*.*.*` 格式的 git tag（如 `v1.2.0`）时触发，跑通
`_checks.yml`（pytest + docker 冒烟测试）后构建镜像并推送到
GitHub Container Registry，产物为 `ghcr.io/aixia715/reflow:<tag>` 与浮动的
`ghcr.io/aixia715/reflow:latest`。同一 tag 重推或短时间连发多个 tag 时靠
`concurrency` 串行执行，避免旧版本覆盖新版本的 `latest`。

登录用内置 `GITHUB_TOKEN`（`packages: write` 权限），不需要额外配置 Secrets/Variables。

**前提：tag 指向的提交必须已经在 `master` 分支历史里。** 流水线第一个 job
`verify-tag-on-master` 会用 `git merge-base --is-ancestor` 校验这一点，tag 打在
还没合并的分支/PR 提交上会直接报错终止，不会构建也不会发布镜像。

发版方式（先合并到 master，再对 master 上的提交打 tag）：

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
的冒烟测试——落回 `dev`），运行时可查：

```bash
curl http://<host>:<port>/version   # {"version": "v1.2.0"}
```

镜像 metadata 里也带了 `org.opencontainers.image.version` label，命令行或
GHCR 网页均可查看：

```bash
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' ghcr.io/aixia715/reflow:latest
```
