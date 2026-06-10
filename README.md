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

1. **新建 BOM 版本**：首页 →「新建 BOM 版本」，填单板名称 / PCB版本 / BOM版本，上传 CSV → 预览校验 → 确认导入。
2. **新建单板**：在某 BOM 版本下填单板ID 一键创建（自动以初始 BOM 为根节点 + 空工作区草稿）。
3. **状态图**：进入单板查看线性的节点演进；点节点看完整 BOM 与 diff 高亮。
4. **工作区改动 → commit**：在工作区草稿增/改/删位号，填说明文字提交成新节点。
5. **修正历史节点**：编辑某历史节点的位号触发传播；若下游显式改过同一位号则弹冲突确认（保留下游值 / 采用修正值并向后传播）。
6. **审计日志**：每次编辑追加一行，记录来源（直接 / 上游传播）。

每个节点都有稳定 URL `/board/{boardId}/node/{nodeId}`，可粘贴到外部笔记。

## 测试

```bash
pytest
```

## 结构

- `app/csv_import.py` — CSV 解析、拆分、校验（纯逻辑）
- `app/bom_engine.py` — 折叠引擎：沿差量链解析完整 BOM（纯逻辑）
- `app/propagation.py` — 传播 & 冲突检测/确认（核心算法）
- `app/models.py` — SQLite 数据访问层
- `app/audit.py` — append-only 审计日志
- `app/routes/` — FastAPI 路由（hierarchy / board / log）
- `app/templates/`、`app/static/` — Jinja2 + HTMX 页面与样式

设计文档见 `docs/superpowers/specs/`，实现计划见 `docs/superpowers/plans/`。
