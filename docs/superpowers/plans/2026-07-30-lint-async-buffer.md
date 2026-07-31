# 元器件值 Lint 异步化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把元器件值 lint 从「Part 输入框失焦」这条输入路径上摘掉，改为「本节点修改」面板行上的异步批量检查。

**Architecture:** 删除 `/lint-part` 路由与输入框上的 blur 触发；新增 `/lint-changes` 批量端点，面板首次渲染只显示「检查中」占位、由 `hx-trigger="load"` 异步补上 ⚠；ⓘ（自动修正）因原值在归一后不留存，只能由执行写入的那次请求一次性报告。

**Tech Stack:** FastAPI + Jinja2 + HTMX + Alpine.js（CDN，无构建）、SQLite、pytest

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-07-30-lint-async-buffer-design.md`
- **改前端前必读 `docs/前端风格指南.md`**：只用 `:root` 的 CSS 变量，禁止裸色值；新增颜色变量必须同步 `[data-theme="dark"]`；圆角用 `var(--radius)`。
- **Starlette 1.2.1 签名**：`templates.TemplateResponse(request, "name.html", {context})`——`request` 是第一个位置参数，context 里**不要**放 `"request"` 键。
- 代码注释、docstring、UI 文案、错误消息一律中文。
- **不动 `_lint_or_none` 的对外契约**：它是节点编辑、工作区编辑、CSV 导入、插入节点、建板初始 BOM 五条写入路径共用的归一入口。
- **不动提交 BOM 修改的路径**：提交仍是一次请求一条、串行。
- TDD：每步先写失败测试，跑，再实现，再跑，再提交。
- worktree 内无 `.venv`，一律用主 checkout 的解释器：
  `/home/tong/code/reflow/.venv/bin/python -m pytest`
- 全量基线：动手前 651 通过（CLAUDE.md 里写的 222、旧记忆里的 491 都已过时，以实测为准）；
  `rename_ui` 的端口竞态 flake 单独重跑即可，不算回归。

---

## File Structure

| 文件 | 改动 | 职责 |
|---|---|---|
| `app/routes/board.py` | 修改 | 删 `lint_part_route`；加 `_lint_with_note`、`lint_changes` 端点；`_node_context` 加 `with_lint` 参数 |
| `app/templates/_changes_panel_block.html` | **新建** | 带 `#changes-panel` 外层 div 的面板块，供 `lint-changes` 用 outerHTML 替换 |
| `app/templates/_changes_panel.html` | 修改 | 行图标三态：检查中 → ⓘ/⚠/无 |
| `app/templates/node_detail.html` | 修改 | `#changes-panel` 挂异步触发；删 `partWarning`/`partFix` 状态 |
| `app/templates/_edit_form.html` | 修改 | 摘掉 blur lint 的全部属性与提示块 |
| `app/static/style.css` | 修改 | `.lint-checking` 占位样式 |
| `tests/test_lint_async.py` | **新建** | 本次全部新测试 |
| `tests/test_routes.py` | 修改 | 删 3 个 `/lint-part` 测试 |
| `tests/test_import_panel_ui.py` | 修改 | 删 3 个 `/lint-part` 与 lint-indicator 测试 |

---

### Task 1: 后端批量 lint 端点

**Files:**
- Modify: `app/routes/board.py`（`_node_context` 约 45 行起、`_lint_or_none` 约 88-102 行）
- Create: `app/templates/_changes_panel_block.html`
- Test: `tests/test_lint_async.py`

**Interfaces:**
- Consumes: `app.component_lint.lint_part`、`lint_warning_for`（已存在）；`models.get_changeset`
- Produces:
  - `_lint_with_note(reference: str, op: str, part: str | None) -> tuple[str | None, str]`，返回 `(归一后的值, fix文案)`；`op=="remove"` 或 `part is None` 时返回 `(None, "")`
  - `_node_context(conn, board_id: int, node, with_lint: bool = True) -> dict`，context 新增键 `lint_ready: bool`、`fixed_ref: str | None`、`fixed_note: str`
  - 路由 `POST /board/{board_id}/node/{node_id}/lint-changes`，返回渲染后的 `_changes_panel_block.html`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_lint_async.py`：

```python
"""元器件值 lint 异步化：批量端点、面板三态、一次性 ⓘ。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def _workspace_id(client, board_id):
    from app import models
    from app.main import get_conn
    return models.workspace_node(get_conn(), int(board_id))["id"]


def _add(client, board_id, ws, reference, part, op="add"):
    return client.post(f"/board/{board_id}/node/{ws}/edit",
                       data={"reference": reference, "op": op, "part": part})


def test_lint_changes_reports_non_standard_value(client):
    """批量端点对面板里的非标准值报 ⚠。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert r.status_code == 200
    assert "不是标准" in r.text


def test_lint_changes_is_clean_for_standard_value(client):
    """标准值不产生 ⚠。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "10k")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert "不是标准" not in r.text


def test_lint_changes_skips_removed_rows(client):
    """op=remove 的行 part 为 None，批量 lint 必须跳过而不是抛错。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "remove", "part": ""})
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert r.status_code == 200
    assert "R1" in r.text


def test_lint_changes_response_does_not_retrigger_itself(client):
    """批量端点的响应不能再挂 load 触发器，否则自我无限触发。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert 'hx-trigger="load"' not in r.text


def test_lint_changes_rejects_foreign_node(client):
    """节点不属于该单板时 404，与其余节点路由一致。"""
    board_id = _setup_board(client)
    r = client.post(f"/board/{board_id}/node/99999/lint-changes")
    assert r.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py -v
```

Expected: 全部 FAIL，`lint-changes` 返回 404/405（路由不存在）。

- [ ] **Step 3: 新建面板块模板**

`app/templates/_changes_panel_block.html`：

```html
{# 带外层容器的修改面板，供 lint-changes 用 outerHTML 整块替换。
   这里**不挂** hx-trigger="load"——挂了会自我无限触发；异步检查只由
   node_detail.html 的首次渲染发起一次。 #}
<div id="changes-panel" class="panel">{% include "_changes_panel.html" %}</div>
```

- [ ] **Step 4: 改 `_node_context` 支持跳过 lint**

在 `app/routes/board.py` 的 `_node_context` 里，把签名与 `changes` 那一段改成：

```python
def _node_context(conn, board_id: int, node, with_lint: bool = True) -> dict:
    """节点页/片段的完整渲染上下文：行数据（含旧值）、不贴行、修改面板、统计。

    with_lint=False 时跳过逐行 lint，面板渲染路径与检查解耦——节点页首次加载走
    这条，行先显示「检查中」，再由 /lint-changes 异步补上结果。
    """
```

`changes` 那一项改为：

```python
        "changes": [{**dict(c),
                     "lint_warning": lint_warning_for(c["reference"], c["part"]) if with_lint else None}
                    for c in changes.values()],
        "lint_ready": with_lint,
        "fixed_ref": None,
        "fixed_note": "",
```

- [ ] **Step 5: 加 `_lint_with_note`，让 `_lint_or_none` 委托它**

在 `app/routes/board.py` 中 `_lint_or_none` 之前插入：

```python
def _lint_with_note(reference: str, op: str, part: str | None) -> tuple[str | None, str]:
    """落库前归一，并回报本次的 fix 文案。返回 (归一后的值, fix文案)。

    fix 只有在这一刻拿得到：归一后原始写法就不留存了（存的是 3.9nF，不是用户
    敲的 3.9Nf），对已落库的值重跑 lint 永远只剩 warning 级——见
    `lint_warning_for` 的 docstring。所以面板上的 ⓘ 是一次性的，只能由执行
    这次写入的请求顺手带出来，刷新页面后就没了。
    """
    if op == "remove" or part is None:
        return None, ""
    fixed, issues = lint_part(reference, part)
    return fixed, next((i.message for i in issues if i.level == "fix"), "")
```

把 `_lint_or_none` 的函数体（保留原 docstring 一字不改）换成：

```python
    return _lint_with_note(reference, op, part)[0]
```

- [ ] **Step 6: 加 `lint-changes` 路由**

在 `app/routes/board.py` 中 `edit_node` 之前插入：

```python
@router.post("/board/{board_id}/node/{node_id}/lint-changes")
def lint_changes(request: Request, board_id: int, node_id: int):
    """批量取「本节点修改」面板各行的元器件值检查结果。

    面板首次渲染不算 lint（渲染路径与检查解耦），由本端点异步补上，一次请求
    带回全部行的结果。只会返回 warning 级：面板行的值都已在写入时归一，fix 级
    问题不复存在（见 `_lint_with_note`）。
    """
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return templates.TemplateResponse(
        request, "_changes_panel_block.html", _node_context(conn, board_id, node))
```

- [ ] **Step 7: 跑测试确认通过**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py -v
```

Expected: 5 passed。

- [ ] **Step 8: 提交**

```bash
git add app/routes/board.py app/templates/_changes_panel_block.html tests/test_lint_async.py
git commit -m "feat：新增 lint-changes 批量端点，面板检查可脱离渲染路径

_node_context 加 with_lint 开关；_lint_with_note 在归一的同时回报 fix
文案——fix 只有写入那一刻拿得到，归一后原值不留存。"
```

---

### Task 2: 面板行三态 + 节点页异步触发

**Files:**
- Modify: `app/routes/board.py:134-141`（`node_detail` 路由传 `with_lint=False`）
- Modify: `app/templates/_changes_panel.html:14`
- Modify: `app/templates/node_detail.html:59`
- Modify: `app/static/style.css`（`.lint-note` 一组之后，约 136-148 行区域）
- Test: `tests/test_lint_async.py`

**Interfaces:**
- Consumes: Task 1 的 `lint_ready` / `fixed_ref` / `fixed_note` context 键、`/lint-changes` 路由
- Produces: 面板行在 `lint_ready` 为假时渲染 `<span class="lint-checking">`；节点页 `#changes-panel` 带 `hx-post`/`hx-trigger="load"`/`hx-swap="outerHTML"`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_lint_async.py`：

```python
def test_node_page_defers_lint_to_async_request(client):
    """节点页首次渲染不算 lint，只挂异步触发器 + 占位。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "lint-changes" in r.text
    assert 'hx-trigger="load"' in r.text
    assert "lint-checking" in r.text
    assert "不是标准" not in r.text


def test_panel_shows_warning_once_lint_arrives(client):
    """异步结果回来后，占位换成 ⚠，且不再有占位。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert "不是标准" in r.text
    assert "lint-checking" not in r.text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py -k "defers or arrives" -v
```

Expected: FAIL —— `lint-changes` / `lint-checking` 不在节点页 HTML 里。

- [ ] **Step 3: 面板行改三态**

`app/templates/_changes_panel.html` 第 14 行那一句：

```html
    {%- with fix='', warning=c.lint_warning or '' %}{% include "_lint_icons.html" %}{% endwith %}
```

替换为：

```html
    {%- if lint_ready %}
      {%- with fix=(fixed_note if fixed_ref == c.reference else ''), warning=c.lint_warning or '' %}{% include "_lint_icons.html" %}{% endwith %}
    {%- else %}
      <span class="lint-note lint-checking" title="正在检查元器件值…" aria-label="正在检查元器件值…"></span>
    {%- endif %}
```

- [ ] **Step 4a: 节点页路由跳过 lint**

`app/routes/board.py` 的 `node_detail` 路由（约 134-141 行），把：

```python
    return templates.TemplateResponse(
        request, "node_detail.html", _node_context(conn, board_id, node))
```

改为：

```python
    return templates.TemplateResponse(
        request, "node_detail.html",
        _node_context(conn, board_id, node, with_lint=False))
```

这是唯一传 `with_lint=False` 的调用点：节点页要尽快出来，检查由随后的异步请求补上。
其余调用点（`edit` / `undo` / `undo-all` / 冲突弹窗 / `lint-changes`）都保持默认
`True`——它们的响应不带 load 触发器，自己不算 lint 就没人补了。

- [ ] **Step 4: 节点页挂异步触发**

`app/templates/node_detail.html` 第 59 行：

```html
    <div id="changes-panel" class="panel">{% include "_changes_panel.html" %}</div>
```

替换为：

```html
    {# 面板先渲染出来（行显示「检查中」），元器件值检查由这一次异步请求批量补上。
       响应是 _changes_panel_block.html，它不带 load 触发器，所以只跑一次。 #}
    <div id="changes-panel" class="panel"
         hx-post="/board/{{ board_id }}/node/{{ node.id }}/lint-changes"
         hx-trigger="load" hx-swap="outerHTML">{% include "_changes_panel.html" %}</div>
```

- [ ] **Step 5: 加占位样式**

`app/static/style.css` 中 `.lint-fix{color:var(--blue)}` 那一行之后插入：

```css
.lint-checking{width:13px;height:13px;border-radius:50%;background:var(--border);
  animation:lint-pulse 1.2s ease-in-out infinite}
@keyframes lint-pulse{0%,100%{opacity:.35}50%{opacity:.9}}
@media (prefers-reduced-motion:reduce){.lint-checking{animation:none}}
```

只用了现有变量 `--border`，不新增颜色变量，因此无需同步 `[data-theme="dark"]`。

- [ ] **Step 6: 跑测试确认通过**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py -v
```

Expected: 7 passed。

- [ ] **Step 7: 提交**

```bash
git add app/templates/_changes_panel.html app/templates/node_detail.html app/static/style.css tests/test_lint_async.py
git commit -m "feat：修改面板行三态，检查结果由异步请求批量补上

节点页首次渲染只出「检查中」占位，load 触发一次 lint-changes 换回结果。"
```

---

### Task 3: 摘掉输入框上的 blur lint

**Files:**
- Modify: `app/templates/_edit_form.html:7,16-29`
- Modify: `app/templates/node_detail.html:119,139`
- Modify: `app/routes/board.py`（删 `lint_part_route`，约 198-213 行）
- Modify: `tests/test_routes.py`（删 3 个测试，约 214-249 行）
- Modify: `tests/test_import_panel_ui.py`（删 3 个测试，约 118-144 行）
- Test: `tests/test_lint_async.py`

**Interfaces:**
- Consumes: Task 2 的面板三态（fix 的展示位置已迁移到面板行）
- Produces: 编辑表单 HTML 里不再出现 `lint-part`、`hx-trigger="blur"`、`lint-indicator`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_lint_async.py`：

```python
def test_edit_form_has_no_blur_lint(client):
    """输入路径上不能再有任何 lint 往返——这是本次改动的核心诉求。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "lint-part" not in r.text
    assert 'hx-trigger="blur"' not in r.text
    assert "lint-indicator" not in r.text


def test_lint_part_route_is_gone(client):
    """旧的单值 lint 路由已删除，不留死路由。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/lint-part",
                    data={"reference": "R1", "part": "1000pF"})
    assert r.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py -k "blur or gone" -v
```

Expected: FAIL —— 表单里仍有 `lint-part`，旧路由仍返回 204。

- [ ] **Step 3: 清理编辑表单**

`app/templates/_edit_form.html` 第 7 行的 after-request 回调，删掉 `partWarning=''; partFix='';` 两项赋值：

```html
      @htmx:after-request.camel="if ($event.target === $el && $event.detail.successful && !$event.detail.xhr.getResponseHeader('HX-Retarget')) { ref=''; part=''; op='modify'; $nextTick(() => $refs.filterInput && $refs.filterInput.select()); }">
```

第 16-29 行（Part 输入框及其后的三个提示块）整体替换为：

```html
  <input class="input" name="part" x-model="part" x-ref="partInput"
         placeholder="新 Part 值" :disabled="op==='remove'"
         @keydown.enter.prevent="$el.closest('form').requestSubmit()">
```

即删除：`hx-post` / `hx-trigger="blur"` / `hx-include` / `hx-indicator` / `@part-linted.camel` / `@input` 六个属性，以及 `.lint-status`、`.lint-inline`、`.flash-warn` 三个块。

> 这同时修掉一个 bug：提交后焦点移到过滤框会让 Part 框失焦，从而对**已清空的空值**再发一次 lint，其响应回来时把用户新敲进去的值抹掉。

- [ ] **Step 4: 清理 Alpine 状态**

`app/templates/node_detail.html` 第 119 行：

```javascript
    ref: '', op: 'modify', part: '', partWarning: '', partFix: '',
```

改为：

```javascript
    ref: '', op: 'modify', part: '',
```

第 139 行：

```javascript
    setFrom(d){ this.ref = d.ref; this.op = d.op; this.part = d.part || ''; this.partWarning = ''; this.partFix = '';
```

改为：

```javascript
    setFrom(d){ this.ref = d.ref; this.op = d.op; this.part = d.part || '';
```

- [ ] **Step 5: 删除旧路由**

`app/routes/board.py` 中整个删除 `lint_part_route`（`@router.post(".../lint-part")` 装饰器连同函数体，约 198-213 行）。

删完后确认 `lint_part` 仍被 `_lint_with_note` 引用、`json` 仍被其他路由引用，两个 import 都保留。

- [ ] **Step 6: 删除旧测试**

`tests/test_routes.py` 删除这三个函数（约 214-249 行）：
- `test_lint_part_endpoint_returns_fix_via_hx_trigger`
- `test_lint_part_endpoint_returns_warning_without_changing_value`
- `test_lint_part_endpoint_skips_non_rcl_reference`

`tests/test_import_panel_ui.py` 删除这三个函数（约 118-144 行）：
- `test_edit_form_part_input_has_a_lint_indicator`
- `test_lint_part_route_reports_the_fix_message`
- `test_lint_part_route_reports_empty_fix_when_value_is_clean`

> 注意 `test_import_form_has_a_checking_indicator`（断言 `hx-indicator` 与 `正在检查`）**保留**——它靠的是导入表单自己的 `#import-status`（`node_detail.html:81,93`），与编辑表单无关。

- [ ] **Step 7: 跑测试确认通过**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py tests/test_routes.py tests/test_import_panel_ui.py -v
```

Expected: 全绿，且 `test_import_form_has_a_checking_indicator` 仍在通过列表里。

- [ ] **Step 8: 提交**

```bash
git add app/templates/_edit_form.html app/templates/node_detail.html app/routes/board.py tests/
git commit -m "feat：摘掉 Part 输入框的 blur lint，删除 /lint-part 死路由

输入路径上不再有任何 lint 往返。顺带修掉一个 clobber bug：提交后移焦会对
已清空的值再 lint 一次，其响应把用户新敲的值抹掉。"
```

---

### Task 4: 一次性 ⓘ（自动修正提示）

**Files:**
- Modify: `app/routes/board.py`（`edit_node`，约 214-250 行）
- Test: `tests/test_lint_async.py`

**Interfaces:**
- Consumes: Task 1 的 `_lint_with_note`、context 键 `fixed_ref`/`fixed_note`；Task 2 的面板行 ⓘ 渲染
- Produces: `edit_node` 在发生 fix 时，响应的面板里该行带 ⓘ

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_lint_async.py`：

```python
def test_edit_response_shows_the_fix_once(client):
    """写入响应里带 ⓘ 告诉用户值被改成了什么。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = _add(client, board_id, ws, "C3", "1000pF")
    assert "1000pF → 1nF" in r.text


def test_fix_note_is_not_persisted(client):
    """ⓘ 是一次性的：刷新页面后不再出现（原值在归一后不留存，无法重算）。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "C3", "1000pF")
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "1000pF → 1nF" not in r.text
    r2 = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert "1000pF → 1nF" not in r2.text
    assert "1nF" in r2.text          # 值本身归一成功了


def test_edit_response_carries_warning_and_does_not_retrigger(client):
    """写入响应自带完整 ⚠，且不挂 load——否则自动刷新会把一次性 ⓘ 冲掉。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = _add(client, board_id, ws, "R7", "230R")
    assert "不是标准" in r.text
    assert 'hx-trigger="load"' not in r.text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py -k "fix_once or not_persisted or carries_warning" -v
```

Expected: 前两个 FAIL（响应里没有 ⓘ 文案）；第三个可能已通过——`_node_update.html` 本就不带 load 触发器。

- [ ] **Step 3: 让 `edit_node` 带出 fix**

`app/routes/board.py` 的 `edit_node` 中，把：

```python
    part_val = _lint_or_none(reference, op, part)
```

改为：

```python
    part_val, fix_note = _lint_with_note(reference, op, part)
```

再把成功分支里的：

```python
    node = models.get_node(conn, node_id)
    ctx = _node_context(conn, board_id, node)
```

改为：

```python
    node = models.get_node(conn, node_id)
    ctx = _node_context(conn, board_id, node)
    if fix_note:
        # ⓘ 只能在这一刻挂上：值已归一，之后任何一次重算都拿不到 fix 级问题。
        ctx.update({"fixed_ref": reference, "fixed_note": fix_note})
```

- [ ] **Step 4: 跑测试确认通过**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_lint_async.py -v
```

Expected: 12 passed。

- [ ] **Step 5: 提交**

```bash
git add app/routes/board.py tests/test_lint_async.py
git commit -m "feat：写入响应带出一次性 ⓘ，告诉用户值被自动改成了什么

摘掉 blur lint 后用户看不到归一结果，面板行补上这条提示。它只能一次性
显示——归一后原始写法不留存，重算永远只剩 warning 级。"
```

---

### Task 5: 全量回归与两套主题人工自检

**Files:** 无代码改动（发现问题则回到对应 Task 修）

- [ ] **Step 1: 跑全量测试**

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest -q 2>&1 | tail -20
```

Expected: 通过数 ≈ 651 − 6（删掉的旧测试）+ 12（新测试）= 657，0 failed。
若 `rename_ui` 相关用例失败，单独重跑确认是端口竞态 flake：

```bash
/home/tong/code/reflow/.venv/bin/python -m pytest tests/test_rename_ui.py -q
```

- [ ] **Step 2: 起服务人工验证**

```bash
/home/tong/code/reflow/.venv/bin/python -m uvicorn app.main:app --port 8010
```

在浏览器里走一遍：

1. 建板 → 进工作区。
2. **连续快速录入**多条修改（如 `C3 / 新增 / 1000pF`、`R7 / 新增 / 230R`、`C4 / 新增 / 2.2uF`），每条敲完直接回车。确认：打字过程中**没有任何等待**，输入框立刻清空可以敲下一条，没有值被抹掉。
3. 确认 `C3` 那行显示 ⓘ，悬停能看到「1000pF → 1nF」；`R7` 那行显示 ⚠「不是标准阻值」。
4. 刷新页面。确认面板行先出现淡色「检查中」圆点，随即换成 ⚠；`C3` 的 ⓘ 已消失（一次性，符合设计），值仍是 `1nF`。
5. 撤销一条修改，确认面板刷新后 ⚠ 仍正确显示。

- [ ] **Step 3: 前端自检清单（`docs/前端风格指南.md`）**

- [ ] 没有裸色值（本次只用了现有的 `--border`）
- [ ] **白天和夜间两套主题都实际打开看过**（右上角 🌙/☀️ 切换），「检查中」圆点在两套主题下都看得见、不刺眼
- [ ] **≤720px 窄屏（DevTools 375px）实际查看过**面板行，图标不溢出
- [ ] 复用了现有组件类（`.lint-note`），没有内联 style
- [ ] 新模板遵守 Starlette 1.2.1 `TemplateResponse` 新签名

- [ ] **Step 4: 提交任何自检修正并推分支开 PR**

```bash
git push -u origin worktree-lint-buffer-async
gh pr create --draft --title "lint 移出输入路径，改为面板行的异步批量检查" --body "..."
```

---

## Self-Review

**1. Spec 覆盖检查**

| Spec 要求 | 实现任务 |
|---|---|
| 摘掉输入路径的 lint 往返 | Task 3 |
| 修掉 clobber bug | Task 3（Step 3 附注 + `test_edit_form_has_no_blur_lint`） |
| lint 结果显示在面板行上 | Task 2 |
| 异步批量取回、面板渲染不等 lint | Task 1 + Task 2 |
| ⓘ 一次性 / ⚠ 永久的不对称 | Task 1（`_lint_with_note`）+ Task 4 |
| `autolint` 冲突（自动刷新冲掉 ⓘ） | Task 1 Step 3（`_changes_panel_block.html` 不挂 load）+ Task 4 Step 1 第三个测试 |
| 不动 `_lint_or_none` 契约 | Task 1 Step 5（保留原 docstring，仅委托） |
| 不动提交路径 | 全程无改动 |
| 删除死路由与旧测试 | Task 3 Step 5-6 |
| 两套主题自检 | Task 5 Step 3 |

**说明一处对 spec 的偏离：** spec 写「复用 `lint_entries`」，计划改用 `lint_warning_for` 逐行调用。理由：`lint_entries` 返回的是扁平的 `LintNote` 列表，要按 reference 映射回行；而 `lint_warning_for` 直接给出「这一行的 warning」，正是 `_node_context` 原本就在用的形式，且天然处理 `part is None`。两者底层都是 `lint_part`，没有新造检查逻辑，符合 spec 的意图。

**2. 占位符扫描：** 无 TBD/TODO；每个代码步骤都给出了完整可粘贴的代码。

**3. 类型一致性：** `_lint_with_note` 在 Task 1 定义、Task 4 调用，签名一致（`tuple[str | None, str]`）。context 键 `lint_ready`/`fixed_ref`/`fixed_note` 在 Task 1 产出、Task 2 消费、Task 4 写入，命名一致。`_changes_panel_block.html` 在 Task 1 创建、Task 1 Step 6 引用，文件名一致。
