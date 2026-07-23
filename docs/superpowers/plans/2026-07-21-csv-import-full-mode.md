# CSV 导入全量/差异模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「从 CSV 导入修改」支持二选一——差异模式（现状）或全量模式（CSV = 完整目标 BOM，系统自动求差、自动算不贴）。

**Architecture:** 三层照旧。纯逻辑新增 `plan_full_changes`（求差）、给 `parse_bom_csv` 加 `forbid_op` 参数、新增全量模板函数；路由 `import_preview` 按 `mode` 分支，`import_apply` 不改（planned change 已带显式 op）；前端加 `.seg` 模式单选 + Alpine 联动模板下载链接。

**Tech Stack:** Python 3 / FastAPI / Starlette 1.2.1 / Jinja2 + HTMX + Alpine.js / pytest。

## Global Constraints

- 运行测试前先 `. .venv/bin/activate`；基线约 491 passed（非 CLAUDE.md 里旧的 222）。`test_rename_ui` 偶发端口竞态，单独重跑即可。
- `TemplateResponse` 用新签名 `templates.TemplateResponse(request, "name.html", {ctx})`，context 里不要放 `"request"` 键。
- 所有 UI 文案、注释、docstring、错误消息一律**简体中文**。
- 前端只用现有 CSS 设计令牌与组件（复用 `.seg`、`.btn-link`、`.muted`、`.flash`）；Alpine 事件监听加 `.camel`；传 JS/hx-vals 的值 `|tojson` 且属性用单引号；`x-cloak` 已在 `style.css:219` 定义。改完两套主题都要实际查看（本计划测试以 TestClient HTML 断言为主，视觉自检在最后一步）。
- TDD：每个任务先写失败测试→跑挂→最小实现→跑过→提交。
- 全量模式的四个既定决策：**不认 OP 列（出现即报错）**、**空 Part 报错**、**空全量 CSV 正常允许（全部 remove）**、**apply 路径不改**。

---

### Task 1: `plan_full_changes` 求差纯逻辑

**Files:**
- Modify: `app/csv_import.py`（在文件末尾、`plan_changes` 之后新增函数；复用已有的 `PlannedChange`、`CsvProblem`、`validate_edit`）
- Test: `tests/test_csv_full_import.py`（新建）

**Interfaces:**
- Consumes: `PlannedChange(reference, op, part)`、`CsvProblem(kind, reference, detail)`、`validate_edit(full_bom, reference, op, part) -> str | None`（均已存在于 `app/csv_import.py` / `app/validation.py`）。
- Produces: `plan_full_changes(current_bom: dict[str, str], target_bom: dict[str, str]) -> tuple[list[PlannedChange], list[CsvProblem]]`，输出按位号排序。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_csv_full_import.py`：

```python
"""issue #129：全量模式导入——把完整目标 BOM 求差为修改清单（纯逻辑）。"""
from app.csv_import import PlannedChange, plan_full_changes

CUR = {"R1": "10k", "C1": "100nF"}


def test_diff_covers_add_modify_remove_sorted():
    # target：R1 改值、C1 消失、R9 新增
    changes, problems = plan_full_changes(CUR, {"R1": "47k", "R9": "1uF"})
    assert problems == []
    assert changes == [
        PlannedChange("C1", "remove", None),
        PlannedChange("R1", "modify", "47k"),
        PlannedChange("R9", "add", "1uF"),
    ]


def test_identical_target_yields_no_changes():
    changes, problems = plan_full_changes(CUR, {"R1": "10k", "C1": "100nF"})
    assert changes == [] and problems == []


def test_same_part_skipped_only_diff_kept():
    changes, _ = plan_full_changes(CUR, {"R1": "10k", "C1": "220nF"})
    assert changes == [PlannedChange("C1", "modify", "220nF")]


def test_empty_target_removes_everything():
    changes, problems = plan_full_changes(CUR, {})
    assert problems == []
    assert changes == [
        PlannedChange("C1", "remove", None),
        PlannedChange("R1", "remove", None),
    ]


def test_empty_current_adds_everything():
    changes, _ = plan_full_changes({}, {"R1": "10k"})
    assert changes == [PlannedChange("R1", "add", "10k")]


def test_does_not_mutate_inputs():
    cur, tgt = dict(CUR), {"R1": "47k"}
    plan_full_changes(cur, tgt)
    assert cur == CUR and tgt == {"R1": "47k"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `. .venv/bin/activate && pytest tests/test_csv_full_import.py -q`
Expected: FAIL —— `ImportError: cannot import name 'plan_full_changes'`

- [ ] **Step 3: 最小实现**

在 `app/csv_import.py` 末尾追加：

```python
def plan_full_changes(
    current_bom: dict[str, str], target_bom: dict[str, str]
) -> tuple[list[PlannedChange], list[CsvProblem]]:
    """把「完整目标 BOM」求差为修改清单（全量导入模式）。

    - ref ∈ target，∉ current → add
    - ref 两边都有、Part 不同 → modify
    - ref 两边都有、Part 相同 → 跳过（无变化）
    - ref ∈ current，∉ target → remove（不贴）
    输出按位号排序保证确定性；每条经 validate_edit 兜底。入参不被修改。
    """
    changes: list[PlannedChange] = []
    problems: list[CsvProblem] = []
    for ref in sorted(set(current_bom) | set(target_bom)):
        in_cur, in_tgt = ref in current_bom, ref in target_bom
        if in_tgt and not in_cur:
            op, part = "add", target_bom[ref]
        elif in_tgt and in_cur:
            if current_bom[ref] == target_bom[ref]:
                continue
            op, part = "modify", target_bom[ref]
        else:  # in_cur and not in_tgt
            op, part = "remove", None
        err = validate_edit(current_bom, ref, op, part)
        if err:
            problems.append(CsvProblem("invalid", ref, err))
            continue
        changes.append(PlannedChange(ref, op, part))
    return changes, problems
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_csv_full_import.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add app/csv_import.py tests/test_csv_full_import.py
git commit -m "feat: 全量导入求差纯逻辑 plan_full_changes（issue 129）"
```

---

### Task 2: `parse_bom_csv` 增 `forbid_op` 参数

**Files:**
- Modify: `app/csv_import.py`（`parse_bom_csv` 签名 + 表头处后加 OP 列检查）
- Test: `tests/test_csv_import.py`（追加用例）

**Interfaces:**
- Produces: `parse_bom_csv(text: str, forbid_op: bool = False) -> tuple[list[CsvEntry], list[CsvProblem]]`。`forbid_op=True` 且表头含 OP 列（大小写/首尾空格不敏感）时抛 `ValueError("全量模式的 CSV 不应包含 OP 列，请删除后重试")`。默认 `False`，现有调用方（`app/routes/hierarchy.py`、`tests/test_bom_export.py`）行为不变。

- [ ] **Step 1: 写失败测试**

在 `tests/test_csv_import.py` 末尾追加：

```python
def test_forbid_op_rejects_op_column():
    with pytest.raises(ValueError, match="不应包含 OP 列"):
        parse_bom_csv("Reference,Part,OP\nR1,10k,add\n", forbid_op=True)


def test_forbid_op_case_and_space_insensitive():
    with pytest.raises(ValueError, match="不应包含 OP 列"):
        parse_bom_csv("Reference , Part , op \nR1,10k,add\n", forbid_op=True)


def test_forbid_op_allows_csv_without_op():
    entries, problems = parse_bom_csv("Reference,Part\nR1,10k\n", forbid_op=True)
    assert problems == [] and [e.reference for e in entries] == ["R1"]


def test_default_still_ignores_op_column():
    # 回归保护：默认 forbid_op=False 时带 OP 列不报错（OP 被忽略）
    entries, _ = parse_bom_csv("Reference,Part,OP\nR1,10k,add\n")
    assert [(e.reference, e.part) for e in entries] == [("R1", "10k")]
```

确认文件顶部已 `import pytest`（已在，第 1 行区域）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_csv_import.py -q -k "forbid_op or default_still"`
Expected: FAIL —— `TypeError: parse_bom_csv() got an unexpected keyword argument 'forbid_op'`

- [ ] **Step 3: 最小实现**

在 `app/csv_import.py` 的 `parse_bom_csv` 里改签名并加检查。当前（约 31、40-42 行）：

```python
def parse_bom_csv(text: str) -> tuple[list[CsvEntry], list[CsvProblem]]:
```
改为：
```python
def parse_bom_csv(text: str, forbid_op: bool = False) -> tuple[list[CsvEntry], list[CsvProblem]]:
```

在 `fieldmap = {(name or "").strip(): name for name in (reader.fieldnames or [])}` 这一行之后、`ref_col = fieldmap.get("Reference")` 之前插入：

```python
    if forbid_op and any(k.lower() == "op" for k in fieldmap):
        raise ValueError("全量模式的 CSV 不应包含 OP 列，请删除后重试")
```

docstring 补一句（可选）：`forbid_op=True 时表头含 OP 列直接报错（全量导入模式用）。`

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_csv_import.py -q`
Expected: PASS（原有用例 + 4 条新用例全过）

- [ ] **Step 5: 提交**

```bash
git add app/csv_import.py tests/test_csv_import.py
git commit -m "feat: parse_bom_csv 支持 forbid_op（全量模式拒绝 OP 列，issue 129）"
```

---

### Task 3: 路由 `import_preview` 全量分支

**Files:**
- Modify: `app/routes/board.py`（第 12 行 import 补齐；`import_preview` 函数，约 355-388 行）
- Test: `tests/test_import_routes.py`（追加全量用例）

**Interfaces:**
- Consumes: `plan_full_changes`（Task 1）、`parse_bom_csv(..., forbid_op=True)`（Task 2）、已有的 `fold_bom`、`models.get_chain`、`_read_upload`、`_import_draft`。
- Produces: `POST /board/{board_id}/node/{node_id}/import/preview` 新增可选表单字段 `mode`（默认 `"diff"`）；`mode="full"` 时按完整目标 BOM 求差。ctx 追加 `mode`、`unchanged`。渲染的 `_import_preview.html` 会在 Task 5 用到 `mode`/`unchanged`（本任务先只保证不报错，模板暂不显示 unchanged）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_import_routes.py` 里，`_preview` 辅助函数之后追加一个全量预览辅助与用例：

```python
def _preview_full(client, board_id, node_id, csv_bytes):
    return client.post(f"/board/{board_id}/node/{node_id}/import/preview",
                       data={"mode": "full"},
                       files={"file": ("full.csv", csv_bytes, "text/csv")})


# ── 全量模式（issue #129）──────────────────────────────────────

def test_full_preview_diffs_against_current_bom(client):
    # 初始 BOM：R1=10k、C1=100nF。全量目标：R1=47k、R9=1uF（C1 消失）
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview_full(client, board_id, ws, b"Reference,Part\nR1,47k\nR9,1uF\n")
    assert r.status_code == 200
    assert "新增 1" in r.text and "修改 1" in r.text and "不贴 1" in r.text
    assert _changeset(ws) == {}  # 预览不写库


def test_full_preview_rejects_op_column(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview_full(client, board_id, ws, b"Reference,Part,OP\nR1,47k,modify\n")
    assert r.status_code == 200
    assert "不应包含 OP 列" in r.text
    assert "hx-vals" not in r.text


def test_full_preview_empty_csv_removes_all(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview_full(client, board_id, ws, b"Reference,Part\n")
    assert r.status_code == 200
    assert "不贴 2" in r.text and "hx-vals" in r.text  # 全部不贴、可应用


def test_full_apply_writes_diffed_changes(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview_full(client, board_id, ws, b"Reference,Part\nR1,47k\nR9,1uF\n")
    r2 = client.post(f"/board/{board_id}/node/{ws}/import",
                     data={"changes": _changes_json(r.text)})
    assert r2.status_code == 204
    assert _changeset(ws) == {"R1": ("modify", "47k"),
                              "R9": ("add", "1uF"),
                              "C1": ("remove", None)}


def test_full_preview_empty_part_is_a_problem(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview_full(client, board_id, ws, b"Reference,Part\nR1,\n")
    assert r.status_code == 200
    assert "Part 为空" in r.text and "hx-vals" not in r.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_import_routes.py -q -k full`
Expected: FAIL —— 全量 CSV 被当差异解析：`test_full_preview_rejects_op_column` 不会出现「不应包含 OP 列」，`test_full_preview_empty_csv_removes_all` 不会出现「不贴 2」等。

- [ ] **Step 3: 最小实现**

3a. `app/routes/board.py` 第 12 行 import 补上两个名字：

```python
from app.csv_import import (
    ChangeEntry, parse_change_csv, plan_changes, change_csv_template,
    parse_bom_csv, plan_full_changes,
)
```

3b. 把 `import_preview`（约 355-388 行）整体替换为：

```python
@router.post("/board/{board_id}/node/{node_id}/import/preview")
async def import_preview(request: Request, board_id: int, node_id: int,
                         file: UploadFile | None = File(None),
                         mode: str = Form("diff")):
    """解析上传的 CSV，渲染预览；不写库。有任一问题行则不给应用按钮。

    mode="diff"：CSV 是修改清单（现有语义）。
    mode="full"：CSV 是完整目标 BOM，与当前折叠 BOM 求差得出修改。
    """
    conn = get_conn()
    if _import_draft(conn, board_id, node_id) is None:
        return PlainTextResponse("只有工作区草稿支持导入修改", status_code=400)

    ctx = {"board_id": board_id, "node_id": node_id, "message": "",
           "changes": [], "problems": [], "ready": False,
           "mode": mode, "unchanged": 0,
           "counts": {"add": 0, "modify": 0, "remove": 0}, "changes_json": "[]"}

    text, err = await _read_upload(file)
    if err:
        ctx["message"] = err
        return templates.TemplateResponse(request, "_import_preview.html", ctx)

    initial, chain = models.get_chain(conn, node_id)
    current = fold_bom(initial, chain)
    try:
        if mode == "full":
            entries, problems = parse_bom_csv(text, forbid_op=True)
            target = {e.reference: e.part for e in entries}
            changes, invalid = plan_full_changes(current, target)
            ctx["unchanged"] = sum(1 for ref, part in current.items()
                                   if target.get(ref) == part)
        else:
            entries, problems = parse_change_csv(text)
            changes, invalid = plan_changes(current, entries)
    except ValueError as e:
        ctx["message"] = str(e)
        return templates.TemplateResponse(request, "_import_preview.html", ctx)

    problems = problems + invalid
    dicts = [c._asdict() for c in changes]
    ctx.update(
        changes=dicts, problems=problems,
        ready=bool(changes) and not problems,
        counts={op: sum(1 for c in changes if c.op == op)
                for op in ("add", "modify", "remove")},
        changes_json=json.dumps(dicts, ensure_ascii=False),
    )
    return templates.TemplateResponse(request, "_import_preview.html", ctx)
```

说明：`import_apply` 不改——预览产出的 planned change 已带显式 op，应用时 `plan_changes` 照常重校验，与模式无关（`test_full_apply_writes_diffed_changes` 覆盖）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_import_routes.py -q`
Expected: PASS（原有用例 + 5 条全量用例全过）

- [ ] **Step 5: 提交**

```bash
git add app/routes/board.py tests/test_import_routes.py
git commit -m "feat: 导入预览支持 mode=full 全量求差（issue 129）"
```

---

### Task 4: 模式化下载模板

**Files:**
- Modify: `app/csv_import.py`（新增 `full_bom_csv_template`）
- Modify: `app/routes/board.py`（`import_csv_template` 加 `mode` 查询参数；第 12 行 import 补 `full_bom_csv_template`）
- Test: `tests/test_import_routes.py`（追加用例）

**Interfaces:**
- Produces: `full_bom_csv_template() -> str`（返回 `"Reference,Part\n"`）；`GET /board/{board_id}/node/{node_id}/import/template?mode=full` 返回全量模板，`mode` 缺省或 `diff` 仍返回 `"Reference,Part,OP\n"`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_import_routes.py` 的「下载模板」区块追加：

```python
def test_full_template_has_no_op_column(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.get(f"/board/{board_id}/node/{ws}/import/template?mode=full")
    assert r.status_code == 200
    assert r.text == "Reference,Part\n"


def test_diff_template_unchanged_by_default(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.get(f"/board/{board_id}/node/{ws}/import/template")
    assert r.text == "Reference,Part,OP\n"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_import_routes.py -q -k template`
Expected: FAIL —— `test_full_template_has_no_op_column` 拿到的仍是三列表头 `Reference,Part,OP\n`。

- [ ] **Step 3: 最小实现**

3a. `app/csv_import.py` 在 `change_csv_template` 之后新增：

```python
def full_bom_csv_template() -> str:
    """全量模式导入模板：仅 Reference/Part 两列表头（全量不认 OP 列）。"""
    return "Reference,Part\n"
```

3b. `app/routes/board.py` 第 12 行 import 追加 `full_bom_csv_template`（并入 Task 3 已改的多行 import）：

```python
from app.csv_import import (
    ChangeEntry, parse_change_csv, plan_changes, change_csv_template,
    parse_bom_csv, plan_full_changes, full_bom_csv_template,
)
```

3c. 把 `import_csv_template`（约 337-352 行）替换为：

```python
@router.get("/board/{board_id}/node/{node_id}/import/template")
def import_csv_template(board_id: int, node_id: int, mode: str = "diff"):
    """下载导入模板。mode=full 仅 Reference/Part 两列；否则含 OP 列（差异模式）。

    模板本身与节点状态无关；入口只在工作区草稿面板展示。
    """
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    body = (full_bom_csv_template() if mode == "full"
            else change_csv_template()).encode("utf-8")
    fname = "full_bom_template.csv" if mode == "full" else "change_template.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(fname)},
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_import_routes.py -q -k template`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/csv_import.py app/routes/board.py tests/test_import_routes.py
git commit -m "feat: 导入模板按 mode 切换（全量仅 Reference/Part，issue 129）"
```

---

### Task 5: 前端 UI —— 模式单选 + 联动模板链接 + 无变化计数

**Files:**
- Modify: `app/templates/node_detail.html`（导入面板 `<details>`，约 76-89 行）
- Modify: `app/templates/_import_preview.html`（信息行加全量「无变化」计数）
- Test: `tests/test_import_routes.py`（追加 UI 断言）

**Interfaces:**
- Consumes: 路由 `mode` 表单字段（Task 3）、模板 `?mode=`（Task 4）、ctx 的 `mode`/`unchanged`（Task 3 已传）。
- Produces: 面板含 `.seg` 模式单选（差异默认选中 / 全量），下载链接 `:href` 按 `mode` 拼 `?mode=`；全量预览显示「其余 N 个位号无变化」。

- [ ] **Step 1: 写失败测试**

在 `tests/test_import_routes.py` 的「页面入口」区块追加：

```python
def test_import_panel_has_mode_selector(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    html = client.get(f"/board/{board_id}/node/{ws}").text
    # .seg 模式单选：差异默认选中、全量存在
    assert 'name="mode" value="diff"' in html
    assert 'name="mode" value="full"' in html
    # 下载链接按 mode 联动（Alpine :href 拼 ?mode=）
    assert "import/template?mode=" in html


def test_full_preview_shows_unchanged_count(client):
    # 初始 R1=10k、C1=100nF；全量把 C1 改成 220nF、R1 原值不变 → 1 个位号无变化
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview_full(client, board_id, ws, b"Reference,Part\nR1,10k\nC1,220nF\n")
    assert r.status_code == 200
    assert "修改 1" in r.text
    assert "其余 1 个位号无变化" in r.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_import_routes.py -q -k "mode_selector or unchanged"`
Expected: FAIL —— 面板无 `name="mode"`；预览无「无变化」文案。

- [ ] **Step 3: 最小实现**

3a. `app/templates/node_detail.html` 把导入面板（76-89 行）替换为：

```html
    <details class="panel" x-data="{ mode: 'diff' }">
      <summary>从 CSV 导入修改…</summary>
      <form class="edit-form" hx-post="/board/{{ board_id }}/node/{{ node.id }}/import/preview"
            hx-target="#import-preview" hx-swap="innerHTML"
            hx-encoding="multipart/form-data" hx-trigger="change">
        <div class="seg">
          <label :class="{on: mode==='diff'}"><input type="radio" name="mode" value="diff" x-model="mode">差异</label>
          <label :class="{on: mode==='full'}"><input type="radio" name="mode" value="full" x-model="mode">全量</label>
        </div>
        <label>CSV 文件
          <input name="file" type="file" accept=".csv"></label>
        <p class="muted" x-show="mode==='diff'" x-cloak>差异模式：CSV 只列改动，未列出的位号保持不变。表头需含 Reference 与 Part，可选 OP 列（add / modify / remove），不写 OP 时按位号是否已存在自动判为新增或修改。</p>
        <p class="muted" x-show="mode==='full'" x-cloak>全量模式：CSV 是这块板<b>完整的目标 BOM</b>，系统自动求差、自动算不贴，<b>不需要 OP 列</b>。表头只需 Reference 与 Part。</p>
        <p class="muted"><a class="btn-link"
             :href="'/board/{{ board_id }}/node/{{ node.id }}/import/template?mode=' + mode">下载模板</a></p>
      </form>
      <div id="import-preview"></div>
    </details>
```

注意：差异的 `<p>` 也带 `x-cloak`——初始 `mode='diff'` 时它靠 `x-show` 显示（Alpine 初始化后立即可见），`x-cloak` 只挡住初始化前的瞬时闪现；两段文案都加，避免全量段闪现。`|tojson` 在此不需要（拼接的是同源相对 URL 字符串，非注入 JS/hx-vals 数据）。

3b. `app/templates/_import_preview.html`，把信息行（12-15 行附近）：

```html
  {% if changes %}
  <div class="flash flash-info">
    共 {{ changes|length }} 条修改：新增 {{ counts.add }} · 修改 {{ counts.modify }} · 不贴 {{ counts.remove }}
  </div>
```
改为：
```html
  {% if changes %}
  <div class="flash flash-info">
    共 {{ changes|length }} 条修改：新增 {{ counts.add }} · 修改 {{ counts.modify }} · 不贴 {{ counts.remove }}
    {% if mode == 'full' and unchanged %} · 其余 {{ unchanged }} 个位号无变化{% endif %}
  </div>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_import_routes.py -q`
Expected: PASS

- [ ] **Step 5: 视觉自检（按前端风格指南）**

启动 `uvicorn app.main:app --reload`，进任一板的工作区草稿页：
- 切「全量/全量」两个分段按钮，`.seg` 高亮跟随、帮助文案切换、无初始闪现（`x-cloak` 生效）。
- 「下载模板」链接在差异下载三列、全量下载两列。
- 全量上传一份完整 BOM，预览计数与「无变化」提示正确。
- 浅色/深色两套主题都看一遍（无新增颜色，应无需改令牌）。

- [ ] **Step 6: 提交**

```bash
git add app/templates/node_detail.html app/templates/_import_preview.html tests/test_import_routes.py
git commit -m "feat: 导入面板全量/差异模式单选 + 联动模板 + 无变化计数（issue 129）"
```

---

### Task 6: 全量回归 + 文档收尾

**Files:**
- Modify: `docs/前端风格指南.md`（如「组件清单」列了导入面板/`.seg` 用例，补一句全量模式说明；无对应条目则跳过）

- [ ] **Step 1: 跑全量测试**

Run: `. .venv/bin/activate && pytest -q`
Expected: 全绿（基线 491 + 本次新增约 17 条）。若 `test_rename_ui` 因端口竞态偶发失败，单独 `pytest tests/test_rename_ui.py -q` 重跑确认。

- [ ] **Step 2: 文档（可选）**

若 `docs/前端风格指南.md` 有「导入面板」或 `.seg` 的组件条目，补一行：导入面板差异/全量二选一用 `.seg` + Alpine `mode`。无则跳过本步。

- [ ] **Step 3: 提交（若有文档改动）**

```bash
git add docs/前端风格指南.md
git commit -m "docs: 风格指南补充导入面板全量/差异模式（issue 129）"
```

---

## Self-Review

**Spec coverage：**
- 全量求差语义（add/modify/remove/skip）→ Task 1 ✅
- 不认 OP 列、出现即报错 → Task 2（`forbid_op`）+ Task 3（`forbid_op=True`）✅
- 空 Part 报错 → 复用 `parse_bom_csv` 的 `empty_part`，Task 3 `test_full_preview_empty_part_is_a_problem` 覆盖 ✅
- 空全量正常允许（全 remove）→ Task 3 `test_full_preview_empty_csv_removes_all` ✅
- apply 不改 → Task 3 说明 + `test_full_apply_writes_diffed_changes` ✅
- 模式单选 + 联动模板 + 无变化计数 → Task 4 + Task 5 ✅
- 差异模式回归不变 → 原有 `test_import_routes.py` 全套 + `test_default_still_ignores_op_column` ✅

**Placeholder scan：** 无 TBD/TODO；每个代码步给出完整代码与预期输出。

**Type consistency：** `plan_full_changes(current_bom, target_bom)`、`parse_bom_csv(text, forbid_op=False)`、`full_bom_csv_template()`、ctx 键 `mode`/`unchanged` 在定义（Task 1/2/4/3）与消费（Task 3/4/5）处一致。`PlannedChange`/`CsvProblem`/`validate_edit` 沿用既有定义。
