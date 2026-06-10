# Reflow UI/UX 改版实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-06-09-reflow-ui-ux-redesign-design.md` 整体升级 Reflow 所有页面的视觉与交互：修复表单残留、增加工作区撤销、统一新建流程、GitHub 工程风视觉，共解决 13 个交互问题。

**Architecture:** 渐进增强——保留 Jinja2 + HTMX 服务端渲染三层架构，新增一个纯逻辑校验模块；CSS 全量重写为 Primer 风格；引入 Alpine.js（CDN）处理客户端小交互。传播/冲突核心算法 `propagation.py` 不动。

**Tech Stack:** FastAPI / Jinja2 / HTMX 1.9（已有）+ Alpine.js 3（新增 CDN）+ 自定义 CSS。无构建步骤。

**分支：** 在已有的 `feature/ui-ux-redesign` 分支上执行（spec 已提交于此）。

**约定（全计划通用）：**
- 测试命令一律先 `. .venv/bin/activate`。
- HTMX 错误约定：校验失败返回 200 + 响应头 `HX-Retarget: #form-error` + `HX-Reswap: innerHTML`，正文为 `_form_error.html` 片段。
- toast 约定：HTMX 操作成功 → 响应头 `HX-Trigger: {"showToast": "消息"}`（`json.dumps` 默认 ensure_ascii，规避头部非 ASCII 问题）；整页跳转 → 重定向 URL 带 `?flash=消息`（RedirectResponse 自动百分号编码）。
- 中文 UI 文案、中文错误消息、中文注释。
- Starlette 1.2.1 模板新签名：`templates.TemplateResponse(request, "name.html", {ctx})`，context 不放 `"request"` 键。

---

## 文件结构总览

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/validation.py` | 新建 | 位号编辑校验（纯逻辑） |
| `tests/test_validation.py` | 新建 | 校验规则单测 |
| `app/static/style.css` | 重写 | Primer 风格全套样式 |
| `app/templates/base.html` | 重写 | 导航/面包屑/标题块/toast/Alpine |
| `app/templates/404.html` | 新建 | 中文 404 页 |
| `app/templates/_form_error.html` | 新建 | 表单错误片段 |
| `app/templates/node_detail.html` | 重写 | 两栏布局核心页 |
| `app/templates/_bom_table.html` | 重写 | BOM 表（工具栏/徽章/行操作） |
| `app/templates/_edit_form.html` | 新建 | 添加修改表单（Alpine 联动） |
| `app/templates/_changes_panel.html` | 新建 | 本节点修改面板（撤销入口） |
| `app/templates/_node_update.html` | 新建 | 编辑/撤销成功的 OOB 复合片段 |
| `app/templates/_conflict_modal.html` | 新建 | 冲突确认弹窗（替代 `_conflicts.html`，旧文件删除） |
| `app/templates/state_graph.html` | 重写 | 时间线（摘要徽章、最新在上） |
| `app/templates/home.html` | 重写 | 分组层级 + 唯一新建入口 |
| `app/templates/board_new.html` | 新建 | 统一新建单板页（替代 `new_bom_version.html` / `import_preview.html`，旧文件删除） |
| `app/templates/_new_preview.html` | 新建 | 新建页的版本状态/CSV 校验片段 |
| `app/templates/log.html` | 重写 | 日志页（可读节点/筛选/倒序） |
| `app/routes/board.py` | 修改 | 校验接入、撤销路由、页面上下文、冲突弹窗、404 |
| `app/routes/hierarchy.py` | 重写 | 首页分组、统一新建三路由（旧四路由删除） |
| `app/routes/log.py` | 修改 | 筛选参数 + models 查询 |
| `app/models.py` | 修改 | 新增 `node_summaries`、`list_board_log` |
| `app/main.py` | 修改 | 404 异常处理器 |
| `tests/test_models.py` | 修改 | 新增两个查询的测试 |
| `tests/test_routes.py` | 修改 | 撤销/校验/新建流程测试；改造 `_setup_board` |
| `CLAUDE.md` | 修改 | 路由表与前端约定更新 |

---

### Task 1: 位号编辑校验（纯逻辑）

**Files:**
- Create: `app/validation.py`
- Test: `tests/test_validation.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_validation.py
from app.validation import validate_edit

BOM = {"R1": "10k", "C1": "100nF"}


def test_modify_unknown_reference_rejected():
    assert "不存在" in validate_edit(BOM, "R9", "modify", "22k")


def test_add_existing_reference_rejected():
    assert "已存在" in validate_edit(BOM, "R1", "add", "22k")


def test_remove_unknown_reference_rejected():
    assert "不存在" in validate_edit(BOM, "R9", "remove", None)


def test_modify_requires_part():
    assert validate_edit(BOM, "R1", "modify", "  ") is not None


def test_add_requires_part():
    assert validate_edit(BOM, "R9", "add", "") is not None


def test_empty_reference_rejected():
    assert validate_edit(BOM, "  ", "modify", "1k") is not None


def test_unknown_op_rejected():
    assert validate_edit(BOM, "R1", "frob", "1k") is not None


def test_valid_modify_passes():
    assert validate_edit(BOM, "R1", "modify", "22k") is None


def test_valid_add_passes():
    assert validate_edit(BOM, "R9", "add", "1k") is None


def test_valid_remove_passes():
    assert validate_edit(BOM, "C1", "remove", None) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_validation.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.validation'`

- [ ] **Step 3: 最小实现**

```python
# app/validation.py
"""位号编辑校验（纯逻辑，零 Web/DB 依赖）。"""


def validate_edit(
    full_bom: dict[str, str], reference: str, op: str, part: str | None
) -> str | None:
    """校验对折叠后 BOM 的一次位号编辑。

    full_bom: 被编辑节点折叠后的完整 BOM（根节点即初始 BOM）。
    合法返回 None，否则返回中文错误消息。
    """
    reference = (reference or "").strip()
    if not reference:
        return "位号不能为空"
    has_part = bool((part or "").strip())
    if op == "add":
        if reference in full_bom:
            return f"位号 {reference} 已存在，请用「修改」"
        if not has_part:
            return "新增位号必须填写 Part"
    elif op == "modify":
        if reference not in full_bom:
            return f"位号 {reference} 不存在，无法修改"
        if not has_part:
            return "修改必须填写新 Part 值"
    elif op == "remove":
        if reference not in full_bom:
            return f"位号 {reference} 不存在或已是不贴状态"
    else:
        return f"未知操作类型：{op}"
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_validation.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add app/validation.py tests/test_validation.py
git commit -m "feat: 位号编辑校验纯逻辑"
```

---

### Task 2: 校验接入编辑路由（错误片段约定）

**Files:**
- Modify: `app/routes/board.py`
- Create: `app/templates/_form_error.html`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试（追加到 tests/test_routes.py 末尾）**

```python
def _workspace_id(client, board_id):
    from app import models
    from app.main import get_conn
    return models.workspace_node(get_conn(), int(board_id))["id"]


def test_edit_rejects_unknown_reference(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/edit",
                    data={"reference": "R99", "op": "modify", "part": "1k"})
    assert r.status_code == 200
    assert r.headers.get("HX-Retarget") == "#form-error"
    assert "不存在" in r.text


def test_edit_rejects_add_existing(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/edit",
                    data={"reference": "R1", "op": "add", "part": "1k"})
    assert "已存在" in r.text


def test_workspace_edit_rejects_invalid(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.post(f"/board/{board_id}/workspace/edit",
                    data={"reference": "R99", "op": "modify", "part": "1k"})
    assert r.status_code == 400
    assert "不存在" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -q -k "rejects"`
Expected: 3 failed（当前路由静默接受非法编辑）

- [ ] **Step 3: 实现**

`app/templates/_form_error.html`（新建）：

```html
<div class="flash flash-error">✕ {{ message }}</div>
```

`app/routes/board.py`：顶部 import 增加

```python
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse
from app.validation import validate_edit
```

新增模块级 helper（放在 `_node_diff` 旁）：

```python
def _validate(conn, node_id, reference, op, part) -> str | None:
    """对被编辑节点折叠后的 BOM 做位号编辑校验。"""
    initial, chain = models.get_chain(conn, node_id)
    return validate_edit(fold_bom(initial, chain), reference, op, part)
```

`edit_node` 开头（取 node 之后、应用之前）插入：

```python
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    reference = reference.strip()
    err = _validate(conn, node_id, reference, op, part)
    if err:
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": err},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
```

`workspace_edit` 同样校验（草稿节点取出后）：

```python
    reference = reference.strip()
    err = _validate(conn, ws["id"], reference, op, part)
    if err:
        return PlainTextResponse(err, status_code=400)
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `pytest -q`
Expected: 全部通过（36 旧 + 10 校验 + 3 新 = 49 passed）

- [ ] **Step 5: Commit**

```bash
git add app/routes/board.py app/templates/_form_error.html tests/test_routes.py
git commit -m "feat: 编辑路由接入位号校验，HTMX 错误片段约定"
```

---

### Task 3: 撤销工作区修改（后端路由）

**Files:**
- Modify: `app/routes/board.py`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_undo_draft_change(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/undo", data={"reference": "R1"})
    assert r.status_code == 200
    from app import models
    from app.main import get_conn
    assert models.get_change(get_conn(), ws, "R1") is None


def test_undo_rejected_on_committed_node(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    committed = _workspace_id(client, board_id)
    client.post(f"/board/{board_id}/commit", data={"message": "S1"})
    r = client.post(f"/board/{board_id}/node/{committed}/undo",
                    data={"reference": "R1"})
    assert "不能撤销" in r.text
    from app import models
    from app.main import get_conn
    assert models.get_change(get_conn(), committed, "R1") is not None


def test_undo_unknown_node_404(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.post(f"/board/{board_id}/node/9999/undo", data={"reference": "R1"})
    assert r.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -q -k "undo"`
Expected: 3 failed（405/404，路由不存在）

- [ ] **Step 3: 实现**

`app/routes/board.py` 顶部加 `import json`，新增路由：

```python
@router.post("/board/{board_id}/node/{node_id}/undo")
def undo_change(request: Request, board_id: int, node_id: int,
                reference: str = Form(...)):
    """撤销草稿节点对某位号的修改（从 changeset 删除，恢复继承）。仅限未提交节点。"""
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["is_committed"]:
        return templates.TemplateResponse(
            request, "_form_error.html",
            {"message": "已提交节点不能撤销，请使用「修正历史记录」"},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
    models.delete_change(conn, node_id, reference)
    node = models.get_node(conn, node_id)
    full, diff = _node_diff(conn, node)
    return templates.TemplateResponse(
        request, "_bom_table.html",
        {"board_id": board_id, "node": node, "bom": sorted(full.items()), "diff": diff},
        headers={"HX-Trigger": json.dumps({"showToast": f"↩ 已撤销 {reference} 的修改"})})
```

（注：本任务返回旧版 `_bom_table.html` 上下文；Task 5 会统一切到新片段 `_node_update.html`。）

- [ ] **Step 4: 运行确认通过**

Run: `pytest -q`
Expected: 52 passed

- [ ] **Step 5: Commit**

```bash
git add app/routes/board.py tests/test_routes.py
git commit -m "feat: 撤销草稿节点修改的路由（仅未提交节点）"
```

---

### Task 4: 全局骨架——样式、base 模板、404、toast

**Files:**
- Rewrite: `app/static/style.css`
- Rewrite: `app/templates/base.html`
- Create: `app/templates/404.html`
- Modify: `app/main.py`（404 处理器）
- Modify: `app/routes/board.py`、`app/routes/log.py`（取不到资源时 raise 404）
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_unknown_board_returns_chinese_404(client):
    r = client.get("/board/9999")
    assert r.status_code == 404
    assert "未找到" in r.text


def test_unknown_node_returns_chinese_404(client):
    loc = _setup_board(client)
    r = client.get(f"{loc}/node/9999")
    assert r.status_code == 404
    assert "未找到" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -q -k "404"`
Expected: 2 failed（当前抛 500 / TypeError）

- [ ] **Step 3: 实现 404 链路**

`app/main.py`——`create_app` 内（mount static 之后）加：

```python
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import PlainTextResponse

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request, exc):
        if exc.status_code == 404:
            return templates.TemplateResponse(
                request, "404.html", {}, status_code=404)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)
```

`app/templates/404.html`（新建）：

```html
{% extends "base.html" %}
{% block title %}未找到 — Reflow{% endblock %}
{% block content %}
<div class="empty">
  <h1>404 · 页面未找到</h1>
  <p class="muted">单板或节点不存在，可能已被删除或链接有误。</p>
  <p><a href="/">← 回首页</a></p>
</div>
{% endblock %}
```

`app/routes/board.py`——`state_graph` 与 `node_detail` 开头加保护：

```python
    # state_graph 中：
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    # node_detail 中：
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
```

`app/routes/log.py`——`board_log` 开头加：

```python
from fastapi import APIRouter, Request, HTTPException
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
```

（log.py 此步只加保护；模板上下文 Task 9 再改。注意 `log.html` 当前不使用 board 变量，传入无害。）

- [ ] **Step 4: 重写 base.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Reflow{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script defer src="https://unpkg.com/alpinejs@3.14.1/dist/cdn.min.js"></script>
</head>
<body>
<header class="topnav">
  <a class="brand" href="/">⟲ Reflow</a>
  <nav class="crumbs">{% block crumbs %}{% endblock %}</nav>
  <nav class="ctx">{% block ctxlinks %}{% endblock %}</nav>
</header>
<main>{% block content %}{% endblock %}</main>
<div id="modal"></div>
<div id="toast-zone">
  {% if request.query_params.get('flash') %}
  <div class="toast">{{ request.query_params.get('flash') }}</div>
  {% endif %}
</div>
<script>
  function showToast(msg){
    const z = document.getElementById('toast-zone');
    const t = document.createElement('div');
    t.className = 'toast'; t.textContent = msg;
    z.appendChild(t); setTimeout(() => t.remove(), 3500);
  }
  document.body.addEventListener('showToast', e => showToast(e.detail.value));
  document.querySelectorAll('#toast-zone .toast')
    .forEach(t => setTimeout(() => t.remove(), 3500));
</script>
</body>
</html>
```

- [ ] **Step 5: 重写 style.css（完整替换）**

```css
:root{
  --bg:#f6f8fa; --fg:#1f2328; --muted:#656d76; --border:#d0d7de; --border-soft:#d8dee4;
  --blue:#0969da; --blue-bg:#ddf4ff; --green:#1f883d; --red:#cf222e; --red-bg:#ffebe9;
  --yellow-bg:#fff8c5; --yellow-fg:#7d4e00; --purple:#6e40c9; --purple-bg:#fbefff;
  --radius:6px;
}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  margin:0;background:var(--bg);color:var(--fg);font-size:14px}
code{background:#eff1f3;padding:0 4px;border-radius:3px;font-size:.92em}
a{color:var(--blue);text-decoration:none} a:hover{text-decoration:underline}
h1{font-size:18px;margin:0}

.topnav{background:#fff;border-bottom:1px solid var(--border);padding:10px 24px;
  display:flex;align-items:center;gap:16px}
.topnav .brand{font-weight:700;color:var(--fg)}
.topnav .crumbs{color:var(--muted);font-size:13px}
.topnav .ctx{margin-left:auto;display:flex;gap:14px;font-size:13px}
main{max-width:1100px;margin:0 auto;padding:20px 24px}

.page-head{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.stable-link{margin-left:auto;font-size:12px}
.muted{color:var(--muted)}

.badge{display:inline-block;background:#eff1f3;color:var(--muted);border-radius:999px;
  padding:1px 10px;font-size:12px;white-space:nowrap}
.badge-blue{background:var(--blue-bg);color:var(--blue)}
.badge-green{background:#c7f0d2;color:#1a7f37}
.badge-yellow{background:#fff1b8;color:#9a6700}
.badge-red{background:var(--red-bg);color:var(--red)}
.badge-purple{background:var(--purple-bg);color:var(--purple)}

.panel{background:#fff;border:1px solid var(--border);border-radius:var(--radius);
  padding:12px;margin-bottom:12px}
.panel-title{font-weight:600;margin-bottom:8px}

.btn{display:inline-block;border:1px solid var(--border);border-radius:var(--radius);
  background:#f6f8fa;color:var(--fg);padding:5px 14px;font-size:13px;cursor:pointer;font-family:inherit}
.btn-primary{background:var(--green);border-color:var(--green);color:#fff;font-weight:600}
.btn-outline{background:#fff;border-color:var(--green);color:var(--green);font-weight:600}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-link{border:none;background:none;color:var(--blue);cursor:pointer;font-size:12px;
  padding:0;font-family:inherit}
.btn-link.danger{color:var(--red)}

.input{border:1px solid var(--border);border-radius:var(--radius);padding:5px 10px;
  font-size:13px;font-family:inherit;width:100%}
input[type=file]{font-size:13px}
label{font-size:13px}

.flash{border-radius:var(--radius);padding:8px 12px;font-size:13px;margin:8px 0;border:1px solid}
.flash-error{background:var(--red-bg);border-color:#cf222e66;color:var(--red)}
.flash-warn{background:var(--yellow-bg);border-color:#d4a72c66;color:var(--yellow-fg)}
.flash-info{background:var(--blue-bg);border-color:#54aeff66;color:var(--blue)}

.two-col{display:flex;gap:16px;align-items:flex-start}
.two-col > div:first-child{flex:1;min-width:0}
.two-col > aside{width:300px;flex-shrink:0}

.toolbar{display:flex;gap:8px;margin-bottom:8px;align-items:center}
.toolbar .input{width:220px}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.seg button,.seg label{border:none;background:#fff;padding:4px 12px;font-size:12px;
  cursor:pointer;color:var(--fg);font-family:inherit}
.seg .on{background:var(--blue);color:#fff}
.seg input[type=radio]{display:none}

table.bom{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--border);
  font-size:13px}
table.bom th{background:var(--bg);color:var(--muted);font-weight:600;text-align:left;padding:6px 12px}
table.bom td{border-top:1px solid var(--border-soft);padding:5px 12px}
tr.row-add{background:#dafbe1}
tr.row-modify{background:var(--yellow-bg)}
tr.row-remove{background:var(--red-bg)}
.strike{text-decoration:line-through;color:var(--muted)}
.old-val{color:var(--muted);text-decoration:line-through;font-size:12px;margin-left:6px}
td.row-actions{text-align:right;white-space:nowrap}
table.bom tr .hover-only{visibility:hidden}
table.bom tr:hover .hover-only{visibility:visible}

.change-row{display:flex;justify-content:space-between;align-items:center;
  padding:5px 0;border-top:1px solid var(--border-soft);font-size:13px}
.change-row:first-of-type{border-top:none}
.form-stack{display:flex;flex-direction:column;gap:10px;max-width:420px}
.edit-form{display:flex;flex-direction:column;gap:8px}
.commit-box{display:flex;flex-direction:column;gap:8px}
details.panel summary{cursor:pointer;font-weight:600}

.timeline{margin-top:8px}
.tl-item{display:block;position:relative;padding:0 0 16px 26px;border-left:2px solid #c7cedb;
  margin-left:8px;color:inherit}
.tl-item:hover{text-decoration:none}
.tl-item.draft{border-left-style:dashed}
.tl-item:last-child{border-left-color:transparent}
.tl-item .dot{position:absolute;left:-7px;top:2px;width:12px;height:12px;border-radius:50%;
  background:var(--blue);border:2px solid #fff}
.tl-item.draft .dot{background:#fff;border-color:var(--blue)}
.tl-item.root .dot{background:var(--purple)}
.tl-card{background:#fff;border:1px solid var(--border);border-radius:var(--radius);padding:8px 12px}
.tl-item.draft .tl-card{border-color:#54aeff}
.tl-item:hover .tl-card{border-color:var(--blue)}

.group-title{font-size:15px;font-weight:600;margin:18px 0 8px}
.panel.version{padding:0}
.version-head{display:flex;align-items:center;gap:8px;padding:8px 12px;
  border-bottom:1px solid var(--border-soft);font-size:13px}
.chips{display:flex;gap:8px;flex-wrap:wrap;padding:8px 12px}
.chip{border:1px solid var(--border);border-radius:999px;padding:3px 12px;font-size:13px}
.chip.pending{border-color:#54aeff;background:var(--blue-bg)}
.problem-list{margin:6px 0;padding-left:20px;font-size:13px;color:var(--red)}
.empty{border:1px dashed var(--border);border-radius:var(--radius);padding:24px;
  text-align:center;color:var(--muted);background:#fff}

.modal-overlay{position:fixed;inset:0;background:rgba(31,35,40,.45);display:flex;
  align-items:flex-start;justify-content:center;padding-top:10vh;z-index:50}
.modal{background:#fff;border-radius:8px;max-width:480px;width:92%;
  box-shadow:0 8px 24px rgba(0,0,0,.3)}
.modal-head{padding:10px 16px;border-bottom:1px solid var(--border);font-weight:600}
.modal .conflict{border:none;margin:0;padding:10px 16px}
.modal .conflict label{display:block;border:1px solid var(--border);border-radius:var(--radius);
  padding:8px 10px;margin-top:6px;cursor:pointer}
.modal .conflict label:has(input:checked){border-color:var(--blue);background:var(--blue-bg)}
.modal-foot{padding:10px 16px;border-top:1px solid var(--border);display:flex;gap:10px;
  justify-content:flex-end}

#toast-zone{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;
  gap:8px;z-index:60}
.toast{background:#1f2328;color:#fff;border-radius:var(--radius);padding:8px 14px;
  font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,.25)}
```

- [ ] **Step 6: 全量回归**

Run: `pytest -q`
Expected: 54 passed（旧模板只用 `content` 块，与新 base 兼容）

- [ ] **Step 7: Commit**

```bash
git add app/static/style.css app/templates/base.html app/templates/404.html app/main.py app/routes/board.py app/routes/log.py tests/test_routes.py
git commit -m "feat: 全局骨架——Primer 风样式、导航、中文 404、toast/flash"
```

---

### Task 5: 节点详情页改版（两栏布局 + 全部交互）

**Files:**
- Modify: `app/routes/board.py`（上下文构建、edit/undo 切新片段、冲突弹窗、commit/resolve flash）
- Rewrite: `app/templates/node_detail.html`、`app/templates/_bom_table.html`
- Create: `app/templates/_edit_form.html`、`_changes_panel.html`、`_node_update.html`、`_conflict_modal.html`
- Delete: `app/templates/_conflicts.html`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_node_detail_shows_changes_panel_and_badges(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    ws = _workspace_id(client, board_id)
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "本节点修改" in r.text
    assert "撤销" in r.text
    assert "47k" in r.text and "10k" in r.text   # 新值 + 划线旧值


def test_committed_node_shows_history_warning(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    ws = _workspace_id(client, board_id)
    client.post(f"/board/{board_id}/commit", data={"message": "S1"})
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "修正历史记录" in r.text
    assert "撤销" not in r.text            # 已提交节点无撤销入口
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -q -k "panel or warning"`
Expected: 2 failed

- [ ] **Step 3: 路由层——上下文构建器与片段切换**

`app/routes/board.py`——用下面的 `_node_context` 替换 `_node_diff`（连同所有调用点）：

```python
def _node_context(conn, board_id: int, node) -> dict:
    """节点页/片段的完整渲染上下文：行数据（含旧值）、不贴行、修改面板、统计。"""
    board = models.get_board(conn, board_id)
    initial, chain = models.get_chain(conn, node["id"])
    full = fold_bom(initial, chain)
    parent_full = fold_bom(initial, chain[:-1])
    changes = {c["reference"]: c for c in models.get_changeset(conn, node["id"])}

    rows = []
    for ref, part in sorted(full.items()):
        ch = changes.get(ref)
        rows.append({
            "reference": ref, "part": part,
            "state": "mine" if ch else None,
            "op": ch["op"] if ch else None,
            "old": parent_full.get(ref),
        })

    # 「不贴」行 = 出现过（初始或链上 add/modify）但不在折叠结果里的位号
    known = set(initial)
    for cs in chain:
        for c in cs:
            if c["op"] in ("add", "modify"):
                known.add(c["reference"])

    def _last_value(ref):
        v = initial.get(ref)
        for cs in chain:
            for c in cs:
                if c["reference"] == ref and c["op"] != "remove":
                    v = c["part"]
        return v

    removed = [
        {"reference": ref, "part": _last_value(ref),
         "state": "mine" if ref in changes else "upstream"}
        for ref in sorted(known - set(full))
    ]
    return {
        "board": board, "board_id": board_id, "node": node,
        "rows": rows, "removed": removed,
        "changes": list(changes.values()),
        "all_refs": sorted(known),
        "total": len(full), "mine_count": len(changes), "removed_count": len(removed),
    }
```

`node_detail` 路由改为：

```python
@router.get("/board/{board_id}/node/{node_id}")
def node_detail(request: Request, board_id: int, node_id: int):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return templates.TemplateResponse(
        request, "node_detail.html", _node_context(conn, board_id, node))
```

`edit_node` 成功/冲突分支改为（校验部分沿用 Task 2）：

```python
    part_val = None if op == "remove" else part
    if node["parent_id"] is None:
        board = models.get_board(conn, board_id)
        old_value = propagation._resolved_value(conn, node_id, reference)
        models.update_initial_bom(conn, board["board_name"], board["pcb_version"],
                                  board["bom_version"], reference, part_val)
        audit.record_edit(conn, node_id, reference, old_value, part_val, op, "direct")
        conflicts = propagation._detect_downstream_conflicts(conn, node, reference, part_val)
    else:
        conflicts = propagation.apply_node_edit(conn, node_id, reference, op, part_val)

    node = models.get_node(conn, node_id)
    ctx = _node_context(conn, board_id, node)
    if conflicts:
        ctx.update({"conflicts": conflicts, "node_id": node_id})
        return templates.TemplateResponse(
            request, "_conflict_modal.html", ctx,
            headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})
    label = {"add": "已新增", "modify": "已修改", "remove": "已设为不贴"}[op]
    msg = f"✓ {label}：{reference}" + (f" → {part_val}" if part_val else "")
    return templates.TemplateResponse(
        request, "_node_update.html", ctx,
        headers={"HX-Trigger": json.dumps({"showToast": msg})})
```

`undo_change` 末尾改为：

```python
    models.delete_change(conn, node_id, reference)
    node = models.get_node(conn, node_id)
    return templates.TemplateResponse(
        request, "_node_update.html", _node_context(conn, board_id, node),
        headers={"HX-Trigger": json.dumps({"showToast": f"↩ 已撤销 {reference} 的修改"})})
```

`resolve` 与 `commit` 的重定向加 flash：

```python
    return RedirectResponse(f"/board/{board_id}/node/{node_id}?flash=✓ 冲突已确认",
                            status_code=303)
    # commit：
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已提交：{message}", status_code=303)
```

- [ ] **Step 4: 模板——node_detail.html（完整替换）**

```html
{% extends "base.html" %}
{% block title %}{% if node.is_committed %}{{ node.message or '节点 #' ~ node.id }}{% else %}工作区{% endif %} · 板 {{ board.board_uid }} — Reflow{% endblock %}
{% block crumbs %}
<a href="/">首页</a> /
<a href="/board/{{ board_id }}">{{ board.board_name }} / {{ board.pcb_version }} / {{ board.bom_version }} / 板 {{ board.board_uid }}</a>
{% endblock %}
{% block ctxlinks %}
<a href="/board/{{ board_id }}">状态图</a>
<a href="/board/{{ board_id }}/log">审计日志</a>
{% endblock %}
{% block content %}
<div class="page-head">
  <h1>{% if not node.is_committed %}工作区草稿{% elif node.parent_id is none %}初始状态{% else %}#{{ node.id }} {{ node.message or '(无说明)' }}{% endif %}</h1>
  {% if node.is_committed %}
  <span class="badge">已提交 · {{ node.committed_at }}</span>
  {% else %}
  <span class="badge badge-blue">未提交 · {{ changes|length }} 条修改</span>
  {% endif %}
  <span class="muted stable-link">稳定链接 <code>/board/{{ board_id }}/node/{{ node.id }}</code></span>
</div>

{% if node.is_committed %}
<div class="flash flash-warn">⚠ 这是已提交的历史节点。在此编辑＝<b>修正历史记录</b>，会自动向下游传播；若下游改过同一位号将需要你逐个确认。</div>
{% endif %}

<div class="two-col" x-data="bomPage()" @fill-form.window="setFrom($event.detail)">
  <div>
    <div id="bom">{% include "_bom_table.html" %}</div>
  </div>
  <aside>
    {% if node.is_committed %}
    <details class="panel">
      <summary>修正历史记录…</summary>
      {% include "_edit_form.html" %}
    </details>
    {% else %}
    <div class="panel">
      <div class="panel-title">添加修改</div>
      {% include "_edit_form.html" %}
    </div>
    {% endif %}
    <div id="changes-panel" class="panel">{% include "_changes_panel.html" %}</div>
    {% if not node.is_committed %}
    <form class="panel commit-box" method="post" action="/board/{{ board_id }}/commit">
      <input class="input" name="message" placeholder="commit 说明" required>
      <button class="btn btn-outline">提交为新节点</button>
    </form>
    {% endif %}
  </aside>
</div>

<script>
function bomPage(){
  return {
    q: '', tab: 'all',
    ref: '', op: 'modify', part: '',
    rowVisible(el){
      const d = el.dataset;
      if (this.tab === 'mine' && d.state !== 'mine') return false;
      if (this.tab === 'removed' && d.removed !== '1') return false;
      const q = this.q.trim().toLowerCase();
      return !q || d.ref.toLowerCase().includes(q)
                || (d.part || '').toLowerCase().includes(q);
    },
    fill(ref, op, part){
      window.dispatchEvent(new CustomEvent('fill-form', {detail: {ref, op, part}}));
    },
    setFrom(d){ this.ref = d.ref; this.op = d.op; this.part = d.part || ''; },
  };
}
</script>
{% endblock %}
```

- [ ] **Step 5: 模板——_bom_table.html（完整替换）**

```html
<div class="toolbar">
  <input class="input" placeholder="筛选位号 / Part…" x-model="q">
  <div class="seg">
    <button type="button" :class="{on: tab==='all'}" @click="tab='all'">全部 {{ total }}</button>
    <button type="button" :class="{on: tab==='mine'}" @click="tab='mine'">本节点修改 {{ mine_count }}</button>
    <button type="button" :class="{on: tab==='removed'}" @click="tab='removed'">不贴 {{ removed_count }}</button>
  </div>
</div>
<table class="bom">
  <thead><tr><th>位号</th><th>Part</th><th>状态</th><th></th></tr></thead>
  <tbody>
  {% for r in rows %}
  <tr data-ref="{{ r.reference }}" data-part="{{ r.part }}" data-state="{{ r.state or '' }}"
      data-removed="0" x-show="rowVisible($el)"
      class="{% if r.state == 'mine' %}row-{{ r.op }}{% endif %}">
    <td><code>{{ r.reference }}</code></td>
    <td>{{ r.part }}{% if r.state == 'mine' and r.op == 'modify' and r.old %}<span class="old-val">{{ r.old }}</span>{% endif %}</td>
    <td>
      {% if r.state == 'mine' %}
      <span class="badge {{ 'badge-green' if r.op == 'add' else 'badge-yellow' }}">本节点 · {{ {'add':'新增','modify':'修改'}[r.op] }}</span>
      {% endif %}
    </td>
    <td class="row-actions">
      {% if r.state == 'mine' and not node.is_committed %}
      <button class="btn-link danger"
              hx-post="/board/{{ board_id }}/node/{{ node.id }}/undo"
              hx-vals='{"reference": "{{ r.reference }}"}'
              hx-target="#bom" hx-swap="innerHTML">↩ 撤销</button>
      {% else %}
      <span class="hover-only">
        <button type="button" class="btn-link" @click="fill('{{ r.reference }}', 'modify', '{{ r.part }}')">修改</button>
        <button type="button" class="btn-link danger" @click="fill('{{ r.reference }}', 'remove', '')">不贴</button>
      </span>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
  {% for r in removed %}
  <tr data-ref="{{ r.reference }}" data-part="{{ r.part or '' }}" data-state="{{ r.state }}"
      data-removed="1" x-show="rowVisible($el)"
      class="{% if r.state == 'mine' %}row-remove{% endif %}">
    <td><code>{{ r.reference }}</code></td>
    <td><span class="strike">{{ r.part or '—' }}</span></td>
    <td>
      {% if r.state == 'mine' %}
      <span class="badge badge-red">本节点 · 不贴</span>
      {% else %}
      <span class="badge">上游 · 不贴</span>
      {% endif %}
    </td>
    <td class="row-actions">
      {% if r.state == 'mine' and not node.is_committed %}
      <button class="btn-link danger"
              hx-post="/board/{{ board_id }}/node/{{ node.id }}/undo"
              hx-vals='{"reference": "{{ r.reference }}"}'
              hx-target="#bom" hx-swap="innerHTML">↩ 撤销</button>
      {% else %}
      <button type="button" class="btn-link hover-only" @click="fill('{{ r.reference }}', 'add', '')">恢复贴装</button>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
```

- [ ] **Step 6: 模板——_edit_form.html / _changes_panel.html / _node_update.html / _conflict_modal.html（新建），删除 _conflicts.html**

`_edit_form.html`：

```html
<form class="edit-form"
      hx-post="/board/{{ board_id }}/node/{{ node.id }}/edit"
      hx-target="#bom" hx-swap="innerHTML"
      @htmx:after-request="if ($event.detail.successful && !$event.detail.xhr.getResponseHeader('HX-Retarget')) { ref=''; part=''; op='modify'; }">
  <input class="input" name="reference" x-model="ref" list="refs"
         placeholder="位号（自动补全）" required autocomplete="off">
  <datalist id="refs">{% for ref in all_refs %}<option value="{{ ref }}">{% endfor %}</datalist>
  <div class="seg">
    <label :class="{on: op==='modify'}"><input type="radio" name="op" value="modify" x-model="op">修改</label>
    <label :class="{on: op==='add'}"><input type="radio" name="op" value="add" x-model="op">新增</label>
    <label :class="{on: op==='remove'}"><input type="radio" name="op" value="remove" x-model="op">不贴</label>
  </div>
  <input class="input" name="part" x-model="part" placeholder="新 Part 值" :disabled="op==='remove'">
  <button class="btn btn-primary">应用修正</button>
  <div id="form-error"></div>
</form>
```

`_changes_panel.html`：

```html
<div class="panel-title">本节点修改（{{ changes|length }}）</div>
{% if not changes %}<p class="muted">还没有修改</p>{% endif %}
{% for c in changes %}
<div class="change-row">
  <span><code>{{ c.reference }}</code>
    {% if c.op == 'remove' %}不贴{% elif c.op == 'add' %}新增 → {{ c.part }}{% else %}修改 → {{ c.part }}{% endif %}
  </span>
  {% if not node.is_committed %}
  <button class="btn-link danger" title="撤销"
          hx-post="/board/{{ board_id }}/node/{{ node.id }}/undo"
          hx-vals='{"reference": "{{ c.reference }}"}'
          hx-target="#bom" hx-swap="innerHTML">↩</button>
  {% endif %}
</div>
{% endfor %}
```

`_node_update.html`（编辑/撤销成功的复合响应：主体换表格，OOB 换面板、清错误）：

```html
{% include "_bom_table.html" %}
<div id="changes-panel" class="panel" hx-swap-oob="true">{% include "_changes_panel.html" %}</div>
<div id="form-error" hx-swap-oob="true"></div>
```

`_conflict_modal.html`（主体进 #modal，OOB 同步表格/面板——编辑已落库）：

```html
<div class="modal-overlay">
  <div class="modal">
    <div class="modal-head">下游节点改过同一位号，请确认</div>
    <form method="post" action="/board/{{ board_id }}/node/{{ node_id }}/resolve">
      {% for c in conflicts %}
      <fieldset class="conflict">
        <legend><code>{{ c.reference }}</code> 在下游节点 #{{ c.downstream_node_id }} 被显式修改过</legend>
        <input type="hidden" name="downstream_node_id" value="{{ c.downstream_node_id }}">
        <input type="hidden" name="reference" value="{{ c.reference }}">
        <label><input type="radio" name="choice" value="keep" checked>
          保留下游值（{{ c.downstream_value or '不贴' }}），下游不变</label>
        <label><input type="radio" name="choice" value="take">
          采用修正值（{{ c.corrected_value or '不贴' }}）并向后传播</label>
      </fieldset>
      {% endfor %}
      <div class="modal-foot">
        <button type="button" class="btn"
                onclick="document.getElementById('modal').innerHTML=''">取消（保留下游值）</button>
        <button class="btn btn-primary">确认</button>
      </div>
    </form>
  </div>
</div>
<div id="bom" hx-swap-oob="innerHTML">{% include "_bom_table.html" %}</div>
<div id="changes-panel" class="panel" hx-swap-oob="true">{% include "_changes_panel.html" %}</div>
<div id="form-error" hx-swap-oob="true"></div>
```

```bash
rm app/templates/_conflicts.html
```

- [ ] **Step 7: 全量回归（含既有冲突测试）**

Run: `pytest -q`
Expected: 56 passed。重点确认 `test_edit_history_node_returns_conflict_fragment`（断言"采用修正值"在新弹窗里依然成立）与 Task 3 撤销测试通过。

- [ ] **Step 8: 手动验证**

```bash
REFLOW_DB=/tmp/reflow-manual.sqlite uvicorn app.main:app --port 8001
```
浏览器检查：添加修改后表单自动清空且 toast 出现；选「不贴」时 Part 禁用；点行尾「修改」自动填表单；撤销按钮工作且面板/表格同步；筛选框与三个标签过滤正确；历史节点显示警示条、表单收起、无撤销；编辑历史节点触发冲突弹窗、取消可关闭。

- [ ] **Step 9: Commit**

```bash
git add -A app/templates app/routes/board.py tests/test_routes.py
git commit -m "feat: 节点详情页改版——两栏布局、行内操作、撤销入口、表单联动、冲突弹窗"
```

---

### Task 6: 状态图改版（节点摘要 + 时间线）

**Files:**
- Modify: `app/models.py`（`node_summaries`）
- Modify: `app/routes/board.py`（state_graph 上下文）
- Rewrite: `app/templates/state_graph.html`
- Test: `tests/test_models.py`、`tests/test_routes.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py` 追加（沿用该文件已有的连接构造方式；若已有 fixture 则复用）：

```python
def test_node_summaries(tmp_path):
    from app.db import connect, init_db
    from app.csv_import import CsvEntry
    from app import models
    conn = connect(str(tmp_path / "t.sqlite")); init_db(conn)
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(conn, "B", "v1", "bomA", "1")
    ws = models.workspace_node(conn, bid)
    models.set_change(conn, ws["id"], "R1", "modify", "22k")
    s = models.node_summaries(conn, bid)
    root = models.list_nodes(conn, bid)[0]
    assert s[root["id"]] == []
    assert s[ws["id"]] == [{"reference": "R1", "op": "modify"}]
```

`tests/test_routes.py` 追加：

```python
def test_state_graph_shows_summary(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    r = client.get(f"/board/{board_id}")
    assert "工作区草稿" in r.text
    assert "R1" in r.text and "修改" in r.text
    assert "初始状态" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest -q -k "summaries or summary"`
Expected: 2 failed

- [ ] **Step 3: 实现**

`app/models.py` 追加（changeset 区段末尾）：

```python
def node_summaries(conn, board_id) -> dict[int, list[dict]]:
    """每个节点的 changeset 摘要 {node_id: [{'reference','op'}, ...]}（节点内按写入顺序）。"""
    rows = conn.execute(
        "SELECT n.id AS node_id, c.reference, c.op FROM nodes n"
        " LEFT JOIN node_changes c ON c.node_id = n.id"
        " WHERE n.board_id = ? ORDER BY n.id, c.id",
        (board_id,),
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["node_id"], [])
        if r["reference"] is not None:
            out[r["node_id"]].append({"reference": r["reference"], "op": r["op"]})
    return out
```

`app/routes/board.py`——`state_graph` 改为：

```python
@router.get("/board/{board_id}")
def state_graph(request: Request, board_id: int):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    nodes = list(reversed(models.list_nodes(conn, board_id)))   # 最新在上
    initial_count = len(models.get_initial_bom(
        conn, board["board_name"], board["pcb_version"], board["bom_version"]))
    return templates.TemplateResponse(
        request, "state_graph.html",
        {"board": board, "board_id": board_id, "nodes": nodes,
         "summaries": models.node_summaries(conn, board_id),
         "initial_count": initial_count})
```

`app/templates/state_graph.html`（完整替换）：

```html
{% extends "base.html" %}
{% block title %}板 {{ board.board_uid }} · 状态图 — Reflow{% endblock %}
{% block crumbs %}
<a href="/">首页</a> /
{{ board.board_name }} / {{ board.pcb_version }} / {{ board.bom_version }} / 板 {{ board.board_uid }}
{% endblock %}
{% block ctxlinks %}<a href="/board/{{ board_id }}/log">审计日志</a>{% endblock %}
{% block content %}
<div class="page-head"><h1>状态演进</h1></div>
<div class="timeline">
{% for n in nodes %}
{% set s = summaries[n.id] %}
<a class="tl-item {{ 'draft' if not n.is_committed else '' }} {{ 'root' if n.parent_id is none else '' }}"
   href="/board/{{ board_id }}/node/{{ n.id }}">
  <span class="dot"></span>
  <div class="tl-card">
    <b>{% if not n.is_committed %}工作区草稿{% elif n.parent_id is none %}初始状态{% else %}#{{ n.id }} {{ n.message or '(无说明)' }}{% endif %}</b>
    {% if n.parent_id is none %}
    <span class="badge badge-purple">初始 BOM · {{ initial_count }} 位号</span>
    {% elif not n.is_committed %}
    <span class="badge badge-blue">{{ s|length }} 条未提交</span>
    {% else %}
    <span class="badge">{{ s|length }} 条修改</span>
    {% endif %}
    <div class="muted">
      {%- for c in s[:4] -%}
        {{ c.reference }} {{ {'add': '新增', 'modify': '修改', 'remove': '不贴'}[c.op] }}{% if not loop.last %} · {% endif %}
      {%- endfor -%}
      {%- if s|length > 4 %} …{% endif -%}
      {%- if s %} · {% endif %}{{ n.committed_at or n.created_at }}
    </div>
  </div>
</a>
{% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 4: 全量回归 + Commit**

Run: `pytest -q` — Expected: 58 passed

```bash
git add app/models.py app/routes/board.py app/templates/state_graph.html tests/test_models.py tests/test_routes.py
git commit -m "feat: 状态图改版——时间线最新在上、节点改动摘要徽章"
```

---

### Task 7: 首页改版（层级分组）

**Files:**
- Modify: `app/routes/hierarchy.py`（home 分组数据）
- Rewrite: `app/templates/home.html`
- Test: `tests/test_routes.py`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_home_groups_by_board_name(client):
    loc = _setup_board(client)
    r = client.get("/")
    assert "新建单板" in r.text           # 唯一新建入口
    assert "板 3" in r.text               # 单板芯片
    assert "1 个位号" in r.text           # 版本卡片统计


def test_home_empty_state(client):
    r = client.get("/")
    assert "还没有" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -q -k "home"`
Expected: 2 failed（test_home_page_loads 仍应通过）

- [ ] **Step 3: 实现**

`app/routes/hierarchy.py`——`home` 改为：

```python
@router.get("/")
def home(request: Request):
    conn = get_conn()
    groups: dict[str, list[dict]] = {}
    for v in models.list_bom_versions(conn):
        boards = []
        for b in models.list_boards(conn, v["board_name"], v["pcb_version"], v["bom_version"]):
            ws = models.workspace_node(conn, b["id"])
            boards.append({
                "id": b["id"], "board_uid": b["board_uid"],
                "node_count": len(models.list_nodes(conn, b["id"])),
                "pending": len(models.get_changeset(conn, ws["id"])) if ws else 0,
            })
        ref_count = len(models.get_initial_bom(
            conn, v["board_name"], v["pcb_version"], v["bom_version"]))
        groups.setdefault(v["board_name"], []).append({
            "pcb_version": v["pcb_version"], "bom_version": v["bom_version"],
            "ref_count": ref_count, "boards": boards,
        })
    return templates.TemplateResponse(request, "home.html", {"groups": groups})
```

`app/templates/home.html`（完整替换；「＋新建单板」入口指向 Task 8 的 `/board/new`，本任务先放链接，Task 8 实现该页）：

```html
{% extends "base.html" %}
{% block title %}Reflow — 单板 BOM 状态管理{% endblock %}
{% block ctxlinks %}<a class="btn btn-primary" href="/board/new">＋ 新建单板</a>{% endblock %}
{% block content %}
{% if not groups %}
<div class="empty">
  <h1>还没有任何单板</h1>
  <p class="muted">点右上角「＋ 新建单板」上传初始 BOM CSV 开始。</p>
</div>
{% endif %}
{% for name, versions in groups.items() %}
<div class="group-title">📋 {{ name }}</div>
{% for v in versions %}
<div class="panel version">
  <div class="version-head">
    <span class="badge">PCB {{ v.pcb_version }}</span>
    <b>{{ v.bom_version }}</b>
    <span class="muted">{{ v.ref_count }} 个位号 · {{ v.boards|length }} 块单板</span>
  </div>
  {% if v.boards %}
  <div class="chips">
    {% for b in v.boards %}
    <a class="chip {{ 'pending' if b.pending else '' }}" href="/board/{{ b.id }}">
      板 {{ b.board_uid }}
      <span class="muted">· {{ b.node_count }} 个节点{% if b.pending %} · {{ b.pending }} 条未提交{% endif %}</span>
    </a>
    {% endfor %}
  </div>
  {% endif %}
</div>
{% endfor %}
{% endfor %}
{% endblock %}
```

- [ ] **Step 4: 全量回归 + Commit**

Run: `pytest -q` — Expected: 60 passed

```bash
git add app/routes/hierarchy.py app/templates/home.html tests/test_routes.py
git commit -m "feat: 首页改版——按单板名称分组、单板芯片、空状态、唯一新建入口"
```

---

### Task 8: 统一「新建单板」流程

**Files:**
- Modify: `app/routes/hierarchy.py`（新增 3 路由，删除旧 4 路由）
- Create: `app/templates/board_new.html`、`app/templates/_new_preview.html`
- Delete: `app/templates/new_bom_version.html`、`app/templates/import_preview.html`
- Test: `tests/test_routes.py`（改造 `_setup_board` 与旧流程测试）

- [ ] **Step 1: 改造测试——替换旧流程测试与 `_setup_board`**

`tests/test_routes.py`：**删除** `test_import_preview_then_create_bom_version` 和 `test_create_board_then_state_graph`，`_setup_board` 替换为：

```python
def _setup_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0]      # /board/{id}
```

新增测试：

```python
def test_create_board_with_new_version(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/board/")


def test_create_second_board_on_existing_version_without_csv(client):
    _setup_board(client)
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "4"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_preview_existing_version_needs_no_csv(client):
    _setup_board(client)
    r = client.post("/board/new/preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"})
    assert "已有版本" in r.text
    assert "disabled" not in r.text


def test_preview_blocks_on_csv_problems(client):
    csv = 'Reference,Part\n"R1,R2",10k\nR1,22k\n'
    r = client.post("/board/new/preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"},
                    files={"file": ("bom.csv", csv, "text/csv")})
    assert "校验问题" in r.text
    assert "disabled" in r.text


def test_preview_new_version_without_csv_warns(client):
    r = client.post("/board/new/preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"})
    assert "请选择" in r.text
    assert "disabled" in r.text


def test_create_rejects_csv_with_problems(client):
    csv = 'Reference,Part\n"R1,R2",10k\nR1,22k\n'
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", csv, "text/csv")})
    assert r.status_code == 400


def test_create_rejects_new_version_without_csv(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"})
    assert r.status_code == 400


def test_board_new_page_loads(client):
    r = client.get("/board/new")
    assert r.status_code == 200
    assert "新建单板" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -q`
Expected: 大量 fail（`/board/new` 不存在，`_setup_board` 已切新路由）

- [ ] **Step 3: 重写 hierarchy.py 路由（保留 Task 7 的 home）**

删除 `new_bom_version`、`import_preview`、`create_bom_version`、`create_board` 四个旧路由，新增：

```python
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException


def _strip(*vals: str) -> list[str]:
    return [v.strip() for v in vals]


async def _read_csv(file: UploadFile):
    """读取上传 CSV，返回 (entries, problems, error_message)。"""
    try:
        text = (await file.read()).decode("utf-8")
        entries, problems = parse_bom_csv(text)
        return entries, problems, None
    except UnicodeDecodeError:
        return [], [], "文件不是 UTF-8 编码"
    except ValueError as e:
        return [], [], str(e)


@router.get("/board/new")
def board_new(request: Request):
    conn = get_conn()
    versions = models.list_bom_versions(conn)
    return templates.TemplateResponse(request, "board_new.html", {
        "names": sorted({v["board_name"] for v in versions}),
        "pcbs": sorted({v["pcb_version"] for v in versions}),
        "boms": sorted({v["bom_version"] for v in versions}),
    })


@router.post("/board/new/preview")
async def board_new_preview(
    request: Request,
    board_name: str = Form(""), pcb_version: str = Form(""),
    bom_version: str = Form(""), file: UploadFile | None = File(None),
):
    conn = get_conn()
    board_name, pcb_version, bom_version = _strip(board_name, pcb_version, bom_version)
    ctx: dict = {"ready": False, "status": "fill", "problems": [], "ref_count": 0,
                 "message": ""}
    if board_name and pcb_version and bom_version:
        existing = models.get_initial_bom(conn, board_name, pcb_version, bom_version)
        if existing:
            ctx.update(status="exists", ready=True, ref_count=len(existing))
        elif file is None or not file.filename:
            ctx["status"] = "need_csv"
        else:
            entries, problems, err = await _read_csv(file)
            if err:
                ctx.update(status="bad_csv", message=err)
            else:
                ctx.update(status="csv", problems=problems,
                           ref_count=len(entries), ready=not problems)
    return templates.TemplateResponse(request, "_new_preview.html", ctx)


@router.post("/board/new")
async def board_create(
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), board_uid: str = Form(...),
    file: UploadFile | None = File(None),
):
    conn = get_conn()
    board_name, pcb_version, bom_version, board_uid = _strip(
        board_name, pcb_version, bom_version, board_uid)
    if not models.get_initial_bom(conn, board_name, pcb_version, bom_version):
        if file is None or not file.filename:
            raise HTTPException(status_code=400, detail="新 BOM 版本必须上传初始 BOM CSV")
        entries, problems, err = await _read_csv(file)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if problems:
            raise HTTPException(status_code=400, detail="CSV 存在校验问题，无法创建")
        models.create_bom_version(conn, board_name, pcb_version, bom_version, entries)
    board_id = models.create_board(conn, board_name, pcb_version, bom_version, board_uid)
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已创建 板 {board_uid}",
                            status_code=303)
```

- [ ] **Step 4: 模板**

`app/templates/board_new.html`（新建）：

```html
{% extends "base.html" %}
{% block title %}新建单板 — Reflow{% endblock %}
{% block crumbs %}<a href="/">首页</a> / 新建单板{% endblock %}
{% block content %}
<div class="page-head"><h1>新建单板</h1></div>
<p class="muted">选已有项直接复用；输入新值则隐式创建对应层级。BOM 版本是新的时才需要上传 CSV。</p>
<form class="panel form-stack" method="post" action="/board/new" enctype="multipart/form-data"
      hx-post="/board/new/preview" hx-target="#preview" hx-trigger="change"
      hx-encoding="multipart/form-data">
  <label>单板名称
    <input class="input" name="board_name" list="names" required autocomplete="off"></label>
  <datalist id="names">{% for n in names %}<option value="{{ n }}">{% endfor %}</datalist>
  <label>PCB 版本
    <input class="input" name="pcb_version" list="pcbs" required autocomplete="off"></label>
  <datalist id="pcbs">{% for p in pcbs %}<option value="{{ p }}">{% endfor %}</datalist>
  <label>BOM 版本
    <input class="input" name="bom_version" list="boms" required autocomplete="off"></label>
  <datalist id="boms">{% for b in boms %}<option value="{{ b }}">{% endfor %}</datalist>
  <label>初始 BOM CSV（仅新 BOM 版本需要）
    <input name="file" type="file" accept=".csv"></label>
  <label>单板 ID
    <input class="input" name="board_uid" required></label>
  <div id="preview"><div class="muted">填写上面三项后自动检查</div></div>
</form>
{% endblock %}
```

（注：表单上 `hx-trigger="change"` 覆盖了 htmx 对 form 默认的 submit 劫持——字段变化时发预览请求，原生提交仍走 `action="/board/new"`。）

`app/templates/_new_preview.html`（新建）：

```html
{% if status == 'fill' %}
<div class="muted">填写上面三项后自动检查</div>
{% elif status == 'exists' %}
<div class="flash flash-info">✓ 已有版本（{{ ref_count }} 个位号），新单板将直接使用其初始 BOM</div>
{% elif status == 'need_csv' %}
<div class="flash flash-warn">⚠ 新 BOM 版本，请选择初始 BOM CSV 文件</div>
{% elif status == 'bad_csv' %}
<div class="flash flash-error">✕ {{ message }}</div>
{% elif status == 'csv' %}
  {% if problems %}
  <div class="flash flash-error">✕ 发现 {{ problems|length }} 个校验问题，修正 CSV 后重新选择文件</div>
  <ul class="problem-list">
    {% for p in problems %}<li><code>{{ p.reference }}</code> {{ p.detail }}</li>{% endfor %}
  </ul>
  {% else %}
  <div class="flash flash-info">✓ 校验通过：{{ ref_count }} 个位号，提交时将创建新 BOM 版本</div>
  {% endif %}
{% endif %}
{% if status != 'fill' %}
<button class="btn btn-primary" {% if not ready %}disabled{% endif %}>
  {% if status == 'exists' %}创建单板{% else %}创建 BOM 版本并新建单板{% endif %}
</button>
{% endif %}
```

```bash
rm app/templates/new_bom_version.html app/templates/import_preview.html
```

- [ ] **Step 5: 全量回归**

Run: `pytest -q`
Expected: 全部通过（删 2 旧测试、加 8 新测试 → 66 passed）

- [ ] **Step 6: 手动验证**

`/board/new`：三个下拉可输可选；选已有组合出现蓝色提示且按钮可用；新组合无 CSV 出现黄色提示且按钮禁用；上传带重复位号的 CSV 出现红色问题列表且按钮禁用；正常 CSV 创建后跳到状态图并出现 toast。

- [ ] **Step 7: Commit**

```bash
git add -A app/routes/hierarchy.py app/templates tests/test_routes.py
git commit -m "feat: 统一新建单板流程——三级可输入下拉、按需 CSV 校验、隐式创建"
```

---

### Task 9: 审计日志页改版

**Files:**
- Modify: `app/models.py`（`list_board_log`）
- Modify: `app/routes/log.py`
- Rewrite: `app/templates/log.html`
- Test: `tests/test_models.py`、`tests/test_routes.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py` 追加：

```python
def test_list_board_log_filters_and_orders(tmp_path):
    from app.db import connect, init_db
    from app.csv_import import CsvEntry
    from app import models, audit
    conn = connect(str(tmp_path / "t.sqlite")); init_db(conn)
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(conn, "B", "v1", "bomA", "1")
    ws = models.workspace_node(conn, bid)
    audit.record_edit(conn, ws["id"], "R1", "10k", "22k", "modify", "direct")
    audit.record_edit(conn, ws["id"], "C1", None, "1uF", "add", "direct")
    rows = models.list_board_log(conn, bid)
    assert [r["reference"] for r in rows] == ["C1", "R1"]          # 倒序
    assert rows[0]["node_message"] is not None                      # join 到节点
    only_r1 = models.list_board_log(conn, bid, reference="R1")
    assert [r["reference"] for r in only_r1] == ["R1"]
    only_node = models.list_board_log(conn, bid, node_id=ws["id"])
    assert len(only_node) == 2
```

`tests/test_routes.py` 追加：

```python
def test_log_page_filter_by_reference(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    r = client.get(f"/board/{board_id}/log?reference=R9")
    assert "R1" not in r.text.split("</form>")[-1]   # 表格区不含 R1
    r2 = client.get(f"/board/{board_id}/log?reference=R1")
    assert "47k" in r2.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest -q -k "board_log or log_page_filter"`
Expected: 2 failed

- [ ] **Step 3: 实现**

`app/models.py` 追加：

```python
def list_board_log(conn, board_id, reference=None, node_id=None) -> list[sqlite3.Row]:
    """单板全链审计日志（带节点说明），可按位号模糊/节点过滤，最新在前。"""
    sql = ("SELECT l.*, n.message AS node_message, n.is_committed AS node_committed"
           " FROM edit_log l JOIN nodes n ON n.id = l.node_id WHERE n.board_id = ?")
    args: list = [board_id]
    if reference:
        sql += " AND l.reference LIKE ?"
        args.append(f"%{reference}%")
    if node_id:
        sql += " AND l.node_id = ?"
        args.append(node_id)
    sql += " ORDER BY l.id DESC"
    return conn.execute(sql, args).fetchall()
```

`app/routes/log.py`（完整替换）：

```python
from fastapi import APIRouter, Request, HTTPException
from app.main import templates, get_conn
from app import models

router = APIRouter()


@router.get("/board/{board_id}/log")
def board_log(request: Request, board_id: int,
              reference: str = "", node: int | None = None):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    rows = models.list_board_log(conn, board_id,
                                 reference=reference.strip() or None, node_id=node)
    return templates.TemplateResponse(
        request, "log.html",
        {"board": board, "board_id": board_id, "rows": rows,
         "nodes": models.list_nodes(conn, board_id),
         "reference": reference, "node": node})
```

`app/templates/log.html`（完整替换）：

```html
{% extends "base.html" %}
{% block title %}板 {{ board.board_uid }} · 审计日志 — Reflow{% endblock %}
{% block crumbs %}
<a href="/">首页</a> /
<a href="/board/{{ board_id }}">{{ board.board_name }} / {{ board.pcb_version }} / {{ board.bom_version }} / 板 {{ board.board_uid }}</a>
/ 审计日志
{% endblock %}
{% block ctxlinks %}<a href="/board/{{ board_id }}">状态图</a>{% endblock %}
{% block content %}
<div class="page-head"><h1>审计日志</h1></div>
<form class="toolbar" method="get" action="/board/{{ board_id }}/log">
  <input class="input" name="reference" value="{{ reference }}" placeholder="按位号筛选，如 R5">
  <select class="input" name="node" style="width:auto">
    <option value="">全部节点</option>
    {% for n in nodes %}
    <option value="{{ n.id }}" {% if node == n.id %}selected{% endif %}>
      {% if not n.is_committed %}工作区草稿{% elif n.parent_id is none %}初始状态{% else %}#{{ n.id }} {{ n.message or '(无说明)' }}{% endif %}
    </option>
    {% endfor %}
  </select>
  <button class="btn">筛选</button>
</form>
<table class="bom">
  <thead><tr><th>时间</th><th>节点</th><th>位号</th><th>变更</th><th>来源</th><th>备注</th></tr></thead>
  <tbody>
  {% for r in rows %}
  <tr>
    <td class="muted">{{ r.created_at }}</td>
    <td><a href="/board/{{ board_id }}/node/{{ r.node_id }}">
      {% if not r.node_committed %}工作区草稿{% else %}#{{ r.node_id }} {{ r.node_message or '(无说明)' }}{% endif %}</a></td>
    <td><code>{{ r.reference }}</code></td>
    <td>
      {% if r.old_part %}<span class="strike">{{ r.old_part }}</span> → {% endif %}
      {% if r.new_part %}<b>{{ r.new_part }}</b>{% else %}<b>不贴</b>{% endif %}
    </td>
    <td>
      {% if r.source == 'direct' %}<span class="badge badge-blue">直接修改</span>
      {% else %}<span class="badge badge-purple">上游传播</span>{% endif %}
    </td>
    <td class="muted">{{ r.note or '' }}</td>
  </tr>
  {% endfor %}
  {% if not rows %}<tr><td colspan="6" class="muted">没有匹配的记录</td></tr>{% endif %}
  </tbody>
</table>
{% endblock %}
```

- [ ] **Step 4: 全量回归 + Commit**

Run: `pytest -q` — Expected: 68 passed。确认既有 `test_log_page_lists_edits`（断言"直接"）仍通过。

```bash
git add app/models.py app/routes/log.py app/templates/log.html tests/test_models.py tests/test_routes.py
git commit -m "feat: 审计日志改版——节点可读可点、变更对比、来源徽章、筛选、倒序"
```

---

### Task 10: 收尾——全量验证与文档更新

**Files:**
- Modify: `CLAUDE.md`
- Test: 全量

- [ ] **Step 1: 全量测试**

Run: `pytest -q`
Expected: 68 passed, 0 failed

- [ ] **Step 2: 手动端到端验证**

```bash
REFLOW_DB=/tmp/reflow-e2e.sqlite uvicorn app.main:app --port 8001
```

走一遍完整旅程并核对 spec 13 项问题均已解决：
1. 空首页引导 → 新建单板（新版本 + CSV，含一次校验失败）→ 跳状态图 + toast
2. 工作区添加修改（表单清空、toast、徽章、旧值划线）→ 行尾撤销 → 面板撤销
3. 筛选框 / 三个标签 / 行尾「修改」「不贴」「恢复贴装」填表
4. commit → 时间线置顶草稿与摘要 → 进历史节点（警示条、收起表单）
5. 修正历史触发冲突弹窗 → 取消可关 → 再次触发 → 确认采用修正值
6. 日志页筛选与跳转；404 页面（手输错误 URL）
7. 同版本再建一块板（无 CSV 路径）

- [ ] **Step 3: 更新 CLAUDE.md**

「架构」表中路由行改为：

```markdown
| `app/routes/{hierarchy,board,log}.py` | 路由：hierarchy=首页+统一新建单板；board=状态图/节点页/编辑/撤销/冲突/commit；log=审计日志（筛选） |
| `app/validation.py` | ★位号编辑校验（纯逻辑） |
```

「约定 / 注意事项」追加：

```markdown
- 前端约定：HTMX 局部刷新 + Alpine.js 客户端小交互（CDN，无构建）。校验失败返回 200 + `HX-Retarget: #form-error`；编辑/撤销成功返回 `_node_update.html`（主体换 `#bom`，OOB 换 `#changes-panel`、清 `#form-error`）+ `HX-Trigger: {"showToast": …}`（json.dumps 保持 ASCII）；整页跳转用 `?flash=` 显示 toast。
- 撤销仅限工作区草稿（is_committed=0），实现为删 changeset 行，不记审计日志。
- 冲突确认是弹窗（`_conflict_modal.html`），取消 ≡ 全部「保留下游值」。
- 新建单板是唯一创建入口（`/board/new`），BOM 版本随之隐式创建；校验有问题禁止创建。
```

「运行与测试」中 `36 passed` 更新为实际数（69）。

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 更新路由职责与前端约定"
```

- [ ] **Step 5: 分支收尾**

使用 superpowers:finishing-a-development-branch 技能决定 merge / PR / 保留。

---

## Self-Review 记录

- **Spec 覆盖**：13 项问题 → 1/3/4(T5 表单+toast)、2(T1/T2/T5 行操作)、5(T5 变体)、6(T5 弹窗)、7(T5 工具栏)、8(T8 preview 禁用)、9(T8 flash+跳转)、10(T7)、11(T6)、12(T9)、13(T4)。决策摘要 6 条均有对应任务。✓
- **占位符**：无 TBD/TODO；所有代码步骤含完整代码。✓
- **类型一致性**：`_node_context` 的 ctx 键与各模板引用一致（rows/removed/changes/all_refs/total/mine_count/removed_count/board/board_id/node）；`validate_edit` 签名在 T1/T2 一致；`node_summaries` 返回 dict[int, list] 与 state_graph.html 的 `summaries[n.id]` 一致；测试计数核对：36 旧 +10(T1) +3(T2) +3(T3) +2(T4) +2(T5) +2(T6) +2(T7) −2+8(T8) +2(T9) = 68，各任务 Expected 数已按此修正；最终以 `pytest -q` 实际输出为准（T10 写入 CLAUDE.md 时用实际数）。✓
