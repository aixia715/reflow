# 工作区从 CSV 导入修改项 —— 设计

对应 issue #108：「从表头包含 Reference 和 PART 的 CSV 表格当中读取数据，并且添加到修改项中」。

## 目标

在工作区草稿页支持上传一份 CSV，把其中每行解析成一条位号修改（新增 / 修改 / 不贴），批量写进草稿的 changeset。取代逐条手工敲位号。

## 范围与前提

**入口只在工作区草稿页**（`node_detail.html` 中 `node.is_committed == 0` 的分支）。已提交节点不提供导入。

由此得到一个显式前提：草稿永远挂在链末、没有子节点，因此 `propagation.apply_node_edit` 对草稿的每次调用必然返回空冲突列表。**本流程不需要冲突弹窗**，也不复用 `_conflict_modal.html`。若将来把导入开放给已提交节点，必须重新处理传播冲突。

CSV 是**增量修改清单**，不是完整目标 BOM。CSV 里没提到的位号一律不动。

## CSV 格式

表头**大小写不敏感**地匹配三列（列名首尾空格容忍，与现有 `parse_bom_csv` 一致）：

| 列 | 必需 | 说明 |
|---|---|---|
| `Reference` | 是 | 位号。沿用逗号合并拆分：一格 `R1,R2` 视作两个位号，两行修改内容相同 |
| `Part` | 是 | 新的 Part 值 |
| `OP` | 否 | `add` / `modify` / `remove`，大小写不敏感 |

健壮性沿用现有解析：UTF-8 BOM 头、CRLF、带引号含逗号的字段。

**op 的确定：**

- 有 `OP` 列时以该列为准。列值为空的行按「无 OP」规则推断（允许一份 CSV 里混用）。
- 无 `OP` 列（或该行 OP 为空）时，按位号是否已在草稿当前折叠 BOM 中，推断为 `modify`（在）或 `add`（不在）。推断永远得不出 `remove`——批量设不贴必须显式写 `OP=remove`。

**Part 为空的语义：**

- `remove` 行：Part 可以为空，取值被忽略。
- `add` / `modify` 行（含推断出来的）：Part 为空是问题行。

推断出的 `modify` 若新值恰好等于当前值，是无害的 no-op，照常应用，不当作问题。

## 校验：全对才能应用

**CSV 内位号重复直接判为问题行**，不做「后者覆盖前者」。这样每个位号在一份 CSV 里至多出现一次，op 推断与逐条校验都可以对一份**静态的**折叠 BOM 跑，不需要像 `insert_save` 那样维护逐行模拟态。（那套复杂度只在允许重复时才必要，且会引入「先 `add R99`、后面又有一行无 OP 地引用 R99」这类隐蔽误判。）

逐条校验直接复用 `validation.validate_edit(full_bom, reference, op, part)`，因此 `add` 已存在、`modify` 不存在、`remove` 已是不贴状态等，都自动成为问题行。

只要存在任一问题行，就禁止应用整份 CSV；应用是全有或全无。

## 与草稿已有修改撞车

同位号以 CSV 为准直接覆盖（`models.set_change` 本就是 upsert），预览里不特别标记。草稿独有的、CSV 没提到的修改保留不动。

审计日志由 `apply_node_edit` 逐条记 `direct`，与手工编辑没有区别。

## 交互流程

两步，复用 `insert_save` 的「本地暂存 JSON → 提交才落库」骨架。

1. `POST /board/{board_id}/node/{node_id}/import/preview`
   上传文件 → 解析 → 对草稿折叠 BOM 逐条校验 → 渲染 `_import_preview.html`：
   - 统计条：新增 / 修改 / 不贴 各几条
   - 逐条列出将产生的修改
   - 问题行单独一块（红色），逐条给出中文原因
   - 解析结果序列化成 JSON 放进隐藏字段
   - 有任何问题行时确认按钮禁用（同 `_new_preview.html` 的做法）

2. `POST /board/{board_id}/node/{node_id}/import`
   收 JSON，**重新校验一遍**——预览与确认之间草稿可能已被改动，不能信任前端传回的结论。任一条不通过就整体拒绝、什么都不写，返回 `_form_error.html`。全部通过才逐条 `apply_node_edit`，然后 `HX-Redirect` 回草稿页并带 `?flash=✓ 已导入 N 条修改`。

节点非草稿（`is_committed == 1`）时两个路由都直接拒绝，不依赖前端不显示入口。

## 代码落点

| 位置 | 改动 |
|---|---|
| `app/csv_import.py`（★纯逻辑） | 新增 `parse_change_csv(text)`：解析三列，产出条目与解析级问题（列缺失抛 `ValueError`、位号为空、CSV 内重复、OP 值非法）。新增 `plan_changes(full_bom, entries)`：op 推断 + 逐条 `validate_edit`，返回 `(changes, problems)` |
| `app/routes/board.py` | 新增 `import/preview` 与 `import` 两个路由 |
| `app/templates/_import_preview.html` | 新片段：统计 + 修改清单 + 问题清单 + 隐藏 JSON + 确认按钮 |
| `app/templates/node_detail.html` | 草稿分支的右侧栏加 `<details class="panel">从 CSV 导入修改…</details>` |

`csv_import.py` 依赖 `validation.py` —— 两者都是零 Web/DB 依赖的纯逻辑模块，这个方向的依赖可接受。

## 测试

TDD，先写失败测试。重点在 `csv_import.py` 的两个纯逻辑函数：

- 表头大小写：`PART` / `part` / `Reference` 带首尾空格
- 缺 `Reference` 或 `Part` 列 → `ValueError`
- 有 OP 列：三种 op 各自生效；OP 值非法 → 问题行；同一份 CSV 里 OP 留空的行走推断
- 无 OP 列：位号在 BOM 中 → `modify`；不在 → `add`
- 逗号合并位号 `R1,R2` 拆成两条；尾随逗号的空段忽略
- Part 为空：`remove` 行放过；`add`/`modify` 行 → 问题行
- CSV 内位号重复 → 问题行
- 复用 `validate_edit` 的三类：`add` 已存在、`modify` 不存在、`remove` 已不贴
- 推断出的 `modify` 新值等于现值 → 正常产出一条修改，不是问题

路由层测两条：预览有问题行时不写库、确认时 CSV 全对则 N 条 changeset 落库且草稿撞车位号被覆盖。

## 不做

- 不支持「完整目标 BOM 做 diff」模式（下载改完再传回、自动推出 remove）。若以后需要，是另一个入口、另一份 spec。
- 不支持已提交节点导入。
- 不支持部分应用（跳过问题行、应用其余）。
