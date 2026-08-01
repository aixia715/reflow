# 元器件值 Lint 移出输入路径，改为面板行的异步批量检查

日期：2026-07-30
状态：设计已确认，待实现

## 问题

在「添加修改」表单里连续录入位号时体验很差。根源是 Part 输入框上挂着一次同步的
lint 往返（`_edit_form.html:16-23`）：

```
hx-post=".../lint-part"  hx-trigger="blur"
```

每输完一个值、光标一离开，就要发一次 HTTP 请求、等结果回来才能继续。录入 N 条修改
就被打断 N 次。

**还有一个真实的 bug**：提交成功后 `_edit_form.html:7` 的 after-request 回调会清空
`part` 并把焦点移到过滤框（`$refs.filterInput`）。这次移焦让 Part 框失焦，于是对
**已经清空的空值**又发一次 `/lint-part`；该响应回来时 `@part-linted.camel` 执行
`part = $event.detail.part`（空串），**把用户在此期间敲进去的下一个值抹掉**。
连续录入时这个 clobber 会随机发生，是「非常不顺畅」最直接的来源。

## 目标

1. 输入路径上不再有任何 lint 往返——敲完直接提交，零阻塞。
2. lint 结果显示在「本节点修改」面板的行上，而不是输入框旁边。
3. 面板多行的检查通过一次异步请求批量取回（「回调」），面板首次渲染不等 lint。

## 非目标

- **不动 `_lint_or_none` 的写入契约**。它是节点编辑、工作区编辑、CSV 导入、插入
  节点、建板初始 BOM 共用的归一入口（见 `board.py:88-102` 的 docstring），把归一
  挪出写入路径会连带击穿 `lint_warning_for` 的前提和其余四条写入路径。
- **不动提交 BOM 修改的路径**。提交仍是一次请求一条，串行。（调查期间实测过并发
  提交同一位号会因 `validate_edit` 读「那一刻」的折叠 BOM 而误判，30 轮里 21 轮报
  「位号不存在」——但那是写入路径的问题，与本次的 lint 无关，此处仅记录，不处理。）
- 不新建「检查项注册表」这类可扩展框架。当前只有元器件值 lint 一种检查，凭空设计
  的扩展点几乎总是与真实需求对不上。本设计留下的自然扩展点是批量端点本身。

## 关键约束：ⓘ 与 ⚠ 的生命周期不对称

这条约束决定了实现形状，必须写明。

`_lint_or_none` 在**写入前**就把 fix 级问题消化掉了：用户输入 `3.9Nf`，落库的是
`3.9nF`，原始写法没有留存。因此**对已落库的值重跑 lint 永远拿不到 fix**。已实测确认：

```
lint_part('C1', '3.9Nf') -> ('3.9nF', [LintIssue(fix, '修正: 3.9Nf → 3.9nF')])
lint_part('C1', '3.9nF') -> ('3.9nF', [])          # 重跑，fix 消失
lint_warning_for('C1', '3.9nF') -> None
```

这正是 `_changes_panel.html:14` 把 `fix=''` 硬编码的原因，也是 `lint_warning_for`
docstring 里「只会剩下 warning 级」那句话的由来。

推论：

| | 来源 | 生命周期 |
|---|---|---|
| ⓘ 自动修正 | 只能由**执行该次写入的那个请求**报告（`edit_node` 同时握有原始 `part` 和归一后的 `part_val`） | 一次性，刷新页面后消失 |
| ⚠ 非标准值 | 任何时候可由 `lint_part` 重算 | 永久，每次都能取到 |

要让 ⓘ 也持久，必须改 schema 存原始值。本次不做。

## 设计

### 后端

**删除** `POST /board/{board_id}/node/{node_id}/lint-part`（`board.py:198-213`）。
摘掉 blur 触发后它没有调用方，不留死路由。它的 5 处测试断言随之删除，见「影响面」。

**新增** `POST /board/{board_id}/node/{node_id}/lint-changes`：

- 读该节点的 changeset，对每行跑 lint，返回逐行结果。
- 复用 `csv_import.lint_entries`（CSV 导入正在用的批量 lint 原语），构造 entries 传入。
- `op == 'remove'` 的行 `part` 为 None，跳过——不贴没有值可检查。
- 因为面板行的值都已归一，返回的 notes 实际只含 warning 级。这与上面的约束一致，
  不是缺陷。
- 响应渲染 `_changes_panel.html`（这次带上 lint 结果），前端整块替换面板。

选择「返回整个面板」而不是「逐行 OOB 片段」的理由：逐行 OOB 需要给每行一个稳定的
HTML id，位号做 id 要处理转义，且异步期间用户若新增了一条修改，行与索引的对应关系
会错位。整块替换由服务端重新查库渲染，天然包含最新的行，最终一致，且与项目已有的
`_node_update.html` OOB 惯例一致。

### 前端

`_edit_form.html`：
- 删掉 Part 输入框上的 `hx-post` / `hx-trigger="blur"` / `hx-include` /
  `hx-indicator` / `@part-linted.camel`。
- 删掉 `#lint-indicator-*`、`.lint-inline`（fix 提示）、`partWarning` 的 flash 块。
- after-request 回调里不再需要重置 `partWarning` / `partFix`。

`node_detail.html`：`bomPage()` 的 `partWarning` / `partFix` 状态随之删除
（`:119`、`:139` 两处）。

`_changes_panel.html`：
- 每行的 lint 图标位置支持三态：**检查中**（淡色占位）→ ⓘ / ⚠ / 无。
- 面板容器挂 `hx-post=".../lint-changes" hx-trigger="load"`，渲染完成后自动异步取
  检查结果。首次渲染不调 `lint_warning_for`，面板渲染路径与 lint 解耦。

`_lint_icons.html`：加一个「检查中」态。现有 ⓘ/⚠ 两种图标保持不变。

`_node_context`：不再对每行调 `lint_warning_for`（挪到批量端点里）。

**ⓘ 的传递，以及它与自动刷新的冲突**：

`edit_node` 写入时若 `_lint_or_none` 产生了 fix，把它放进 `_node_update.html` 的
context，让该行在这一次响应里带上 ⓘ。

但这里有个冲突：若提交响应替换进来的面板也挂着 `hx-trigger="load"`，它会立刻再发
一次 `lint-changes`，而该端点**算不出 fix**（值已归一，见上面的约束表），返回的面板
会把刚显示出来的 ⓘ 冲掉——用户根本来不及看见。

解法：**面板模板用一个 `autolint` 开关控制是否挂 `hx-trigger="load"`**。

| 渲染场景 | `autolint` | 行为 |
|---|---|---|
| GET 节点页（首次加载） | 真 | 面板先显示「检查中」，异步取回 ⚠ |
| `lint-changes` 的响应 | 假 | 已含 ⚠，再挂 load 会无限自我触发 |
| `edit_node` / `undo` 等写入响应 | 假 | 已含完整 ⚠ + 本次一次性 ⓘ，不能被冲掉 |

写入响应里的 ⚠ 由该请求顺手算出（`lint_part` 是微秒级纯函数，放在提交响应里不影响
输入体验——被摘掉的是**输入路径**上的往返，不是提交路径）。这样 ⓘ 能稳定显示到用户
下一次操作或刷新为止，与上面的一次性语义一致。

### 样式

复用现有的 `.lint-note` / `.lint-fix` / `.lint-warn`。「检查中」态新增一个淡色样式，
按风格指南只用 CSS 变量、且同步夜间模式。

## 测试策略

纯逻辑（★）是投入重点，但本次改动的纯逻辑部分（`lint_part`、`lint_entries`）没有
变化，所以测试集中在路由层与模板渲染：

1. `lint-changes` 端点对含非标准值的 changeset 返回带 ⚠ 的面板 HTML。
2. `lint-changes` 跳过 `op=remove` 的行，不因 `part is None` 抛错。
3. `lint-changes` 对全部合规的 changeset 返回不含 ⚠ 的面板。
4. 编辑表单的 HTML 里不再出现 `lint-part` / `hx-trigger="blur"`（防回归）。
5. `edit_node` 提交 `3.9Nf` 后，响应里该行带 ⓘ 且文案含 `3.9Nf → 3.9nF`；
   再次 GET 节点页时该行没有 ⓘ（验证一次性语义）。
6. 面板首次渲染（GET 节点页）不含 ⚠、且挂了 `hx-trigger="load"`——⚠ 由异步请求
   补上（验证解耦）。
7. `lint-changes` 的响应**不含** `hx-trigger="load"`（防自我无限触发）。
8. `edit_node` 的响应**不含** `hx-trigger="load"`，且同时含 ⚠ 与本次 ⓘ
   （验证一次性 ⓘ 不会被自动刷新冲掉）。

TDD：每条先写失败测试再实现。

## 影响面

删除或改写的既有测试（断言 `/lint-part` 路由与 `partLinted` 事件）：

- `tests/test_routes.py:220-249`（3 处）
- `tests/test_import_panel_ui.py:126-143`（2 处）

这些测试覆盖的是「路由能正确报告 fix/warning 文案」，该能力转移到 `lint-changes`
后由新测试覆盖，不是净损失。

基线：改动前后跑全量 pytest 对比（基线约 491 通过；`rename_ui` 的端口竞态 flake
单独重跑即可，不算回归）。worktree 内无 .venv，用主 checkout 的
`/home/tong/code/reflow/.venv/bin/python -m pytest`。

## 关于「并发」

用户明确要求通过回调并发进行多个检查。本设计照此实现：面板渲染不再等 lint，检查
结果由独立的异步请求带回。

补充一个供后续判断的事实：`lint_part` 是纯 CPU、微秒级、不碰数据库的纯函数，因此
在当前这一种检查下，异步批量的收益是**架构解耦**（面板渲染不依赖 lint、后续接入
慢检查如器件库查询无需改结构），而不是速度提升。同理，lint 是只读的，不存在写入
路径那种顺序依赖，多行并发对它是安全的。
