# 移动端适配 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Reflow 在手机上「查看 + 轻量编辑」可用：header 不挤爆、节点详情页侧栏堆叠、触屏能看到并点到悬停按钮、宽表格横向滚动。

**Architecture:** 纯 CSS 为主——在 `app/static/style.css` 末尾新增「移动端适配」分区，含两组独立规则：`@media (max-width:720px)` 管窄屏布局、`@media (hover:none)` 管触屏；全部是追加覆盖，不改桌面样式。另有两处小模板改动：四个 `table.bom` 外套新组件 `.table-scroll`，`node_detail.html` 回填表单后 `scrollIntoView`。

**Tech Stack:** 原生 CSS（媒体查询）、Jinja2 模板、Alpine.js（已有，无新依赖）。

**设计文档:** `docs/superpowers/specs/2026-07-16-mobile-adaptation-design.md`

## Global Constraints

- 颜色只用 `:root` CSS 变量，禁止裸色值（本计划不引入任何新颜色）。
- 不写内联 style、不引入 CSS 框架、不加构建步骤。
- 所有注释、commit 信息、文档用中文。
- 改动完成后 `pytest` 必须全绿（当前 491 passed + 1 skipped；纯前端改动不应影响任何测试）。
- 每个 commit 末尾加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 运行环境：先 `. .venv/bin/activate`。

---

### Task 1: style.css 移动端适配分区

**Files:**
- Modify: `app/static/style.css`（文件末尾追加，现文件共 289 行）

**Interfaces:**
- Produces: 组件类 `.table-scroll`（Task 2 的模板要用）；媒体查询规则（无 JS 接口）。

说明：CSS 无法用 pytest 做 TDD，本任务以「pytest 无回归 + Task 5 浏览器目检」为验证手段。

- [ ] **Step 1: 在 `app/static/style.css` 文件末尾（`.attach-form .input{flex:1;min-width:0}` 之后）追加以下整块**

```css

/* ===== 移动端适配 =====
   设计见 docs/superpowers/specs/2026-07-16-mobile-adaptation-design.md。
   两组规则相互独立：hover:none 管触屏（与屏宽无关），max-width:720px 管窄屏布局。
   只追加覆盖，不改上方桌面样式。 */

/* 表格横向滚动兜底容器：所有 table.bom 外层都套它（宽度不够时容器内滚动，页面不横滚） */
.table-scroll{width:100%;overflow-x:auto}

/* 触屏设备：悬停显现的按钮常显、图标按钮点按区加大 */
@media (hover: none){
  table.bom tr .hover-only{visibility:visible}
  .menu-btn{opacity:.45}
  .icon-btn{padding:6px}
}

/* 窄屏布局（单断点） */
@media (max-width: 720px){
  /* header：品牌+主题切换占首行，面包屑/上下文链接/哈希框依次换行 */
  .topnav{flex-wrap:wrap;padding:8px 12px;gap:6px 12px}
  .topnav .theme-toggle{order:1;margin-left:auto}
  .topnav .crumbs{order:2;flex-basis:100%;font-size:12px}
  .topnav .ctx{order:3;flex-basis:100%;margin-left:0;flex-wrap:wrap;row-gap:6px}
  .topnav .hash-jump{order:4;flex-basis:100%}
  .hash-jump .input{width:100%}

  main{padding:16px 12px}

  /* 主内容 + 侧栏改纵向堆叠 */
  .two-col{flex-direction:column}
  .two-col > aside{width:100%}

  /* 工具栏：筛选框自适应收缩，装不下则换行 */
  .toolbar{flex-wrap:wrap}
  .toolbar .input{flex:1 1 160px;width:auto;min-width:0}
  .toolbar .input-clearable{flex:1 1 170px}
  .input-clearable .input{width:100%}

  table.bom th,table.bom td{padding-left:8px;padding-right:8px}

  .cmp-picker .input{max-width:100%}
  .compare-bar{flex-wrap:wrap}

  .modal-overlay{padding:16px 12px 0}
  #toast-zone{left:12px;right:12px}
  .toast{width:fit-content;margin-left:auto}
}
```

- [ ] **Step 2: 运行测试确认无回归**

Run: `. .venv/bin/activate && pytest -q`
Expected: 491 passed + 1 skipped（数字与改动前一致，0 failed）

- [ ] **Step 3: Commit**

```bash
git add app/static/style.css
git commit -m "移动端适配：style.css 增加 720px 断点与触屏规则分区

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 四处 table.bom 外套 .table-scroll 容器

**Files:**
- Modify: `app/templates/_bom_table.html:14-72`
- Modify: `app/templates/log.html:23`
- Modify: `app/templates/insert_node.html:73`
- Modify: `app/templates/compare.html:43`

**Interfaces:**
- Consumes: Task 1 的 `.table-scroll` 类。
- Produces: 无（纯模板包裹）。

- [ ] **Step 1: `_bom_table.html` — 把 `<table class="bom">…</table>`（第 14 行起到文件末尾的 `</table>`）整体包进 wrapper**

第 14 行改为：

```html
<div class="table-scroll">
<table class="bom">
```

文件末尾的 `</table>` 后补一行 `</div>`：

```html
</table>
</div>
```

- [ ] **Step 2: `log.html` — 同样包裹（第 23 行 `<table class="bom">` 前加 `<div class="table-scroll">`，对应的 `</table>` 后加 `</div>`）**

- [ ] **Step 3: `insert_node.html` — 同样包裹第 73 行的 `<table class="bom">` 与其 `</table>`（注意保持原有缩进层级，wrapper 与 `<div class="toolbar">` 同级）**

- [ ] **Step 4: `compare.html` — 包裹第 43 行表格，并顺手删掉违规内联 style**

原：

```html
  <table class="bom" style="width:100%">
```

改为：

```html
  <div class="table-scroll">
  <table class="bom">
```

对应第 69 行 `</table>` 后补 `</div>`。（该表格是 `.page-head` flex 容器的子项，原来靠内联 `width:100%` 占满整行；`.table-scroll` 自带 `width:100%`，行为不变且消除了内联 style。）

- [ ] **Step 5: 运行测试确认无回归**

Run: `. .venv/bin/activate && pytest -q`
Expected: 491 passed + 1 skipped（有测试断言 HTML 片段的话若失败，看失败信息调整——预期不会，包裹不改变表格内容）

- [ ] **Step 6: Commit**

```bash
git add app/templates/_bom_table.html app/templates/log.html app/templates/insert_node.html app/templates/compare.html
git commit -m "移动端适配：table.bom 统一外套 .table-scroll 横向滚动容器

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 节点详情页回填表单后滚动到编辑面板

**Files:**
- Modify: `app/templates/node_detail.html:55`（草稿分支的编辑面板加 x-ref）
- Modify: `app/templates/node_detail.html:125-126`（`setFrom()`）

**Interfaces:**
- Consumes: 页面已有 Alpine 组件 `bomPage()`、`x-ref="editDetails"`（已提交节点分支）。
- Produces: 无。

背景：窄屏下侧栏堆在 BOM 表之后，点行内「修改」回填表单后表单在视口外，必须滚过去；桌面上面板本就在视口内，`block:'nearest'` 不产生位移，行为无感。

- [ ] **Step 1: 给草稿分支的编辑面板加 ref**

`node_detail.html` 第 55 行：

```html
    <div class="panel">
```

改为：

```html
    <div class="panel" x-ref="editPanel">
```

（已提交分支第 50 行已有 `x-ref="editDetails"`，不动。）

- [ ] **Step 2: `setFrom()` 末尾滚动到面板**

原（第 125-126 行）：

```js
    setFrom(d){ this.ref = d.ref; this.op = d.op; this.part = d.part || '';
      const det = this.$refs.editDetails; if (det && !det.open) det.open = true; },
```

改为：

```js
    setFrom(d){ this.ref = d.ref; this.op = d.op; this.part = d.part || '';
      const det = this.$refs.editDetails; if (det && !det.open) det.open = true;
      const panel = det || this.$refs.editPanel;
      if (panel) panel.scrollIntoView({behavior: 'smooth', block: 'nearest'}); },
```

- [ ] **Step 3: 运行测试确认无回归**

Run: `. .venv/bin/activate && pytest -q`
Expected: 491 passed + 1 skipped

- [ ] **Step 4: Commit**

```bash
git add app/templates/node_detail.html
git commit -m "移动端适配：回填编辑表单后滚动到编辑面板

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 更新前端风格指南

**Files:**
- Modify: `docs/前端风格指南.md`（第 10 行、组件清单表、自检清单）

**Interfaces:** 无（纯文档）。

- [ ] **Step 1: 改「设计原则」第三条（第 10 行）**

原：

```markdown
- 桌面单栏布局，`main` 最大宽度 1100px，居中。不做响应式断点（单人桌面工具）。
```

改为：

```markdown
- 桌面单栏布局，`main` 最大宽度 1100px，居中。移动端适配集中在 `style.css` 末尾「移动端适配」分区：单断点 `@media (max-width:720px)` 管窄屏布局，`@media (hover:none)` 管触屏（悬停按钮常显、点按区加大）；只写追加覆盖，不改桌面样式，不做多级断点。
```

- [ ] **Step 2: 组件清单表加 `.table-scroll` 行（加在 `table.bom` 那行之后）**

```markdown
| `.table-scroll` | 表格横向滚动容器；所有 `table.bom` 外层必须套它，防窄屏页面横滚 |
```

- [ ] **Step 3: 自检清单加一条（加在「白天和夜间两套主题」那条之后）**

```markdown
- [ ] **≤720px 窄屏（DevTools 手机模拟，如 375px）实际查看过改动页面**，header/表格/表单无溢出、悬停按钮触屏可见
```

- [ ] **Step 4: Commit**

```bash
git add docs/前端风格指南.md
git commit -m "docs：风格指南补移动端适配约定与自检项

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 浏览器目检与修补

**Files:**
- 可能小幅修补 `app/static/style.css`（仅限「移动端适配」分区内）

**Interfaces:**
- Consumes: Task 1–3 的全部改动。

- [ ] **Step 1: 启动应用**

Run: `. .venv/bin/activate && REFLOW_DB=reflow.sqlite uvicorn app.main:app --port 8000`
（若 `reflow.sqlite` 无数据，先在桌面宽度下新建一块单板并添加几条修改、提交一个节点，保证有内容可看。）

- [ ] **Step 2: 浏览器 375px 宽度逐页目检（DevTools 手机模拟）**

检查页面清单，白天 + 夜间两套主题都要看：

1. 首页 `/`：层级列表、chip 不溢出
2. 状态图 `/board/{id}`：时间线卡片、三点菜单（触屏模拟下应常显半透明）、对比条
3. 节点详情 `/board/{id}/node/{nodeId}`：BOM 表在容器内横滚、侧栏堆叠在下、点行内「修改」页面滚到编辑表单、「修改/撤销」图标常显
4. 审计日志 `/board/{id}/log`：6 列表格容器内横滚、筛选工具栏换行
5. 对比页 `/board/{id}/compare?...`：选择器换行、表格横滚
6. header：任意页面首行是「⟲ Reflow … 🌙」，面包屑/链接/哈希框依次换行，无横向溢出

验收标准：任何页面 `document.documentElement.scrollWidth <= window.innerWidth`（页面本身不横滚）；两套主题无看不清的元素。

- [ ] **Step 3: 发现问题就地修补（仅动「移动端适配」分区），重复 Step 2 直到全过**

- [ ] **Step 4: 最终回归**

Run: `pytest -q`
Expected: 491 passed + 1 skipped

- [ ] **Step 5: 有修补则提交**

```bash
git add app/static/style.css
git commit -m "移动端适配：目检修补

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
