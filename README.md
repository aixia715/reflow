# Reflow — 单板 BOM 状态管理工具

像 git 一样对硬件单板的 BOM 演进做**线性版本管理**（无分支、无合并）。差量存储、历史编辑的自动传播 + 冲突确认、append-only 审计日志、稳定可分享链接。SQLite 存储。未登录不能打开业务页面；登录后只能看到自己被加入的单板。

工具名一语双关：硬件的回流焊（reflow soldering），也描述核心机制——编辑上游节点后，改动向后「重新流动」传播。

## 运行

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

访问 http://127.0.0.1:8000/ 。第一次使用要先创建管理员，见下一节。

数据库文件路径默认 `reflow.sqlite`，可用环境变量 `REFLOW_DB` 覆盖。

## 账号与单板权限

不开放注册。库里还没有用户时，登录页只提示去服务器执行命令，页面上不能设密码。

登录之后只能看见自己至少是只读成员的单板。一块板有三种角色：所有者、可编辑、只读。所有者在单板页的「成员」里把人加进来、改角色或转让。系统管理员身份不会自动打开单板内容。

若生产环境还停在只合并了登录闸门、还没有本阶段的版本，则登录后的人仍然能改所有板。以当前代码为准时，没有成员关系就进不了那块板。

首选命令行创建第一个管理员。密码从终端读入，不进命令参数，也不进 shell 历史：

```bash
python -m app.cli create-admin --username admin
```

容器里：

```bash
docker exec -it reflow python -m app.cli create-admin --username admin
```

如果库里已经有单板，它们会归这个管理员，这些板所在的 BOM 版本也由他负责。库里已经有用户之后，这条命令不会重置密码，也不是后门。

普通用户：

```bash
python -m app.cli create-user --username huaqing
```

这条命令不把人加进任何单板。加成员在网页上做：打开单板，⋯ 菜单里的「成员」。

系统管理员也可以在网页上建账号：登录后打开右上角 ⋯ 菜单里的「用户管理」。可以新建账号（勾选「设为系统管理员」就是管理员）、停用、重新启用、重置别人的密码。没有自助注册，也没有删除账号。停用或重置密码会立刻让对方已有的登录失效。不能停用自己，也不能停用最后一位仍可登录的系统管理员。管理员改自己的密码仍走「修改密码」，当前这次登录保持有效。

同一页列出「所有者已停用的单板」和「负责人已停用的 BOM 版本」，只有名称和登录名，不能从这里打开单板或看到 BOM。把单板转让给另一名未停用用户后，对方成为所有者，原来的所有者降为可编辑；管理员自己不会因此变成成员，转让完仍然打不开这些板。BOM 版本只更换负责人，各板成员不变。

升级已有库时，启动会建 `bom_versions`，并把还没有负责人的 BOM 版本交给最早的未停用管理员。已经有负责人的不会改。若希望空缺归指定的管理员，在新版本第一次被访问之前执行：

```bash
python -m app.cli assign-orphans --username admin
```

它只补还没有所有者的单板，以及还没有负责人的 BOM 版本。服务已经用新版本启动过的话，版本空缺通常已经被最早的管理员领走，这条命令不会再改。

单板页上的根节点只读。共用的初始 BOM 要等 BOM 版本页才能改；在那之前，网页上暂时不能改初始 BOM。这块板自己的差异用本板上的新节点记录。

无人值守的第一次启动可以同时设置 `REFLOW_BOOTSTRAP_USERNAME` 和 `REFLOW_BOOTSTRAP_PASSWORD`（至少 8 位）。只在用户表为空时创建一次，不会改已有账号的密码。不要把这两个变量写进镜像。用过就从运行环境里删掉：`docker inspect` 看得到环境变量。缺一个、或密码太短，进程照常起来，`/healthz` 仍然返回 200，登录页继续提示用命令行创建。

| 变量 | 默认 | 作用 |
|---|---|---|
| `REFLOW_BOOTSTRAP_USERNAME` | 空 | 仅当库中无用户时，和密码一起创建第一个管理员 |
| `REFLOW_BOOTSTRAP_PASSWORD` | 空 | 同上。至少 8 位。用过即删除 |
| `REFLOW_COOKIE_SECURE` | `0` | 设为 `1` 时会话和 CSRF cookie 加 `Secure`。当前部署是 HTTP，保持 `0` |
| `REFLOW_SESSION_DAYS` | `14` | 会话从最近活动起的有效天数 |

没有 `REFLOW_SECRET_KEY`。会话存在数据库里，cookie 只放随机令牌。

## 使用流程

1. **新建单板**：首页右上角「＋ 新建单板」，单板名称 / PCB版本 / BOM版本三项可输入下拉（只列出自己看得到的，以及自己负责的版本）。全新的 BOM 版本要上传初始 BOM CSV，创建者成为该版本负责人和这块板的所有者。已有 BOM 版本下再建板，只有该版本负责人可以。校验有问题会禁止创建。创建后自动以初始 BOM 为根节点 + 空工作区草稿。
2. **状态图**：进入单板查看线性的节点演进（最新在上，工作区置顶，每节点带改动摘要）；点节点看完整 BOM 与 diff 高亮，「下载 CSV」可二选一导出：**完整 BOM**（两列 `Reference,Part`，不贴位号不导出）或**本节点修改项**（三列 `Reference,Part,OP`，即本节点相对上一个节点的改动，可再作为修改清单导入到别处重放）。
3. **工作区改动 → commit**：在工作区草稿增/改/删位号（支持表格行内快捷操作、位号自动补全、修改项撤销），填说明文字提交成新节点。
4. **修正历史节点**：编辑某历史节点（根节点除外）的位号触发传播；若下游显式改过同一位号则弹冲突确认（保留下游值 / 采用修正值并向后传播）。根节点是初始 BOM，单板页不能改。
5. **审计日志**：每次编辑追加一行，记录来源（直接 / 上游传播），可按位号/节点筛选。

每个节点都有稳定 URL `/board/{boardId}/node/{nodeId}`，可粘贴到外部笔记。

## 测试

```bash
pytest
```

## 分支与测试部署

长期分支是 `test` 和 `master`。`test` 从 `master` 拉出。

- 开发 PR 指向 `test`。`ci.yml` 不限 base 分支，指向 `test` 与指向 `master` 的 PR 都会跑 pytest 和 Docker 冒烟。CI 通过后把 PR 合并进 `test`。
- push 到 `test` 自动部署测试服务器（`.github/workflows/deploy.yml`，工作流名 `deploy-test`）。Actions 里也可以对 `test` 手动触发同一条流水线。部署前先再跑一遍 `_checks.yml`，通过才替换容器；数据卷保留，不清库。
- 在测试服务器上验收。通过后从 `test` 开 PR 合并到 `master`，这一批改动整批进入 `master`。合并 `master` 不部署，也不打 tag。
- 发版仍是下面的手动 tag，`publish-image.yml` 不变。Secrets、数据卷和首次 `create-admin` 见 `docs/部署-测试环境-CI.md`。

## 发版（触发 GHCR 自动构建镜像）

推 `v*.*.*` 格式的 git tag 会触发 `publish-image.yml`，构建并推送镜像到
`ghcr.io/aixia715/reflow`。**前提：tag 指向的提交必须已经在 `master` 分支历史里**
——CI 里 `verify-tag-on-master` 这一步会用 `git merge-base --is-ancestor` 校验，
tag 打在还没合并的分支/PR 上会直接报错终止，不会构建发布。

正确顺序：验收后把 `test` 整批合并到 `master`，再对 `master` 上的提交打 tag。合并本身不会打 tag，也不会部署：

```bash
git checkout master && git pull
git tag v0.1.2
git push origin v0.1.2
```

## 从 GHCR 拉取镜像（推荐，联网环境）

```bash
docker pull ghcr.io/aixia715/reflow:v0.1.2   # 指定版本
# 或
docker pull ghcr.io/aixia715/reflow:latest   # 最新发版

docker run -d --name reflow --restart unless-stopped \
  -p 8000:8000 \
  -v reflow-data:/data \
  ghcr.io/aixia715/reflow:v0.1.2
```

访问 `http://<部署机IP>:8000/`。GHCR 包默认 private，仓库协作者本地已用
`gh auth login` / `docker login ghcr.io` 登录即可直接 `pull`；若需要外部匿名拉取，
要去仓库 Packages 页面把该包手动设为 public。

`latest` 是浮动 tag，看名字分不清对应哪次发版，可用 `/version` 接口或镜像 label 确认：

```bash
curl http://<部署机IP>:8000/version   # {"version": "v0.1.2"}
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' ghcr.io/aixia715/reflow:v0.1.2
```

升级版本、数据卷位置、备份方式和下面「部署（离线导入）」小节一致（`docker run` 换成
`ghcr.io/aixia715/reflow:<新 tag>` 即可，`-v reflow-data:/data` 照用），不重复贴一遍。

## 打包成 Docker 镜像（离线环境，无镜像仓库时用）

仓库根目录有 `Dockerfile`，前端依赖（htmx / Alpine.js）已内置在 `app/static/vendor/`，镜像**完全自包含，运行时不需要外网**。

```bash
# 构建镜像（标签跟随 pyproject.toml 的版本号）
docker build -t reflow:0.1.0 .

# 导出为压缩包（用于拷贝到没有镜像仓库的目标机器）
docker save reflow:0.1.0 | gzip > reflow-0.1.0.tar.gz
```

得到的 `reflow-0.1.0.tar.gz` 可以通过 U 盘 / scp 等任意方式传到部署机。

## 部署（离线导入）

目标机器只需要装有 Docker：

```bash
# 1. 导入镜像
gunzip -c reflow-0.1.0.tar.gz | docker load

# 2. 启动容器（数据库放在名为 reflow-data 的卷里，升级镜像不丢数据）
docker run -d --name reflow --restart unless-stopped \
  -p 8000:8000 \
  -v reflow-data:/data \
  reflow:0.1.0
```

访问 `http://<部署机IP>:8000/`。

- **数据位置**：SQLite 数据库在容器内 `/data/reflow.sqlite`（由环境变量 `REFLOW_DB` 指定），硬更改上传图片在 `/data/uploads/`（由 `REFLOW_UPLOAD_DIR` 指定）——两者同在 `/data` 卷，重部署都不丢。想直接用主机目录存放，把 `-v reflow-data:/data` 换成 `-v /srv/reflow:/data`。数据库已开 WAL，正在写入时不要只拷 `reflow.sqlite` 一个文件，用下面的备份方式。
- **第一次打开**：容器起来后还不能登录。执行 `docker exec -it reflow python -m app.cli create-admin --username admin`，密码在终端里输入两遍。升级老库时，这条命令会把当时还没有所有者的单板、以及还没有负责人的 BOM 版本认领给这个管理员。已经有管理员的库，重启新版本就会把尚无负责人的版本交给最早的未停用管理员；还想补无主单板时再跑 `python -m app.cli assign-orphans --username admin`。普通用户用 `python -m app.cli create-user --username <登录名>`，或由管理员在网页「用户管理」里创建，再由所有者在网页上加入单板。
- **升级版本**：导入新镜像后重建容器，数据卷不受影响：

  ```bash
  gunzip -c reflow-0.2.0.tar.gz | docker load
  docker stop reflow && docker rm reflow
  docker run -d --name reflow --restart unless-stopped \
    -p 8000:8000 -v reflow-data:/data reflow:0.2.0
  ```

- **备份**（使用命名卷时）：WAL 模式下直接 `cp` 正在使用的 `reflow.sqlite` 会抄到一半。用 SQLite 的 backup API，它会把 `-wal` 里还没写回的页一起带上：

  ```bash
  docker run --rm -i -v reflow-data:/data -v "$PWD":/backup --entrypoint python python:3.12-slim - <<'PY'
  import sqlite3
  src = sqlite3.connect("/data/reflow.sqlite")
  dst = sqlite3.connect("/backup/reflow-backup.sqlite")
  with dst:
      src.backup(dst)
  src.close()
  dst.close()
  PY
  ```

  容器已经停掉时，也可以把 `reflow.sqlite`、`reflow.sqlite-wal`、`reflow.sqlite-shm` 三个文件一起拷走。图片目录 `uploads/` 仍是直接拷贝。

## 结构

- `app/csv_import.py` — CSV 解析、拆分、校验（纯逻辑）
- `app/validation.py` — 位号编辑校验（纯逻辑）
- `app/bom_engine.py` — 折叠引擎：沿差量链解析完整 BOM（纯逻辑）
- `app/bom_export.py` — 折叠后 BOM 导出为 CSV（纯逻辑）
- `app/propagation.py` — 传播 & 冲突检测/确认（核心算法）
- `app/models.py` — SQLite 数据访问层
- `app/audit.py` — append-only 审计日志
- `app/routes/` — FastAPI 路由（hierarchy / board / log）
- `app/templates/`、`app/static/` — Jinja2 + HTMX 页面与样式

历史设计文档在 `docs/superpowers/`，仅供参考。
