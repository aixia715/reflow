# CLAUDE.md

Reflow —— 单人使用的单板 BOM 状态管理工具。像 git 一样对硬件单板的 BOM 演进做**线性版本管理**（无分支、无合并）：差量存储、历史编辑自动传播 + 冲突确认、append-only 审计日志、稳定可分享链接。

## 运行与测试

```bash
. .venv/bin/activate          # 依赖已装在 .venv（pip install -e ".[dev]"）
uvicorn app.main:app --reload # 启动，访问 http://127.0.0.1:8000/
pytest                        # 全部测试（当前 74 passed）
```

数据库默认 `reflow.sqlite`，用环境变量 `REFLOW_DB` 覆盖（测试用 tmp 文件）。首次运行自动建表。

## 架构

三层：纯逻辑（零 Web/DB 依赖，测试重点）→ 数据访问层 → 薄路由（收请求 → 调逻辑 → 渲染模板）。

| 文件 | 职责 |
|---|---|
| `app/csv_import.py` | ★CSV 解析、拆分合并位号、校验报告（纯逻辑） |
| `app/validation.py` | ★位号编辑校验（纯逻辑） |
| `app/bom_engine.py` | ★折叠引擎：`fold_bom` / `resolve_reference` 沿差量链求解（纯逻辑） |
| `app/propagation.py` | ★传播 & 冲突检测/确认（核心算法） |
| `app/models.py` | SQLite 数据访问层（层级 / 节点 / changeset / 取链） |
| `app/audit.py` | append-only 审计日志 |
| `app/db.py` | 连接 + 五表 schema |
| `app/main.py` | FastAPI 装配；提供 `templates`、`get_conn` |
| `app/routes/{hierarchy,board,log}.py` | 路由：hierarchy=首页+统一新建单板；board=状态图/节点页/编辑/撤销/冲突/commit；log=审计日志（筛选） |
| `app/templates/`、`app/static/` | Jinja2 + HTMX 页面与样式 |

完整 BOM **读取时实时折叠**得出（初始 BOM + 沿父链叠加 changeset），不物化缓存。

## 核心数据模型

- 四级定位：单板名称 → PCB版本 → BOM版本 → 单板ID。前两级只是 `boards_hierarchy` 行上的文本字段，没有独立实体表；新建 BOM 版本时隐式创建。
- 初始 BOM 绑定在 **BOM版本** 层（`initial_bom` 表）；状态图（节点链）绑定在 **单板ID** 层。
- 节点只存相对父节点的 changeset（`node_changes`，`UNIQUE(node_id, reference)`）。根节点 `parent_id=NULL`，changeset 为空，初始 BOM 单独存 `initial_bom`。
- 工作区 = 一个 `is_committed=0` 的草稿节点挂在链末；commit 时翻成正式节点并新开空草稿。
- 「不贴」(DNP) = 该位号不在 BOM 中；没有单独的贴装标记字段。
- 节点 URL `/board/{boardId}/node/{nodeId}` 稳定，编辑内容不改 URL。

## 传播 & 冲突（最关键的逻辑）

编辑某节点某位号是「修正记录」。下游只是**继承**该位号的节点会随实时折叠自动变；只有下游节点**显式操作过**同一位号才冲突。链是线性的 → 至多一个下游冲突节点。冲突二选一：**保留下游值**（不动）/ **采用修正值**（删下游显式 op，让它重新继承，记 `propagated` 日志）。判定标准只看「下游 changeset 里有没有这条 reference」，与 op 类型无关。根节点修正改 `initial_bom` 行，走同样的下游冲突检测。

`propagation._children_in_order` **沿 parent_id 链游走**求下游，不要改回依赖 id 顺序——id 顺序只在生产环境恰好等于链顺序，不够稳健。

## 约定 / 注意事项

- **用中文沟通**；代码注释、docstring、UI 文案均为中文，错误消息也是中文，保持一致。
- **Starlette 1.2.1**：`TemplateResponse` 必须用新签名 `templates.TemplateResponse(request, "name.html", {context})`——`request` 第一个位置参数，context 里**不要**放 `"request"` 键。旧签名会抛 `TypeError`。
- 标识符用 surrogate key（SQLite AUTOINCREMENT），URL 用节点/单板 id；名称仅展示，重命名不破坏链接。
- 单人使用：`get_conn()` 每请求开一个连接、不显式关闭，对单用户 MVP 可接受。
- 改动遵循 TDD：先写失败测试再实现。纯逻辑模块（★）是测试投入重点。
- 前端约定：HTMX 局部刷新 + Alpine.js 客户端小交互（CDN，无构建）。校验失败返回 200 + `HX-Retarget: #form-error`；编辑/撤销成功返回 `_node_update.html`（主体换 `#bom`，OOB 换 `#changes-panel`、清 `#form-error`）+ `HX-Trigger: {"showToast": …}`（json.dumps 保持 ASCII）；整页跳转用 `?flash=` 显示 toast。htmx 事件在 Alpine 里监听要加 `.camel` 修饰符；模板向 hx-vals/JS 传值一律 `|tojson` 且属性用单引号。
- 撤销仅限工作区草稿（is_committed=0），实现为删 changeset 行，不记审计日志。
- 冲突确认是弹窗（`_conflict_modal.html`），取消 ≡ 全部「保留下游值」。
- 新建单板是唯一创建入口（`/board/new`），BOM 版本随之隐式创建；校验有问题禁止创建。

## 文档

- 设计 spec：`docs/superpowers/specs/2026-06-09-reflow-bom-tool-design.md`（另有 HTML 版）
- 实现计划：`docs/superpowers/plans/2026-06-09-reflow-bom-tool.md`
- 需求文档：`docs/Reflow-需求文档.md`

## MVP 边界（暂不做）

飞线等「位号以外」的修改类型、「按时间回看 / 状态回放」界面、多用户/并发/权限、工具内的测试记录功能（测试记录在外部笔记，本工具只提供链接）。
