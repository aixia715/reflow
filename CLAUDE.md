# CLAUDE.md

Reflow —— 单人使用的单板 BOM 状态管理工具。像 git 一样对硬件单板的 BOM 演进做**线性版本管理**（无分支、无合并）：差量存储、历史编辑自动传播 + 冲突确认、append-only 审计日志、稳定可分享链接。

## 运行与测试

```bash
. .venv/bin/activate          # 依赖已装在 .venv（pip install -e ".[dev]"）
uvicorn app.main:app --reload # 启动，访问 http://127.0.0.1:8000/
pytest                        # 全部测试
```

数据库默认 `reflow.sqlite`，用环境变量 `REFLOW_DB` 覆盖（测试用 tmp 文件）。首次运行自动建表。

## 架构

三层：纯逻辑（零 Web/DB 依赖，测试重点）→ 数据访问层 → 薄路由（收请求 → 调逻辑 → 渲染模板）。

| 文件 | 职责 |
|---|---|
| `app/csv_import.py` | ★CSV 解析、拆分合并位号、校验报告；工作区导入的修改清单解析 + op 推断；导入预览的统一行表合成 `build_preview_rows`（纯逻辑） |
| `app/validation.py` | ★位号编辑校验（纯逻辑） |
| `app/component_lint.py` | ★元器件值 Lint：单位/SI 前缀标准化、量级归一、标准容差序列校验（电阻 E6~E192，电容/电感封顶 E6~E96），只对 R/C/L 位号生效（纯逻辑） |
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

- **改前端（`app/templates/`、`app/static/`）之前必读 `docs/前端风格指南.md`**：设计令牌（只用 CSS 变量、新颜色同步夜间模式）、组件清单（先复用再新建）、交互/文案规范、改完自检清单（两套主题都要实际查看）。
- **用中文沟通**；代码注释、docstring、UI 文案均为中文，错误消息也是中文，保持一致。
- **Starlette 1.2.1**：`TemplateResponse` 必须用新签名 `templates.TemplateResponse(request, "name.html", {context})`——`request` 第一个位置参数，context 里**不要**放 `"request"` 键。旧签名会抛 `TypeError`。
- 标识符用 surrogate key（SQLite AUTOINCREMENT），URL 用节点/单板 id；名称仅展示，重命名不破坏链接。
- 单人使用：`get_conn()` 每请求开一个连接、不显式关闭，对单用户 MVP 可接受。
- 改动遵循 TDD：先写失败测试再实现。纯逻辑模块（★）是测试投入重点。
- 前端约定：HTMX 局部刷新 + Alpine.js 客户端小交互（CDN，无构建）。校验失败返回 200 + `HX-Retarget: #form-error`；编辑/撤销成功返回 `_node_update.html`（主体换 `#bom`，OOB 换 `#changes-panel`、清 `#form-error`）+ `HX-Trigger: {"showToast": …}`（json.dumps 保持 ASCII）；整页跳转用 `?flash=` 显示 toast。htmx 事件在 Alpine 里监听要加 `.camel` 修饰符；模板向 hx-vals/JS 传值一律 `|tojson` 且属性用单引号。表单内某个字段需要独立于整表单提交做「输入即请求」的小交互（如失焦校验/联想），给该字段单独挂 `hx-post`/`hx-trigger`，返回 204 + `HX-Trigger: {"xxxLinted": {...}}` 携带数据，Alpine 用 `@xxx-linted.camel` 接收；此时表单级 `@htmx:after-request` 处理器必须用 `$event.target === $el` 排除这类嵌套请求（htmx 事件会冒泡），否则会被误判为表单提交成功——现存守卫示例见 `_edit_form.html` 的 `@htmx:after-request` 处理器。
- 撤销仅限工作区草稿（is_committed=0），实现为删 changeset 行，不记审计日志。「清除全部修改」（`/undo-all`）同语义，只是一次删光整个 changeset。
- 冲突确认是弹窗（`_conflict_modal.html`），取消 ≡ 全部「保留下游值」。
- **插入节点页的改动暂存在浏览器，但校验不许留在前端**：位号校验、op 推断、元器件 lint 一律走 `/board/{id}/node/{pid}/insert/check`（204 + `HX-Trigger: insertChecked`），JS 只负责显示与暂存——这三样曾在 `insert_node.html` 里各抄过一份，与 `app/validation.py` 各改各的必然漂移。日期控件的 min/max 同理，由 `validation.insert_time_bounds` 算好下发，与 `validate_insert_time` 的开区间同源（控件是闭区间且只能产出整分钟，自己截秒必然两头漏）。
- **草稿里把值改回上游原样 = 这条修改不留痕，但审计日志照记**：`propagation.apply_node_edit(..., drop_noop=True)` 删掉 changeset 行，否则「本节点修改」面板会显示一条改前改后相同的假修改。`drop_noop` 只给交互式单条编辑（`/edit`、`/workspace/edit`）用，**批量写入路径必须保持默认 False**——copy-to-draft 从紧邻父节点复制时逐条都等于父节点原值，开了会被全吃掉，界面上就成了「提示复制了 N 条、面板里一条没有」。已提交节点也一律不丢：它的显式 op 还承担屏蔽上游修正的作用（判冲突只看下游有没有这条 reference，与值无关）。
- 插入节点页与工作区草稿**功能保持同步**（它是工作区编辑器的客户端复刻版，历史上漏过一批）：清除全部修改、撤销/添加的 toast、筛选框回车取首行位号+当前值、点「修改」滚动到编辑面板、位号框回车先补 Part（`refEnter`，两页同一行为）。插入页改动只在浏览器里，toast 自己往 body 派 `showToast`、离开前有 `beforeunload` 守卫（保存成功要用 `saving` 解除，HX-Redirect 也是一次 unload）；`/insert/check` 失败必须落到 `editError`，全站没有 htmx 错误兜底，不接就是点了毫无反应。CSV 导入同理走 `/insert/import/preview`：解析求差复用 `csv_import` 的同一套函数，**只算不写库**，「应用」不是请求而是把服务端换算好的清单合进浏览器暂存。
- **插入页的两张 BOM 不要混用**（`_insert_boms`）：校验与「相对屏幕」的求差用**当前视图**（父节点折叠 + 本页暂存），否则「先手工加一条再导入」会算错；而回给前端落库用的 op 一律用 `bom_engine.stage_change` 按**父节点**换算（暂存里 add 过的位号再改，落库仍是一条 add），值回到父节点原样的标成 `action=drop`。单条编辑与批量导入共用 `stage_change` 这一份判定，别再各写一遍。
- 新建单板是唯一创建入口（`/board/new`），BOM 版本随之隐式创建；校验有问题禁止创建。
- **时间统一**：存储层一律 canonical UTC（`YYYY-MM-DDTHH:MM:SS+00:00`，见 `models._now`）；硬更改 `occurred_at` 由前端在提交时转 UTC；展示层用 `<time class="local-dt" datetime="UTC">` + `base.html` 的 `renderLocalDates()` 渲染为浏览器本地时间。历史旧数据用 `scripts/migrate_occurred_at_utc.py`（按新加坡 +08:00）一次性迁移。

## 文档

- 前端风格指南：`docs/前端风格指南.md`（改前端必读）
- 设计 spec：`docs/superpowers/specs/2026-06-09-reflow-bom-tool-design.md`（另有 HTML 版）
- 实现计划：`docs/superpowers/plans/2026-06-09-reflow-bom-tool.md`

## MVP 边界（暂不做）

飞线等「位号以外」的修改类型、「按时间回看 / 状态回放」界面、多用户/并发/权限、工具内的测试记录功能（测试记录在外部笔记，本工具只提供链接）。
