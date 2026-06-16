# 四级定位名称重命名功能 — 设计文档

- **Issue**: #20 增加对四级定位的名称的修改的功能（high priority）
- **日期**: 2026-06-16
- **目标**: 允许对四级定位（单板名称 / PCB版本 / BOM版本 / 单板ID）的名称做修改。

## 背景与约束

四级定位在数据库里是**去规范化的文本**：

- `board_name` / `pcb_version` / `bom_version` 三个字段**同时冗余存在** `boards_hierarchy` 和 `initial_bom` 两张表，并充当二者 JOIN 的文本连接键。
- `board_uid` 只在 `boards_hierarchy`；URL 用整数 `board_id`，所以重命名天然不破坏链接（CLAUDE.md："名称仅展示，重命名不破坏链接"）。

因此"重命名"= 对匹配行做 `UPDATE`，且前三级必须**两表同步更新**。

现有强可参照模式：首页 `home.html` 按 `board_name` 分组（🗑 删整组）→ 每个 `(PCB版本, BOM版本)` 配对行（🗑 删 BOM 版本）→ 每块单板 `board_uid` chip（× 删单板）。删除端点 `/board-group`、`/bom-version`、`/board/{id}` 都在 `routes/hierarchy.py`。删除图标本就是 **hover-reveal**（`.del-icon { opacity:0 }`，悬停才浮现）。

## 决策（已与用户确认）

1. **级联范围**：按层级级联。改 `board_name` 级联其下全部；改 `pcb_version` 级联该 PCB 下所有 BOM 版本；改 `bom_version` 只动该三元组；改 `board_uid` 只动该单板行。
2. **冲突处理**：新名与同层已存在名冲突 → **拒绝并返回中文错误，不做任何修改**。不做合并。
3. **交互形态**：每个层级一个悬停浮现的 **⋯ 菜单**（含「重命名 / 删除」）。点「重命名」→ 名字**就地变输入框**内联编辑。
4. **成功后**：整页 `HX-Redirect` 重渲染（非局部 OOB 补丁），保证元素内嵌的删除/重命名 URL 按新名重新拼接。
5. **审计**：重命名是元数据变更，**不写 `edit_log`**。

## 数据层（`app/models.py`，与 `delete_*` 并列）

四个函数。前三级两表同步 `UPDATE`，冲突命中即抛异常（中文消息），不修改任何行。新名 == 旧名时视为 no-op 直接成功。

| 函数 | 更新范围 | 冲突判定（命中即拒绝） |
|---|---|---|
| `rename_board_name(conn, old, new)` | 两表中 `board_name=old` 的所有行 | 存在任意行 `board_name=new` |
| `rename_pcb_version(conn, board_name, old, new)` | 两表中 `board_name=X AND pcb_version=old` | 存在 `board_name=X AND pcb_version=new` |
| `rename_bom_version(conn, board_name, pcb_version, old, new)` | 两表中匹配三元组前缀 | 存在 `(X, pcb, new)` |
| `rename_board_uid(conn, board_id, new)` | `boards_hierarchy` 单行 | 复用现有 `board_uid_exists`：同 `(board_name,pcb,bom)` 内重名 |

- 冲突异常类型：复用 `ValueError`（消息为中文），路由层捕获转 toast。
- `board_uid` 不在 `initial_bom`，只更新 `boards_hierarchy`。

## 纯逻辑（`app/validation.py`）

```python
def validate_new_name(new: str) -> str | None:
    """新名 trim 后非空校验。返回中文错误消息或 None。"""
```

仅做"trim 后非空"。冲突校验需查库，留在 models 层。

## 路由（`app/routes/hierarchy.py`，与 delete 路由同处）

四个 `POST` 端点，参数 = 旧标识 + `new_name`（提交前 `_strip`）：

- `POST /board-group/rename` — `board_name`(旧) + `new_name`
- `POST /pcb-version/rename` — `board_name`, `pcb_version`(旧) + `new_name`
- `POST /bom-version/rename` — `board_name`, `pcb_version`, `bom_version`(旧) + `new_name`
- `POST /board/{board_id}/rename` — `new_name`（`board_id` 定位行）

流程：

1. `validate_new_name` 空校验失败 → 返回 200 + `HX-Trigger` 弹 `showToast` 中文错误，输入框保留。
2. 调 models 重命名；捕获 `ValueError`（冲突）→ 同样 200 + `showToast` 错误。
3. 成功 → `HX-Redirect` 回 `/` 重渲染（复用现有 `_hx_redirect` 思路）。

`HX-Trigger` 的 JSON 用 `json.dumps(..., ensure_ascii=...)` 保持与现有 toast 约定一致（参考 board.py 现有写法）。

## 前端（`app/templates/` + `app/static/style.css`）

> 改前端先读 `docs/前端风格指南.md`。

- 现有每处裸 🗑/× 换成 **⋯ 菜单**（Alpine `x-data="{open:false, editing:false}"` 下拉）。沿用 hover-reveal：静止态零图标，悬停浮现 ⋯。各场景菜单项：
  - **分组标题**（board_name）：「重命名 / 删除整组」
  - **版本行**（同时显示 PCB版本 + BOM版本两个可改字段）：「重命名 PCB版本 / 重命名 BOM版本 / 删除 BOM 版本」
  - **单板 chip**（board_uid）：「重命名 / 删除单板」
- 点某个「重命名 X」→ `editing` 指向对应字段，该名字就地变 `<input>`（`x-ref`），Enter 或失焦 `hx-post` 提交，Esc 取消还原。版本行有两个可改字段，编辑态需区分当前在改哪个。
- 用一个 **Jinja macro**（如 `app/templates/_row_menu.html`）参数化覆盖 group / version / chip 三种场景，避免复制（"先复用再新建"）。
- 新增 `.menu` / `.menu-pop` 等样式；**任何新颜色变量必须同步在 `[data-theme="dark"]` 给出夜间值**，否则夜间模式破。
- htmx 成功事件在 Alpine 监听加 `.camel` 修饰符；向 hx-vals/JS 传值一律 `|tojson` 且属性用单引号。

## 测试（TDD，先红后绿）

- **纯逻辑** (`tests/`)：`validate_new_name` 空串 / 纯空白 → 报错；正常名 → None。
- **models**：每个 rename 的 happy path（两表都更新、值正确）；级联正确性（`board_name` 跨多个 PCB/BOM 版本一并改）；冲突抛 `ValueError` 且**未改任何行**；no-op（新==旧）；`board_uid` 同版本重名拒绝。沿用现有 tmp 库 fixture。
- **路由**：成功返回 `HX-Redirect`；冲突返回 200 + toast 错误。各一条冒烟测试。

## 不做（YAGNI）

- 不做合并语义、不做改名历史/审计、不在子页面（状态图/节点页/日志页）另设改名入口——首页 ⋯ 菜单是唯一管理面。
- 不顺手拆分偏大的 `models.py`，rename 与 delete 并列放置，避免无关重构。

## 影响文件清单

- `app/validation.py`（新增纯函数）
- `app/models.py`（新增 4 个 rename 函数）
- `app/routes/hierarchy.py`（新增 4 个路由）
- `app/templates/home.html` + 新 `app/templates/_row_menu.html`（macro）
- `app/static/style.css`（菜单样式 + 夜间变量）
- `tests/`（纯逻辑 / models / 路由）
