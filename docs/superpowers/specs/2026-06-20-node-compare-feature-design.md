# 节点对比功能 + 时间统一 · 设计

> 对应 Issue #34「增加两个节点的对比功能」。需求经 Visual Companion 与用户逐屏对齐。

## 1. 目标

1. **节点对比**：在一块单板内任选两个节点，对比其完整 BOM 的差异，并列出两节点之间发生的硬更改。
2. **时间统一**（对比功能的前置正确性需求）：当前 BOM 变更时间存 UTC、硬更改 `occurred_at` 存浏览器本地，两者时区与格式不一致，会让时间线排序与「区间内硬更改」判定出错。统一为**存储层 canonical UTC、展示层渲染浏览器本地时间**。

## 2. 已确定的需求

| 维度 | 决定 |
|---|---|
| 展示形式 | 统一差异表，方向 左→右，绿=新增 / 黄=修改 / 红=不贴；**未变项默认折叠**，可展开 |
| 入口 | 状态图页「对比模式」按钮 + 节点卡片勾选；选满 2 个 → 底部浮条「开始对比」 |
| 可选范围 | 所有节点（已提交 / 初始状态 / 工作区草稿）都能比；硬更改卡片不参与选择；同一节点不与自己比 |
| 硬更改范围 | 两节点提交时间区间 `[min, max]` 内（按 `occurred_at`，含两端）；草稿无提交时间则用「当下」作端点 |
| 对比页路由 | `GET /board/{board_id}/compare?left=&right=`，带「⇄ 交换」（跳到 left/right 互换的 URL） |
| 时间策略 | 存储 canonical UTC；展示渲染浏览器本地时间 |
| 旧数据迁移 | `hard_changes.occurred_at` 旧的「无偏移本地」值，按 **`Asia/Singapore`（固定 +08:00，无 DST）** 一次性补成带偏移 UTC；幂等 |

## 3. 架构（遵循三层 + 纯逻辑优先）

### 新增纯逻辑模块 `app/compare.py`（★ 测试重点，零 Web/DB 依赖）

```python
def diff_boms(left: dict[str, str], right: dict[str, str]) -> list[dict]:
    """对比两个折叠后的完整 BOM，返回按位号排序的差异行。
    每行 {reference, left, right, kind}，kind ∈ {add, modify, remove, same}：
      - 仅右有             → add
      - 两边都有且值不同    → modify
      - 两边都有且值相同    → same
      - 仅左有             → remove
    """

def hard_changes_between(hcs: list[dict], lo_ts: str, hi_ts: str) -> list[dict]:
    """取 occurred_at 落在 [lo_ts, hi_ts]（含两端）的硬更改，按时间升序。
    lo/hi 由调用方先 sorted 保证 lo<=hi；UTC ISO 字符串可直接比较。"""
```

### 复用现有

- `models.get_chain(node_id)` + `bom_engine.fold_bom` → 任意节点的完整 BOM（实时折叠，无需新存储）。
- 差异表配色复用 CSS：`row-add` / `row-modify` / `row-remove`。

### 薄路由 `app/routes/board.py` 新增 handler

```
GET /board/{board_id}/compare?left=&right=
```

流程：校验两节点均存在、同属该 board、`left != right` → 折叠双方 BOM → `diff_boms` → 算两节点时间区间 → `hard_changes_between` → 渲染 `compare.html`。

### 模板

- 新建 `app/templates/compare.html`：页头（两节点 + ⇄ 交换 + 统计）、统一差异表（未变项 Alpine 折叠）、硬更改区。
- 改 `app/templates/state_graph.html`：加「对比模式」Alpine 交互。

## 4. 数据流与边界

### 节点时间戳取值（用于硬更改区间）

- 已提交节点：`committed_at`。
- 草稿（未提交）：无 `committed_at` → 用「当下」`_now()`（UTC）。
- 初始状态（根节点）：`committed_at`（建板时写入）。
- `lo, hi = sorted([ts_left, ts_right])`；取 `lo <= occurred_at <= hi`。「之间」对称，与 left/right 谁前谁后无关。

### 差异方向

表头与「左值→右值」严格按 URL 的 `left` / `right`。「⇄ 交换」仅跳到 `?left=右&right=左`，无额外状态。

### 错误与边界（消息均为中文）

- `left`/`right` 缺失、非整数、节点不存在、不属于该 board → 404。
- `left == right` → 跳回状态图并 `?flash=不能和自己比`。
- 两节点完全相同（无差异）→ 正常渲染，差异表区显示「两节点 BOM 完全一致」，未变项仍可展开。
- 区间内无硬更改 → 硬更改区显示「这段时间内没有硬更改记录」。

### 入口交互（state_graph.html，Alpine 纯客户端）

- 「对比模式」按钮 toggle `compareMode`；开启后节点卡片显示勾选框（硬更改卡片不显示）。
- `selected[]` 最多 2 个；满 2 个底部浮条亮「开始对比 →」，点击跳 `…/compare?left=&right=`（按勾选先后定 left/right）。
- 不发请求；符合「HTMX 局部刷新 + Alpine 小交互」约定。

## 5. 时间统一

### 写入层 → canonical UTC

- `_now()` 已是 UTC，保持。
- 硬更改 `occurred_at`：表单保留 `datetime-local`（用户填本地时间），新增一个隐藏字段，Alpine 提交时用 `new Date(本地值).toISOString()` 算出 UTC 写入；后端只存该 UTC 值，自身零时区逻辑。编辑回填时把存的 UTC 转回本地填入 `datetime-local`（JS）。

### 展示层 → 渲染浏览器本地

- 所有时间输出改用 `<time datetime="{{ utc }}" class="local-dt"></time>`；`base.html` 内一小段 JS 用 `toLocaleString` 把 `.local-dt` 渲染成浏览器本地时间。
- 覆盖点：`node_detail`、`state_graph`、`log`、`hard_change_detail`，及新建的 `compare`。

### 纯逻辑/排序

全部 UTC ISO 带偏移、同格式 → 字符串直接可比。现有 `merge_timeline` 与新 `hard_changes_between` 均正确，无需特殊处理。

### 迁移脚本（一次性、幂等）

- 把 `hard_changes.occurred_at` 中「无偏移」的旧值，视为 `Asia/Singapore`（+08:00，无 DST）本地时间，转成带偏移 UTC ISO。
- 幂等：已带偏移（`+`/`Z` 后缀）的值跳过；脚本可重复运行结果不变。
- 精度：原分钟精度，转换后仍分钟（带偏移），可接受。
- 形式：放在迁移/脚本位置（如 `scripts/`），单次手动执行。

## 6. 测试（TDD，纯逻辑为重点）

- **`compare.py`（★）**：`diff_boms` 覆盖 add/modify/remove/same、空表、完全一致、排序；`hard_changes_between` 覆盖区间含端点、左右顺序对称、草稿用当下、空结果。
- **时间转换（★）**：本地↔UTC 转换、迁移脚本幂等（再跑一次不变）、`Asia/Singapore` 固定偏移转换正确。
- **路由层**：compare 的 404（缺参/不存在/跨 board）、`left==right` 跳转、`?left=&right=` 正常渲染、交换 URL。
- **回归**：现有 74 passed 全绿；`merge_timeline` 在统一格式后排序仍正确。

## 7. 不做（YAGNI 边界）

- 不做多节点（>2）对比、不做并列双表视图、不做差异导出。
- 不做时区选择 UI；展示固定跟随浏览器本地，迁移固定 `Asia/Singapore`。
- 不改动 BOM 折叠存储模型。

## 8. 前端约定遵循

改 `app/templates/`、`app/static/` 前需遵循 `docs/前端风格指南.md`：只用设计令牌（CSS 变量）、新颜色同步夜间模式、先复用组件、两套主题都实际查看自检。`TemplateResponse` 用新签名、context 不放 `request` 键。
