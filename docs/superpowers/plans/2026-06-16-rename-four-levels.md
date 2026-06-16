# 四级定位名称重命名功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户能在首页重命名四级定位（单板名称 / PCB版本 / BOM版本 / 单板ID），名称改动级联更新去规范化的两张表，冲突时拒绝。

**Architecture:** 三层照现有模式扩展——纯逻辑 `validate_new_name`（空校验）+ 数据层 4 个 `rename_*`（级联 UPDATE + 冲突检测）+ 路由层 4 个 POST（成功 `HX-Redirect` 回首页、失败弹 toast）+ 前端把每个层级的删除图标换成悬停浮现的 ⋯ 菜单（含「重命名 / 删除」），点重命名后名字就地变输入框。

**Tech Stack:** FastAPI / Starlette 1.2.1、SQLite、Jinja2 + HTMX + Alpine.js、pytest（含 Playwright UI 测试）。

**并行分发映射（4 个任务按文件归属互不重叠）：**
- 第 1 波并行：**Task 1**（`validation.py`）+ **Task 2**（`models.py`）——无依赖、各写各的测试文件。
- 第 2 波并行（待第 1 波合并）：**Task 3**（`routes/hierarchy.py`，导入 T1/T2）+ **Task 4**（前端，用 T3 的端点 URL，URL 已在本计划钉死）。

---

## File Structure

| 文件 | 角色 | 任务 |
|---|---|---|
| `app/validation.py` | 新增纯函数 `validate_new_name` | T1 |
| `tests/test_validation.py` | 追加空校验测试 | T1 |
| `app/models.py` | 新增 4 个 `rename_*` 函数（紧挨现有 `delete_*`） | T2 |
| `tests/test_rename_models.py` | 新建：数据层重命名测试 | T2 |
| `app/routes/hierarchy.py` | 新增 4 个 rename 路由 + `json` 导入 + `_toast_error` 助手 | T3 |
| `tests/test_rename_routes.py` | 新建：路由层重命名测试（TestClient） | T3 |
| `app/templates/home.html` | 三处删除图标改为 ⋯ 菜单 + 内联重命名表单 | T4 |
| `app/static/style.css` | 新增 `.menu*` / `.rename-input` 样式 + 夜间变量 | T4 |
| `tests/test_rename_ui.py` | 新建：菜单 + 内联编辑的 Playwright 测试 | T4 |
| `tests/test_delete_ui.py` | 更新：删除按钮现位于 ⋯ 菜单内 | T4 |

---

## Task 1: 纯逻辑 `validate_new_name`

**Files:**
- Modify: `app/validation.py`（在文件末尾追加函数）
- Test: `tests/test_validation.py`（追加用例）

- [ ] **Step 1: Write the failing tests**

在 `tests/test_validation.py` 末尾追加：

```python
from app.validation import validate_new_name


def test_new_name_empty_rejected():
    assert "不能为空" in validate_new_name("")


def test_new_name_whitespace_rejected():
    assert "不能为空" in validate_new_name("   ")


def test_new_name_none_rejected():
    assert "不能为空" in validate_new_name(None)


def test_valid_new_name_passes():
    assert validate_new_name("v2") is None


def test_new_name_not_stripped_in_return():
    # 契约：只判断 trim 后非空，不负责裁剪；裁剪由调用方（路由）负责
    assert validate_new_name("  v2  ") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_validation.py -k new_name -v`
Expected: FAIL — `ImportError: cannot import name 'validate_new_name'`

- [ ] **Step 3: Write minimal implementation**

在 `app/validation.py` 末尾追加：

```python
def validate_new_name(new: str | None) -> str | None:
    """重命名时的新名校验：trim 后非空。返回中文错误消息或 None。

    只判断非空，不裁剪（裁剪由调用方负责，与 validate_edit 的 part 契约一致）。
    """
    if not (new or "").strip():
        return "名称不能为空"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_validation.py -k new_name -v`
Expected: PASS（5 个用例）

- [ ] **Step 5: Commit**

```bash
git add app/validation.py tests/test_validation.py
git commit -m "feat: 重命名新名空校验纯函数 validate_new_name (issue #20)"
```

---

## Task 2: 数据层 4 个 `rename_*` 函数

**Files:**
- Modify: `app/models.py`（在 `delete_board_name` 之后追加 4 个函数）
- Test: `tests/test_rename_models.py`（新建）

设计要点：前三级在 `boards_hierarchy` + `initial_bom` **两张表**同步 `UPDATE`；冲突命中即抛 `ValueError`（中文消息）且不改任何行；新名 == 旧名时 no-op。`board_uid` 只在 `boards_hierarchy`。

- [ ] **Step 1: Write the failing tests**

新建 `tests/test_rename_models.py`：

```python
import pytest
from app.db import connect, init_db
from app import models
from app.csv_import import CsvEntry


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def _seed(conn, name="MB", pcb="v1", bom="bomA", uid="SN1"):
    models.create_bom_version(conn, name, pcb, bom, [CsvEntry("R1", "10k")])
    return models.create_board(conn, name, pcb, bom, uid)


# ── rename_board_name ───────────────────────────────────────────────

def test_rename_board_name_updates_both_tables(conn):
    _seed(conn, "Old")
    models.rename_board_name(conn, "Old", "New")
    assert models.list_boards(conn, "New", "v1", "bomA")
    assert models.get_initial_bom(conn, "New", "v1", "bomA") == {"R1": "10k"}
    assert not models.list_boards(conn, "Old", "v1", "bomA")


def test_rename_board_name_cascades_across_versions(conn):
    _seed(conn, "Old", "v1", "bomA", "SN1")
    _seed(conn, "Old", "v2", "bomB", "SN2")
    models.rename_board_name(conn, "Old", "New")
    assert models.list_boards(conn, "New", "v1", "bomA")
    assert models.list_boards(conn, "New", "v2", "bomB")


def test_rename_board_name_conflict_rejected(conn):
    _seed(conn, "A")
    _seed(conn, "B")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_board_name(conn, "A", "B")
    # 未改任何行
    assert models.list_boards(conn, "A", "v1", "bomA")


def test_rename_board_name_noop_when_unchanged(conn):
    _seed(conn, "Same")
    models.rename_board_name(conn, "Same", "Same")  # 不抛
    assert models.list_boards(conn, "Same", "v1", "bomA")


# ── rename_pcb_version ──────────────────────────────────────────────

def test_rename_pcb_version_cascades_under_board(conn):
    _seed(conn, "MB", "p1", "bomA", "SN1")
    _seed(conn, "MB", "p1", "bomB", "SN2")
    models.rename_pcb_version(conn, "MB", "p1", "p2")
    assert models.list_boards(conn, "MB", "p2", "bomA")
    assert models.list_boards(conn, "MB", "p2", "bomB")
    assert models.get_initial_bom(conn, "MB", "p2", "bomA") == {"R1": "10k"}


def test_rename_pcb_version_conflict_rejected(conn):
    _seed(conn, "MB", "p1", "bomA", "SN1")
    _seed(conn, "MB", "p2", "bomA", "SN2")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_pcb_version(conn, "MB", "p1", "p2")


# ── rename_bom_version ──────────────────────────────────────────────

def test_rename_bom_version_updates_triple(conn):
    _seed(conn, "MB", "v1", "b1", "SN1")
    models.rename_bom_version(conn, "MB", "v1", "b1", "b2")
    assert models.list_boards(conn, "MB", "v1", "b2")
    assert models.get_initial_bom(conn, "MB", "v1", "b2") == {"R1": "10k"}
    assert not models.get_initial_bom(conn, "MB", "v1", "b1")


def test_rename_bom_version_conflict_rejected(conn):
    _seed(conn, "MB", "v1", "b1", "SN1")
    _seed(conn, "MB", "v1", "b2", "SN2")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_bom_version(conn, "MB", "v1", "b1", "b2")


# ── rename_board_uid ────────────────────────────────────────────────

def test_rename_board_uid_updates_row(conn):
    bid = _seed(conn, "MB", "v1", "bomA", "SN1")
    models.rename_board_uid(conn, bid, "SN9")
    assert models.get_board(conn, bid)["board_uid"] == "SN9"


def test_rename_board_uid_conflict_within_version_rejected(conn):
    bid = _seed(conn, "MB", "v1", "bomA", "SN1")
    _seed(conn, "MB", "v1", "bomA", "SN2")
    with pytest.raises(ValueError, match="已存在"):
        models.rename_board_uid(conn, bid, "SN2")


def test_rename_board_uid_same_uid_other_version_ok(conn):
    bid = _seed(conn, "MB", "v1", "bomA", "SN1")
    _seed(conn, "MB", "v1", "bomB", "SN9")  # 不同 BOM 版本
    models.rename_board_uid(conn, bid, "SN9")  # 不冲突
    assert models.get_board(conn, bid)["board_uid"] == "SN9"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rename_models.py -v`
Expected: FAIL — `AttributeError: module 'app.models' has no attribute 'rename_board_name'`

- [ ] **Step 3: Write minimal implementation**

在 `app/models.py` 中 `delete_board_name` 函数之后追加：

```python
def rename_board_name(conn, old, new) -> None:
    """重命名单板名称：级联更新两表所有匹配行。同名冲突抛 ValueError。"""
    if new == old:
        return
    exists = (
        conn.execute("SELECT 1 FROM boards_hierarchy WHERE board_name=? LIMIT 1",
                     (new,)).fetchone()
        or conn.execute("SELECT 1 FROM initial_bom WHERE board_name=? LIMIT 1",
                        (new,)).fetchone()
    )
    if exists:
        raise ValueError(f"单板名称「{new}」已存在")
    conn.execute("UPDATE boards_hierarchy SET board_name=? WHERE board_name=?", (new, old))
    conn.execute("UPDATE initial_bom SET board_name=? WHERE board_name=?", (new, old))
    conn.commit()


def rename_pcb_version(conn, board_name, old, new) -> None:
    """重命名 PCB 版本：级联该 board_name 下该 PCB 的所有行。冲突抛 ValueError。"""
    if new == old:
        return
    exists = (
        conn.execute("SELECT 1 FROM boards_hierarchy WHERE board_name=? AND pcb_version=? LIMIT 1",
                     (board_name, new)).fetchone()
        or conn.execute("SELECT 1 FROM initial_bom WHERE board_name=? AND pcb_version=? LIMIT 1",
                        (board_name, new)).fetchone()
    )
    if exists:
        raise ValueError(f"PCB 版本「{new}」已存在")
    conn.execute("UPDATE boards_hierarchy SET pcb_version=? WHERE board_name=? AND pcb_version=?",
                 (new, board_name, old))
    conn.execute("UPDATE initial_bom SET pcb_version=? WHERE board_name=? AND pcb_version=?",
                 (new, board_name, old))
    conn.commit()


def rename_bom_version(conn, board_name, pcb_version, old, new) -> None:
    """重命名 BOM 版本：只动 (board_name, pcb_version, old) 三元组。冲突抛 ValueError。"""
    if new == old:
        return
    exists = (
        conn.execute("SELECT 1 FROM boards_hierarchy"
                     " WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
                     (board_name, pcb_version, new)).fetchone()
        or conn.execute("SELECT 1 FROM initial_bom"
                        " WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
                        (board_name, pcb_version, new)).fetchone()
    )
    if exists:
        raise ValueError(f"BOM 版本「{new}」已存在")
    conn.execute("UPDATE boards_hierarchy SET bom_version=?"
                 " WHERE board_name=? AND pcb_version=? AND bom_version=?",
                 (new, board_name, pcb_version, old))
    conn.execute("UPDATE initial_bom SET bom_version=?"
                 " WHERE board_name=? AND pcb_version=? AND bom_version=?",
                 (new, board_name, pcb_version, old))
    conn.commit()


def rename_board_uid(conn, board_id, new) -> None:
    """重命名单板 ID：只动该行。同 BOM 版本内重名抛 ValueError。"""
    row = get_board(conn, board_id)
    if row is None:
        raise ValueError("单板不存在")
    if new == row["board_uid"]:
        return
    if board_uid_exists(conn, row["board_name"], row["pcb_version"], row["bom_version"], new):
        raise ValueError(f"单板 ID「{new}」在该 BOM 版本下已存在")
    conn.execute("UPDATE boards_hierarchy SET board_uid=? WHERE id=?", (new, board_id))
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rename_models.py -v`
Expected: PASS（11 个用例）

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_rename_models.py
git commit -m "feat: 四级定位重命名数据层函数 rename_* (issue #20)"
```

---

## Task 3: 路由层 4 个 rename 端点

**Files:**
- Modify: `app/routes/hierarchy.py`（顶部加 `import json`、`from app.validation import validate_new_name`；新增 `_toast_error` 助手与 4 个路由）
- Test: `tests/test_rename_routes.py`（新建）

> 依赖 Task 1（`validate_new_name`）、Task 2（`rename_*`）。端点 URL 与表单字段名见下，Task 4 据此拼接 `hx-post`。

成功 → `HX-Redirect` 回 `/`（复用现有 `_hx_redirect`）。失败（空名/冲突）→ 返回 200 + `HX-Trigger: showToast` 中文错误，不重定向（输入框保留待改）。

- [ ] **Step 1: Write the failing tests**

新建 `tests/test_rename_routes.py`：

```python
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup(client, name="B", pcb="v1", bom="bomA", uid="SN1"):
    r = client.post("/board/new",
                    data={"board_name": name, "pcb_version": pcb,
                          "bom_version": bom, "board_uid": uid},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]  # board_id


def test_rename_board_group_success_redirects(client):
    _setup(client, "Old")
    r = client.post("/board-group/rename",
                    data={"board_name": "Old", "new_name": "New"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "New" in client.get("/").text
    assert "Old" not in client.get("/").text


def test_rename_board_group_conflict_returns_toast(client):
    _setup(client, "A")
    _setup(client, "B")
    r = client.post("/board-group/rename",
                    data={"board_name": "A", "new_name": "B"})
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers
    trig = json.loads(r.headers["HX-Trigger"])
    assert "已存在" in trig["showToast"]


def test_rename_empty_name_returns_toast(client):
    _setup(client, "A")
    r = client.post("/board-group/rename",
                    data={"board_name": "A", "new_name": "   "})
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "不能为空" in trig["showToast"]


def test_rename_pcb_version_success(client):
    _setup(client, "MB", "p1", "bomA", "SN1")
    r = client.post("/pcb-version/rename",
                    data={"board_name": "MB", "pcb_version": "p1", "new_name": "p2"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "PCB p2" in client.get("/").text


def test_rename_bom_version_success(client):
    _setup(client, "MB", "v1", "b1", "SN1")
    r = client.post("/bom-version/rename",
                    data={"board_name": "MB", "pcb_version": "v1",
                          "bom_version": "b1", "new_name": "b2"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "b2" in client.get("/").text


def test_rename_board_uid_success(client):
    bid = _setup(client, "MB", "v1", "bomA", "SN1")
    r = client.post(f"/board/{bid}/rename", data={"new_name": "SN9"})
    assert r.headers.get("HX-Redirect") == "/"
    assert "SN9" in client.get("/").text


def test_rename_board_uid_conflict_returns_toast(client):
    bid = _setup(client, "MB", "v1", "bomA", "SN1")
    _setup(client, "MB", "v1", "bomA", "SN2")
    r = client.post(f"/board/{bid}/rename", data={"new_name": "SN2"})
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "已存在" in trig["showToast"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rename_routes.py -v`
Expected: FAIL — 404（路由未定义）

- [ ] **Step 3: Write minimal implementation**

在 `app/routes/hierarchy.py` 顶部 import 区追加（与现有 import 并列）：

```python
import json
from app.validation import validate_new_name
```

在文件末尾（`board_group_delete` 等之后）追加助手与 4 个路由：

```python
def _toast_error(msg: str) -> Response:
    """重命名失败：200 + 弹 toast，不重定向，保留输入框。"""
    return Response(status_code=200,
                    headers={"HX-Trigger": json.dumps({"showToast": msg})})


@router.post("/board-group/rename")
def board_group_rename(board_name: str = Form(...), new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
    try:
        models.rename_board_name(conn, board_name, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/pcb-version/rename")
def pcb_version_rename(board_name: str = Form(...), pcb_version: str = Form(...),
                       new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
    try:
        models.rename_pcb_version(conn, board_name, pcb_version, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/bom-version/rename")
def bom_version_rename(board_name: str = Form(...), pcb_version: str = Form(...),
                       bom_version: str = Form(...), new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
    try:
        models.rename_bom_version(conn, board_name, pcb_version, bom_version, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/board/{board_id}/rename")
def board_uid_rename(board_id: int, new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
    try:
        models.rename_board_uid(conn, board_id, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_rename_routes.py -v`
Expected: PASS（7 个用例）

- [ ] **Step 5: Commit**

```bash
git add app/routes/hierarchy.py tests/test_rename_routes.py
git commit -m "feat: 四级定位重命名路由端点 (issue #20)"
```

---

## Task 4: 前端 ⋯ 菜单 + 内联重命名

**Files:**
- Modify: `app/templates/home.html`（三处删除图标改为 ⋯ 菜单 + 内联表单）
- Modify: `app/static/style.css`（新增 `.menu*` / `.rename-input` 样式 + 夜间变量）
- Create: `tests/test_rename_ui.py`（菜单/内联编辑 Playwright 测试）
- Modify: `tests/test_delete_ui.py`（删除按钮现位于 ⋯ 菜单内，需先开菜单）

> 改前端前必读 `docs/前端风格指南.md`。约定：新颜色变量必须同步 `[data-theme="dark"]`；htmx 事件在 Alpine 监听加 `.camel`；模板传值 `|tojson` 且属性用单引号。
> 关键 HTMX 细节：重命名表单加 `hx-swap="none"`，否则失败响应（200 空体）会清空表单内容。成功时 `HX-Redirect` 直接整页跳转。

每个层级用一个 Alpine `x-data` 包住「名字显示 + ⋯ 菜单 + 内联表单」。`editing` 为当前编辑字段（`false` / `'pcb'` / `'bom'` / `true`）。静止态 ⋯ 隐藏（沿用 hover-reveal），菜单项含「重命名 / 删除」。版本行一个菜单含两个重命名项。三处显示结构差异较大、且版本行需合并菜单，故不抽公共 macro、直接内联（工程权衡，符合可读优先）。

- [ ] **Step 1: Write the failing UI tests**

新建 `tests/test_rename_ui.py`：

```python
"""Playwright 测试：⋯ 菜单与四级定位内联重命名。"""
import httpx
import pytest
from playwright.sync_api import Page, expect


def _api_create_board(base, name, pcb, bom, uid,
                      csv="Reference,Part\nR1,10k\n"):
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": name, "pcb_version": pcb,
                         "bom_version": bom, "board_uid": uid},
                   files={"file": ("bom.csv", csv.encode(), "text/csv")})
    return r.headers.get("location", "").split("/board/")[-1].split("?")[0]


def test_menu_button_present_and_hidden(seeded_server, page: Page):
    """⋯ 菜单按钮存在且默认隐藏（opacity == 0）。"""
    page.goto(seeded_server)
    btn = page.locator(".menu-btn").first
    expect(btn).to_be_attached()
    assert float(btn.evaluate("el => getComputedStyle(el).opacity")) == 0


def test_menu_opens_and_shows_rename(seeded_server, page: Page):
    """悬停容器 → 点 ⋯ → 菜单出现「重命名」「删除」项。"""
    page.goto(seeded_server)
    group = page.locator(".group-title").first
    group.hover()
    group.locator(".menu-btn").click()
    pop = group.locator(".menu-pop")
    expect(pop).to_be_visible()
    assert "重命名" in pop.inner_text()
    assert "删除" in pop.inner_text()


def test_inline_rename_board_group(live_server, page: Page):
    """点重命名 → 名字变输入框 → 改值回车 → 整组改名成功。"""
    _api_create_board(live_server, "RenameMe", "v1", "bomA", "SN1")
    page.goto(live_server)
    group = page.locator(".group-title", has_text="RenameMe")
    group.hover()
    group.locator(".menu-btn").click()
    group.get_by_text("重命名").click()
    inp = group.locator(".rename-input")
    expect(inp).to_be_visible()
    inp.fill("Renamed")
    inp.press("Enter")
    page.wait_for_load_state("networkidle")
    page.goto(live_server)
    assert "Renamed" in page.content()
    assert "RenameMe" not in page.content()


def test_version_menu_has_two_rename_items(seeded_server, page: Page):
    """版本行菜单含「重命名 PCB版本」「重命名 BOM版本」两项。"""
    page.goto(seeded_server)
    vh = page.locator(".version-head").first
    vh.hover()
    vh.locator(".menu-btn").click()
    pop = vh.locator(".menu-pop")
    assert "重命名 PCB版本" in pop.inner_text()
    assert "重命名 BOM版本" in pop.inner_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rename_ui.py -v`
Expected: FAIL — 找不到 `.menu-btn`（尚未实现）

- [ ] **Step 3: Rewrite `home.html` content block**

将 `app/templates/home.html` 的 `{% block content %}` … `{% endblock %}` 整体替换为：

```html
{% block content %}
{% if not groups %}
<div class="empty">
  <h1>还没有任何单板</h1>
  <p class="muted">点右上角「＋ 新建单板」上传初始 BOM CSV 开始。</p>
</div>
{% endif %}
{% for name, versions in groups.items() %}
<div class="group-title" x-data="{open:false, editing:false}" @click.outside="open=false">
  <span x-show="!editing">📋 {{ name }}</span>
  <form x-show="editing" x-cloak class="rename-form" hx-post="/board-group/rename"
        hx-swap="none" @submit="editing=false">
    <input type="hidden" name="board_name" value='{{ name|tojson }}'>
    <input class="rename-input" name="new_name" value='{{ name|tojson }}'
           x-ref="gi" @keydown.escape="editing=false"
           x-effect="editing && $nextTick(() => $refs.gi.select())">
  </form>
  <span class="menu" x-show="!editing">
    <button class="menu-btn" @click="open=!open" title="操作">⋯</button>
    <div class="menu-pop" x-show="open" x-cloak>
      <button type="button" @click="editing=true; open=false">✏️ 重命名</button>
      <button type="button" class="del"
              hx-delete="/board-group?board_name={{ name | urlencode }}"
              hx-confirm="确认删除「{{ name }}」的全部数据？此操作将删除其下所有 BOM 版本和单板记录，不可恢复。">🗑 删除整组</button>
    </div>
  </span>
</div>
{% for v in versions %}
<div class="panel version">
  <div class="version-head" x-data="{open:false, editing:false}" @click.outside="open=false">
    <span class="badge {{ v.pcb_version | pcb_badge_class }}"
          x-show="editing!=='pcb'">PCB {{ v.pcb_version }}</span>
    <form x-show="editing==='pcb'" x-cloak class="rename-form" hx-post="/pcb-version/rename"
          hx-swap="none" @submit="editing=false">
      <input type="hidden" name="board_name" value='{{ name|tojson }}'>
      <input type="hidden" name="pcb_version" value='{{ v.pcb_version|tojson }}'>
      <input class="rename-input" name="new_name" value='{{ v.pcb_version|tojson }}'
             x-ref="pi" @keydown.escape="editing=false"
             x-effect="editing==='pcb' && $nextTick(() => $refs.pi.select())">
    </form>
    <b x-show="editing!=='bom'">{{ v.bom_version }}</b>
    <form x-show="editing==='bom'" x-cloak class="rename-form" hx-post="/bom-version/rename"
          hx-swap="none" @submit="editing=false">
      <input type="hidden" name="board_name" value='{{ name|tojson }}'>
      <input type="hidden" name="pcb_version" value='{{ v.pcb_version|tojson }}'>
      <input type="hidden" name="bom_version" value='{{ v.bom_version|tojson }}'>
      <input class="rename-input" name="new_name" value='{{ v.bom_version|tojson }}'
             x-ref="bi" @keydown.escape="editing=false"
             x-effect="editing==='bom' && $nextTick(() => $refs.bi.select())">
    </form>
    <span class="muted">{{ v.ref_count }} 个位号 · {{ v.boards|length }} 块单板</span>
    <span class="menu ml-auto" x-show="!editing">
      <button class="menu-btn" @click="open=!open" title="操作">⋯</button>
      <div class="menu-pop" x-show="open" x-cloak>
        <button type="button" @click="editing='pcb'; open=false">✏️ 重命名 PCB版本</button>
        <button type="button" @click="editing='bom'; open=false">✏️ 重命名 BOM版本</button>
        <button type="button" class="del"
                hx-delete="/bom-version?board_name={{ name | urlencode }}&pcb_version={{ v.pcb_version | urlencode }}&bom_version={{ v.bom_version | urlencode }}"
                hx-confirm="确认删除 BOM 版本「{{ v.bom_version }}」？将同时删除其下 {{ v.boards|length }} 块单板的全部记录，不可恢复。">🗑 删除 BOM 版本</button>
      </div>
    </span>
  </div>
  {% if v.boards %}
  <div class="chips">
    {% for b in v.boards %}
    <span class="chip-wrap" x-data="{open:false, editing:false}" @click.outside="open=false">
      <a class="chip {{ 'pending' if b.pending else '' }}" href="/board/{{ b.id }}"
         x-show="!editing">
        板 {{ b.board_uid }}
        <span class="muted">· {{ b.node_count }} 个节点{% if b.pending %} · {{ b.pending }} 条未提交{% endif %}</span>
      </a>
      <form x-show="editing" x-cloak class="rename-form" hx-post="/board/{{ b.id }}/rename"
            hx-swap="none" @submit="editing=false">
        <input class="rename-input" name="new_name" value='{{ b.board_uid|tojson }}'
               x-ref="ci{{ b.id }}" @keydown.escape="editing=false"
               x-effect="editing && $nextTick(() => $refs['ci{{ b.id }}'].select())">
      </form>
      <span class="menu" x-show="!editing">
        <button class="menu-btn chip-menu" @click="open=!open" title="操作">⋯</button>
        <div class="menu-pop" x-show="open" x-cloak>
          <button type="button" @click="editing=true; open=false">✏️ 重命名</button>
          <button type="button" class="del" hx-delete="/board/{{ b.id }}"
                  hx-confirm="确认删除单板「{{ b.board_uid }}」？此操作将同时删除该单板所有节点记录，不可恢复。">🗑 删除单板</button>
        </div>
      </span>
    </span>
    {% endfor %}
  </div>
  {% endif %}
</div>
{% endfor %}
{% endfor %}
{% endblock %}
```

- [ ] **Step 4: Add menu styles to `style.css`**

在 `app/static/style.css` 的 `:root` 块内追加一个变量（紧跟现有阴影/边框变量）：

```css
  --menu-shadow:#1f23282e;
```

在 `[data-theme="dark"]` 块内追加对应夜间值：

```css
  --menu-shadow:#00000066;
```

在文件末尾追加菜单与内联输入样式（复用现有 `.del-icon` 的 hover-reveal 思路）：

```css
/* ⋯ 操作菜单 */
.menu{position:relative;display:inline-flex}
.menu-btn{border:none;background:none;cursor:pointer;color:var(--muted);font-size:14px;
  padding:2px 6px;border-radius:3px;line-height:1;opacity:0;transition:opacity .15s,color .15s}
.group-title:hover .menu-btn,
.version-head:hover .menu-btn,
.chip-wrap:hover .menu-btn{opacity:.45}
.menu-btn:hover,.menu-btn:focus-visible{opacity:1;color:var(--fg)}
.menu-pop{position:absolute;right:0;top:100%;z-index:20;min-width:128px;margin-top:2px;
  background:var(--surface);border:1px solid var(--border);border-radius:6px;
  box-shadow:0 6px 18px var(--menu-shadow);padding:4px;display:flex;flex-direction:column}
.menu-pop button{border:none;background:none;text-align:left;cursor:pointer;font:inherit;
  font-size:13px;padding:6px 10px;border-radius:4px;color:var(--fg);white-space:nowrap}
.menu-pop button:hover{background:var(--surface-2)}
.menu-pop button.del{color:var(--red)}
/* 内联重命名输入框 */
.rename-form{display:inline-flex;margin:0}
.rename-input{border:1px solid var(--blue);border-radius:5px;padding:1px 6px;
  font:inherit;color:var(--fg);background:var(--surface);outline:none;
  box-shadow:0 0 0 3px var(--accent-soft)}
```

- [ ] **Step 5: Run rename UI tests to verify they pass**

Run: `pytest tests/test_rename_ui.py -v`
Expected: PASS（4 个用例）

- [ ] **Step 6: Update `test_delete_ui.py` for the new menu location**

删除按钮现在位于 ⋯ 菜单内（默认隐藏）。把 `tests/test_delete_ui.py` 中**依赖旧 `.del-icon` / 直接可见删除按钮**的用例改为「先开菜单」。具体改动：

1. 删除/替换这四个失效用例（它们断言旧 `.del-icon` 结构与可见性，已不成立）：
   `test_delete_buttons_present_on_home`、`test_chip_del_button_exists`、`test_del_icon_initial_hidden`、`test_del_icon_revealed_on_parent_hover`、`test_del_icon_hover_turns_red`。
   用下面这一个等价的「菜单内删除按钮存在」用例替代：

```python
def test_delete_actions_present_in_menus(seeded_server, page: Page):
    """三个层级的删除按钮都在各自 ⋯ 菜单内（默认隐藏）。"""
    page.goto(seeded_server)
    dels = page.locator(".menu-pop button.del")
    count = dels.count()
    assert count >= 3, f"期望 ≥3 个菜单内删除按钮，实际 {count}"
```

2. 把确认弹窗交互用例（`test_board_delete_cancel_keeps_data`、`test_board_delete_confirm_removes_board`、`test_bom_version_delete_confirm`、`test_board_group_delete_confirm`）中「直接定位删除按钮」的写法，改成先 hover 容器并点 ⋯ 再点菜单内删除按钮。示例（其余三个照此模式改）：

```python
def test_board_delete_confirm_removes_board(live_server, page: Page):
    """点单板 ⋯ → 删除单板 → accept → 单板消失。"""
    board_id = _api_create_board(live_server, "DelBoard", "v1", "bomDel", "DB002")
    page.goto(live_server)
    chip = page.locator(".chip-wrap", has_text="DB002")
    chip.hover()
    chip.locator(".menu-btn").click()
    del_btn = chip.locator("button.del")
    _click_accept_and_check(page, del_btn, "DB002", live_server)
```

- [ ] **Step 7: Run the full UI suite to verify it passes**

Run: `pytest tests/test_delete_ui.py tests/test_rename_ui.py -v`
Expected: PASS

- [ ] **Step 8: Manually verify both themes**（前端自检清单要求）

启动 `uvicorn app.main:app --reload`，在浅色与夜间两套主题下各确认：⋯ 悬停浮现、菜单弹出配色正确、内联输入框边框/聚焦光晕正常、重命名成功后整页刷新、冲突时 toast 中文报错且输入框保留。

- [ ] **Step 9: Commit**

```bash
git add app/templates/home.html app/static/style.css tests/test_rename_ui.py tests/test_delete_ui.py
git commit -m "feat: 首页四级定位 ⋯ 菜单与内联重命名 UI (issue #20)"
```

---

## 集成验证（两波合并后）

- [ ] 运行全量测试：`pytest`
  Expected: 全绿（原 74 passed + 本特性新增用例；`test_delete_ui.py` 已更新）。
- [ ] 人工冒烟：首页对四级各做一次重命名 + 一次冲突，确认级联与报错符合预期。

---

## Self-Review（计划自查记录）

- **Spec 覆盖**：级联范围（T2 四函数）、冲突拒绝（T2 + T3 toast）、空校验（T1）、⋯ 菜单 + 内联编辑（T4）、版本行双重命名项（T4 Step 3/Step 1 用例）、不写审计（全程无 edit_log 写入）、成功整页重渲染（T3 `_hx_redirect`）——均有对应任务。
- **接口一致性**：`rename_board_name/rename_pcb_version/rename_bom_version/rename_board_uid` 签名在 T2 定义、T3 调用一致；端点 `/board-group/rename`、`/pcb-version/rename`、`/bom-version/rename`、`/board/{id}/rename` 与表单字段 `board_name/pcb_version/bom_version/new_name` 在 T3 定义、T4 `hx-post` 拼接一致。
- **无占位符**：所有步骤含完整代码与命令。
- **偏离 spec 说明**：spec 提议抽 Jinja macro，计划改为内联（三处显示差异大 + 版本行合并菜单，macro 收益低），已在 T4 注明。
