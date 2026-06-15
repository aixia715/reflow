# 硬更改（飞线/割线）功能设计

> Issue #5。在每个单板ID 下记录「硬更改」——飞线、割线等**位号以外**的物理改动。每条硬更改含标题、时间、文字说明、多张附图，按时间混排进状态图时间线。

## 背景与定位

- 本功能**主动突破** CLAUDE.md「MVP 暂不做：飞线等位号以外的修改类型」这条边界，经确认现在要做。
- 硬更改挂在**单板ID 层**（`boards_hierarchy` 行），就是状态图页 `/board/{board_id}` 对应的实体。
- 硬更改是**纯展示项**：独立存表，**不进 BOM 折叠引擎**，飞线/割线不增删位号；完整 BOM 仍只由 BOM 节点链（`nodes`/`node_changes`）实时折叠算出。
- 在状态图 timeline 上，硬更改卡片与 BOM 节点卡片**按时间混排**，让人一条线看全这块板的完整演进。
- 首版范围：**最小可用版**（不做缩略图、灯箱、拖拽排序）。

## 数据模型

两张新表，都不碰现有表和折叠引擎。

```sql
CREATE TABLE IF NOT EXISTS hard_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id    INTEGER NOT NULL REFERENCES boards_hierarchy(id),  -- 单板ID 层
    title       TEXT NOT NULL,                                     -- 标题
    description TEXT NOT NULL DEFAULT '',                          -- 文字说明
    occurred_at TEXT NOT NULL,                                     -- 发生时间（手填，默认当前）
    created_at  TEXT NOT NULL                                      -- 系统创建时间（不可改）
);

CREATE TABLE IF NOT EXISTS hard_change_images (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    hard_change_id INTEGER NOT NULL REFERENCES hard_changes(id),
    filename       TEXT NOT NULL,           -- 存盘名（uuid4 + 原扩展名，防重名/路径穿越）
    original_name  TEXT,                    -- 原始文件名（详情页展示用）
    sort_order     INTEGER NOT NULL DEFAULT 0,  -- 按上传顺序
    created_at     TEXT NOT NULL
);
```

## 文件存储

- 上传目录由环境变量 `REFLOW_UPLOAD_DIR` 配置，默认 `./uploads`（加进 `.gitignore`），沿用现有 `REFLOW_DB` 的风格。
- 落盘文件名 = `uuid4().hex + 原扩展名`，杜绝重名和路径穿越；DB 只存这个文件名。
- `main.py` 新挂静态路由 `/uploads` → `StaticFiles(上传目录)`；应用启动时 `mkdir(parents=True, exist_ok=True)` 确保目录存在。页面用 `<img src="/uploads/{filename}">` 引用。
- 删除硬更改时：删 DB 行 + 删对应磁盘文件。

## 时间线混排排序

状态图渲染时，把已提交 BOM 节点、硬更改、工作区草稿合并成一条按时间排序的 timeline：

| 时间线项 | 排序用时间戳 |
|---|---|
| 已提交 BOM 节点 | `committed_at` |
| 硬更改 | `occurred_at`（手填） |
| 工作区草稿（未提交节点） | —— **永远钉在末尾**（它是「当前正在做的」） |

**为什么不会与链顺序冲突**：`committed_at` 在 commit 那一刻写入一次（`models.py:225`），之后历史编辑只改 `node_changes`、不动它 → 单调递增 → 已提交节点按 `committed_at` 排 = parent_id 链顺序。所以「按时间排」与「按链排」对 BOM 节点是同一结果。硬更改的 `occurred_at` 是手填的，落到对应时间点位置（只在已提交节点之间穿插；草稿恒在末尾）。折叠引擎一行不动。

## 架构分层（沿用现有三层）

| 层 | 新增 | 职责 |
|---|---|---|
| 纯逻辑 ★ | `app/hard_change.py` | 时间线混排排序（merge BOM 节点 + 硬更改、草稿钉底）、上传校验（格式/大小/数量/标题必填）、安全文件名生成 |
| 数据访问 | `app/models.py` 加函数 | 硬更改 + 图片的增删改查 |
| 路由（薄） | `app/routes/hard_change.py` | 收请求 → 调逻辑 → 落盘 → 渲染；新文件避免 `board.py` 膨胀，在 `main.py` include |

## 路由

URL 都在单板ID 层下，稳定可分享。

```
GET  /board/{id}/hard-change/new          新建表单页
POST /board/{id}/hard-change              创建（multipart: title, occurred_at, description, files[]）
GET  /board/{id}/hard-change/{hid}        详情页
GET  /board/{id}/hard-change/{hid}/edit   编辑表单页
POST /board/{id}/hard-change/{hid}/edit   保存编辑（可删旧图 / 追加新图）
POST /board/{id}/hard-change/{hid}/delete 删除（删行 + 删磁盘文件）→ 跳回状态图
```

- 新建/编辑用**独立表单页 + 整页 multipart 提交**（文件上传整页最简单可靠，与现有 `board_new` 一脉相承）；成功用 `?flash=` 跳回状态图带 toast。
- 删除用 `hx-confirm` + 跳转回状态图。

## 模板（3 个新模板 + 1 处改造）

1. **改造 `state_graph.html`**：timeline 渲染硬更改卡片，复用 `.tl-item` 加修饰类 `.tl-item.hard` + 图标（如 🔧）区分 BOM 节点；页头加「＋ 记录硬更改」按钮。
2. **`hard_change_form.html`**（新建/编辑共用）：标题、时间（`datetime-local`，默认当前）、说明（textarea）、多图（`input file multiple`）；编辑态额外列出已有图 + 勾选删除 + 追加新图。
3. **`hard_change_detail.html`**：标题 / 时间 / 说明全文 + 多张大图（限宽展示）+ 编辑/删除入口 + 面包屑回状态图。

模板遵守前端风格指南（设计令牌、复用组件、两套主题自检）与 Starlette 1.2.1 `TemplateResponse` 新签名。

## 数据流（创建）

multipart 请求 → 路由取 title/occurred_at/description/files → 纯逻辑校验文件 → 落盘（uuid 名）→ `models` 插 `hard_changes` + `hard_change_images` → 跳回状态图 flash。

## 错误处理

- 标题必填；上传校验失败（非法格式 / 超 10MB / 超 12 张）→ 重渲染表单 + `flash-error`，**不落盘**（先校验后写盘）。
- 硬更改不存在 → 走现有 404 处理。
- 文案全中文，写清发生了什么、影响范围、下一步（前端风格指南要求）。

## 审计

硬更改**不进** `edit_log`（那是位号编辑日志）。硬更改本身就是独立记录，增删改不写审计，保持简单。

## 落地边界（容易漏）

1. **级联删除 ⚠️ 必做**：现有删单板（`DELETE /board/{id}`）、删 BOM 版本、删整组会删 `nodes`/`node_changes`；加硬更改后，这些路径**都要连带删 `hard_changes` + `hard_change_images` 行 + 磁盘图片文件**，否则留孤儿数据和文件。
2. `main.py` 挂 `/uploads` StaticFiles；启动时 `mkdir` 确保目录存在；`REFLOW_UPLOAD_DIR` 环境变量（测试用 tmp 目录）。
3. `.gitignore` 加 `uploads/`。
4. 图片约束常量：扩展名白名单 `{png, jpg, jpeg, webp, gif}`（**不含 svg**，svg 可藏脚本有 XSS 风险），单图 ≤ 10 MB，每条 ≤ 12 张；Content-Type 再校验一道。

## 测试策略（TDD，先写失败测试；纯逻辑是重点）

- **纯逻辑 ★**（`app/hard_change.py`，测试主战场）：
  - 混排排序：给定带 `committed_at` 的 BOM 节点 + 带 `occurred_at` 的硬更改 + 草稿 → 验证顺序、草稿钉底、硬更改按时间落位。
  - 上传校验：合法格式过 / 非法扩展名拒 / 超 10 MB 拒 / 超 12 张拒 / 空标题拒。
  - 安全文件名：uuid + 扩展名、路径穿越（`../`）被消除、扩展名提取正确。
- **数据访问 + 路由**：创建（含图）→ 查得到；编辑（增删图）；删除连带删图片行**和磁盘文件**；详情页渲染；multipart 上传端点；级联删除（删单板/版本/组后硬更改与图片文件一并清除）。
- **UI（playwright，沿用 `tests/test_delete_ui.py` 风格，只覆盖关键路径）**：状态图显示硬更改卡片、新建提交、详情多图展示。

## 不做（YAGNI / 本版边界）

- 缩略图生成、点图灯箱放大、附图拖拽排序。
- 硬更改参与 BOM 折叠 / 改变位号清单。
- 硬更改进审计日志、版本化、传播。
- 硬更改关联到具体 BOM 节点（本版只按时间混排，不建外键关联）。
