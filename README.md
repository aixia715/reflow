# Reflow — 单板 BOM 状态管理工具

像 git 一样对硬件单板的 BOM 演进做**线性版本管理**（无分支、无合并）。差量存储、历史编辑的自动传播 + 冲突确认、append-only 审计日志、稳定可分享链接。单人使用，SQLite 存储。

工具名一语双关：硬件的回流焊（reflow soldering），也描述核心机制——编辑上游节点后，改动向后「重新流动」传播。

## 运行

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

访问 http://127.0.0.1:8000/

数据库文件路径默认 `reflow.sqlite`，可用环境变量 `REFLOW_DB` 覆盖。

## 使用流程

1. **新建单板**：首页右上角「＋ 新建单板」，单板名称 / PCB版本 / BOM版本三项可输入下拉（选已有项直接复用，输新值隐式创建）；BOM 版本是新的时上传初始 BOM CSV，校验有问题会禁止创建。创建后自动以初始 BOM 为根节点 + 空工作区草稿。
2. **状态图**：进入单板查看线性的节点演进（最新在上，工作区置顶，每节点带改动摘要）；点节点看完整 BOM 与 diff 高亮。
3. **工作区改动 → commit**：在工作区草稿增/改/删位号（支持表格行内快捷操作、位号自动补全、修改项撤销），填说明文字提交成新节点。
4. **修正历史节点**：编辑某历史节点的位号触发传播；若下游显式改过同一位号则弹冲突确认（保留下游值 / 采用修正值并向后传播）。
5. **审计日志**：每次编辑追加一行，记录来源（直接 / 上游传播），可按位号/节点筛选。

每个节点都有稳定 URL `/board/{boardId}/node/{nodeId}`，可粘贴到外部笔记。

## 测试

```bash
pytest
```

## 打包成 Docker 镜像

仓库根目录有 `Dockerfile`，前端依赖（htmx / Alpine.js）已内置在 `app/static/vendor/`，镜像**完全自包含，运行时不需要外网**。

```bash
# 构建镜像（标签跟随 pyproject.toml 的版本号）
docker build -t reflow:0.1.0 .

# 导出为压缩包（用于拷贝到没有镜像仓库的目标机器）
docker save reflow:0.1.0 | gzip > reflow-0.1.0.tar.gz
```

得到的 `reflow-0.1.0.tar.gz` 可以通过 U 盘 / scp 等任意方式传到部署机。

## 部署

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

- **数据位置**：SQLite 数据库在容器内 `/data/reflow.sqlite`（由环境变量 `REFLOW_DB` 指定），硬更改上传图片在 `/data/uploads/`（由 `REFLOW_UPLOAD_DIR` 指定）——两者同在 `/data` 卷，重部署都不丢。想直接用主机目录存放，把 `-v reflow-data:/data` 换成 `-v /srv/reflow:/data`（备份时直接拷走目录里的 `reflow.sqlite` 和 `uploads/` 即可）。
- **升级版本**：导入新镜像后重建容器，数据卷不受影响：

  ```bash
  gunzip -c reflow-0.2.0.tar.gz | docker load
  docker stop reflow && docker rm reflow
  docker run -d --name reflow --restart unless-stopped \
    -p 8000:8000 -v reflow-data:/data reflow:0.2.0
  ```

- **备份**（使用命名卷时）：

  ```bash
  docker run --rm -v reflow-data:/data -v "$PWD":/backup python:3.12-slim \
    cp /data/reflow.sqlite /backup/reflow-backup.sqlite
  ```

## 结构

- `app/csv_import.py` — CSV 解析、拆分、校验（纯逻辑）
- `app/validation.py` — 位号编辑校验（纯逻辑）
- `app/bom_engine.py` — 折叠引擎：沿差量链解析完整 BOM（纯逻辑）
- `app/propagation.py` — 传播 & 冲突检测/确认（核心算法）
- `app/models.py` — SQLite 数据访问层
- `app/audit.py` — append-only 审计日志
- `app/routes/` — FastAPI 路由（hierarchy / board / log）
- `app/templates/`、`app/static/` — Jinja2 + HTMX 页面与样式

设计文档见 `docs/superpowers/specs/`，实现计划见 `docs/superpowers/plans/`。
