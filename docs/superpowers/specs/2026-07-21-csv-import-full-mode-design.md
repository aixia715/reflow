# CSV 导入支持「全量 / 差异」两种模式 — 设计

> Issue 129：从 csv 导入更改的时候，可以选择是全量还是差异。

## 背景与现状

「从 CSV 导入修改」目前只支持**差异**语义：上传的 CSV 每行是一条改动，`Reference` / `Part` 必填，`OP` 列（`add`/`modify`/`remove`）可选；`OP` 留空时按位号是否已在折叠 BOM 中自动推断为 `modify` 或 `add`——**永远推不出 `remove`**，批量设不贴必须显式写 `OP=remove`。

- 纯逻辑：`csv_import.parse_change_csv` + `plan_changes`
- 路由：`board.py` 的 `import_preview`（预览、不写库）与 `import_apply`（应用、全有或全无）
- 入口：仅工作区草稿（`is_committed=0`）的导入面板
- 模板下载：`change_csv_template()` = `Reference,Part,OP`

痛点：想把某块板的 BOM 一次对齐到一份完整清单时，用户得手工算差、尤其是手写每一条 `OP=remove`，既繁琐又易漏。

## 目标

导入面板新增「全量 / 差异」二选一（默认**差异**，保持现状）。全量模式把 CSV 当作**完整目标 BOM**，由系统和当前折叠 BOM 自动求差，最大价值是**自动算出 `remove`**。

## 语义定义

### 差异模式（现有，不变）
CSV 只列改动，未列出的位号保持不变；`OP` 列可选/可推断。

### 全量模式（新增）
CSV 是这块板**完整的目标 BOM**。设 `current` = 当前节点折叠后的 BOM，`target` = CSV 解析出的位号→Part 映射，求差规则：

| 情况 | 结果 op |
|---|---|
| ref ∈ target，∉ current | `add` |
| ref ∈ 两者，Part 不同 | `modify` |
| ref ∈ 两者，Part 相同 | 跳过（无变化） |
| ref ∈ current，∉ target | `remove`（不贴） |

约束：

- **不认 OP 列**：全量 CSV 中出现 OP 列直接**报错**（"全量模式不应包含 OP 列，请删除后重试"），避免用户误以为 OP 生效。
- **空 Part 报错**：全量某行 Part 为空 → 报错（复用 `parse_bom_csv` 现有的 `empty_part` 校验）。
- **空全量正常允许**：零行的全量 CSV 求差得到「全部位号 remove（不贴）」，在预览里如实展示，用户确认后应用。不特判、不拦截。

## 架构与改动

三层照旧：纯逻辑 → 路由 → 模板。刻意做两处「省改动」取舍：**`import_apply` 完全不动**、**全量解析复用 `parse_bom_csv`**。

### 1. 纯逻辑层 `csv_import.py`（测试重点）

**新增** `plan_full_changes(current_bom, target_bom) -> tuple[list[PlannedChange], list[CsvProblem]]`：

- 按上表求差，生成 `PlannedChange`；`remove` 时 `part=None`。
- 每条经 `validate_edit` 兜底校验，失败进 `problems`（`kind="invalid"`）。
- 输出按位号排序，保证确定性（预览稳定、便于测试）。
- 不修改入参。

**扩展** `parse_bom_csv(text, forbid_op=False)`：

- 新增可选参数 `forbid_op`，默认 `False`——`parse_change_csv`、初始 BOM 导入路径不受影响。
- 为 `True` 且表头存在 OP 列（大小写不敏感、首尾空格容错，与 `parse_change_csv` 的 OP 识别一致）时，抛 `ValueError("全量模式的 CSV 不应包含 OP 列，请删除后重试")`。
- 其余行为不变：只取 Reference/Part 两列，拆合并位号，产出 `duplicate` / `empty_part` / `empty_reference` 问题清单。空 Part 的条目仍进 `entries`（part=""），但已被标为 `empty_part` 问题行 → 上层因有 problems 不会 ready，满足「空 Part 报错」。

### 2. 路由层 `board.py`

`import_preview` 增读表单字段 `mode`（`Form("diff")`，默认 `"diff"`）：

- `mode == "full"`：
  1. `entries, problems = parse_bom_csv(text, forbid_op=True)`，`ValueError` 走现有的错误消息分支（`ctx["message"]`）。
  2. `target = {e.reference: e.part for e in entries}`。
  3. `current = fold_bom(initial, chain)`。
  4. `changes, invalid = plan_full_changes(current, target)`；`problems += invalid`。
  5. 额外统计 `unchanged`（`current` 与 `target` 中 Part 相同的位号数），传入 ctx 供预览展示。
- `mode`（缺省或 `"diff"`）：走现有 `parse_change_csv` + `plan_changes` 路径，`unchanged` 传 0/不显示。
- 两条路汇合到同一套 `ctx`（`changes` / `problems` / `ready` / `counts` / `changes_json`），渲染 `_import_preview.html`。ctx 追加 `mode`、`unchanged`。

`import_apply` **不改**：预览产出的 planned change 已带**显式 op**，`hx-vals` 回传 `changes_json`，应用时 `plan_changes(fold_bom(...), entries)` 对显式 op 照常重校验（全有或全无），与模式无关。

`import_csv_template` 模板路由增读 `mode` 查询参数：`mode == "full"` 返回 `Reference,Part\n`，否则返回现有 `Reference,Part,OP\n`。对应在 `csv_import.py` 增一个全量模板常量/函数（与 `change_csv_template` 并列）。

### 3. 前端 `node_detail.html` + `_import_preview.html`

- 导入面板 `<details>` 加 `x-data="{ mode: 'diff' }"` 的小 Alpine 作用域。
- 新增单选「差异（默认）/ 全量」：`name="mode"` 供表单提交、`x-model="mode"` 供 href 联动；随表单 `hx-trigger="change"` 自动重新预览。
- 「下载模板」链接 `:href` 按 `mode` 拼 `?mode=`（差异 = `Reference,Part,OP`，全量 = `Reference,Part`），避免用户把带 OP 的差异模板误用到全量模式而触发报错。
- 帮助文案分别说明两种模式：差异「只列改动，未列出的位号不变」；全量「CSV 是完整目标 BOM，系统自动求差、自动算不贴，不需要 OP 列」。
- `_import_preview.html`：全量模式（`mode == "full"`）在信息行额外显示「其余 N 个位号无变化」，让用户确信没漏；差异模式不显示。

遵循前端风格指南：只用现有设计令牌、组件先复用；两套主题都要实际查看；Alpine htmx 事件监听加 `.camel`；传 JS/hx-vals 的值 `|tojson` + 单引号属性。

## 测试计划（TDD，先失败测试再实现）

按纯逻辑 → 路由 → 前端顺序，沿用现有 `tests/` 分文件风格：

1. **纯逻辑**（`plan_full_changes`）：
   - add / modify / remove / skip（Part 相同）各组合。
   - 空 target（全 remove）、空 current（全 add）。
   - 输出顺序确定（按位号排序）。
   - `validate_edit` 兜底：构造非法位号进 `problems`。
2. **纯逻辑**（`parse_bom_csv(forbid_op=True)`）：含 OP 列（含大小写/空格变体）抛 `ValueError`；不含 OP 列正常解析；`forbid_op=False`（默认）时带 OP 列仍不报错（回归保护）。
3. **路由**：
   - 全量预览计数正确（add/modify/remove 计数、unchanged 计数）。
   - 全量 CSV 带 OP 列 → 错误消息。
   - 空全量 CSV → 预览全为 remove、ready 可应用。
   - apply 落库正确（全量产出的 remove/modify/add 均生效）。
   - 差异模式回归不变。
4. **前端 UI**：模式单选存在、默认差异；模板下载链接按模式带 `?mode=`；全量预览显示「无变化」计数（沿用现有 UI 测试风格）。

## 影响面与非目标

- 不改数据模型、不改 `import_apply`、不改差异模式既有行为。
- 非目标：全量模式「用当前 BOM 预填模板导出再编辑」的高级体验（YAGNI，本期只提供空表头模板）；差异以外的修改类型（飞线等，MVP 边界外）。
