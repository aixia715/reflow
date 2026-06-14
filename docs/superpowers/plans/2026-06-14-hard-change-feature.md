# 硬更改（飞线/割线）功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每个单板ID 下记录「硬更改」（飞线/割线等位号以外的物理改动），含标题/时间/文字/多图，按时间混排进状态图时间线。

**Architecture:** 沿用现有三层（纯逻辑 → 数据访问 → 薄路由）。硬更改独立两张表，不进 BOM 折叠引擎；图片存文件系统、DB 存路径；状态图渲染时把硬更改与 BOM 节点按时间合并展示。

**Tech Stack:** FastAPI / Starlette 1.2.1、SQLite、Jinja2 + HTMX、pytest（+ playwright UI）。

**设计来源：** `docs/superpowers/specs/2026-06-14-hard-change-feature-design.md`

**关键约定（实现全程遵守）：**
- 中文 UI/注释/错误消息。
- `templates.TemplateResponse(request, "x.html", {ctx})` 新签名，ctx 不放 `"request"`。
- 改前端必读 `docs/前端风格指南.md`：只用 CSS 变量、复用组件、两套主题自检。
- 时间字符串：BOM 节点用 `models._now()`（UTC ISO 秒）；硬更改 `occurred_at` 用本地 `datetime-local` 格式 `YYYY-MM-DDTHH:MM`。混排排序按字符串比较，近似到分钟即可（测试用明显不同的时间戳）。

---

## 文件结构

**新建：**
- `app/hard_change.py` — 纯逻辑 ★：`split_ext` / `make_stored_name` / `validate_upload` / `merge_timeline` + 常量
- `app/storage.py` — 文件 IO：`upload_dir` / `save_image` / `delete_images`
- `app/routes/hard_change.py` — 路由（new/create/detail/edit/delete）
- `app/templates/hard_change_form.html` — 新建/编辑共用表单
- `app/templates/hard_change_detail.html` — 详情页
- `tests/test_hard_change.py` — 纯逻辑测试
- `tests/test_hard_change_models.py` — 数据访问 + 级联测试
- `tests/test_hard_change_routes.py` — 路由测试
- `tests/test_hard_change_ui.py` — playwright 关键路径

**修改：**
- `app/db.py` — 加两张表
- `app/models.py` — 硬更改 CRUD + `delete_board` 级联返回 filename
- `app/main.py` — 挂 `/uploads` + mkdir + include router
- `app/routes/hierarchy.py` — 三个删除路由删盘
- `app/routes/board.py` — `state_graph` 改混排
- `app/templates/state_graph.html` — 混排渲染 + 入口按钮
- `app/static/style.css` — 硬更改卡片 + 表单/详情/画廊样式
- `.gitignore` — `uploads/`
- `tests/conftest.py` — `live_server` 注入 `REFLOW_UPLOAD_DIR`

---

## Task 1: 数据库 schema（两张新表）

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_hard_change_models.py`

- [ ] **Step 1: 写失败测试**（建文件）

```python
import pytest
from app.db import connect, init_db
from app import models


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def test_hard_change_tables_exist(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hard_changes" in names
    assert "hard_change_images" in names
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hard_change_models.py::test_hard_change_tables_exist -v`
Expected: FAIL（表不存在）

- [ ] **Step 3: 在 `app/db.py` 的 `SCHEMA` 末尾（`"""` 之前）追加两张表**

```sql
CREATE TABLE IF NOT EXISTS hard_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id    INTEGER NOT NULL REFERENCES boards_hierarchy(id),
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hard_change_images (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    hard_change_id INTEGER NOT NULL REFERENCES hard_changes(id),
    filename       TEXT NOT NULL,
    original_name  TEXT,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_hard_change_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_hard_change_models.py
git commit -m "feat: 硬更改两张表 schema (#5)"
```

---

## Task 2: 纯逻辑 `app/hard_change.py`

**Files:**
- Create: `app/hard_change.py`
- Test: `tests/test_hard_change.py`

- [ ] **Step 1: 写失败测试**（建文件，覆盖文件名/校验/混排）

```python
from app import hard_change as hc


def test_split_ext_lowercases_and_strips_dot():
    assert hc.split_ext("Photo.JPG") == "jpg"
    assert hc.split_ext("noext") == ""


def test_make_stored_name_unique_with_ext():
    a = hc.make_stored_name("x.png")
    b = hc.make_stored_name("x.png")
    assert a != b and a.endswith(".png")


def test_validate_upload_rejects_empty_title():
    assert hc.validate_upload("  ", []) is not None


def test_validate_upload_rejects_bad_ext():
    assert hc.validate_upload("标题", [("a.svg", 100)]) is not None


def test_validate_upload_rejects_too_big():
    assert hc.validate_upload("标题", [("a.png", hc.MAX_IMAGE_BYTES + 1)]) is not None


def test_validate_upload_rejects_too_many():
    imgs = [("a.png", 10)] * (hc.MAX_IMAGES + 1)
    assert hc.validate_upload("标题", imgs) is not None


def test_validate_upload_ok():
    assert hc.validate_upload("标题", [("a.png", 10), ("b.jpg", 20)]) is None


def test_merge_timeline_orders_newest_first_draft_pinned_top():
    nodes = [
        {"id": 1, "is_committed": 1, "committed_at": "2026-01-01T00:00", "created_at": "x"},
        {"id": 2, "is_committed": 1, "committed_at": "2026-03-01T00:00", "created_at": "x"},
        {"id": 3, "is_committed": 0, "committed_at": None, "created_at": "2026-09-09T00:00"},
    ]
    hards = [{"id": 9, "occurred_at": "2026-02-01T00:00"}]
    out = hc.merge_timeline(nodes, hards)
    kinds = [(it["kind"], it["obj"]["id"]) for it in out]
    # 草稿(node 3)在最顶；其余按时间降序：node2(3月) > hard9(2月) > node1(1月)
    assert kinds == [("node", 3), ("node", 2), ("hard", 9), ("node", 1)]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hard_change.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `app/hard_change.py`**

```python
"""硬更改纯逻辑：文件名、上传校验、时间线混排（零 Web/DB 依赖）。"""
import os
import uuid

ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 单图 10 MB
MAX_IMAGES = 12                       # 每条硬更改最多 12 张


def split_ext(filename: str) -> str:
    """返回小写扩展名（不含点）；无扩展名返回空串。"""
    _, ext = os.path.splitext(filename or "")
    return ext[1:].lower()


def make_stored_name(original: str) -> str:
    """生成唯一存盘名 uuid4.hex(+.ext)；扩展名取自原名。"""
    ext = split_ext(original)
    return f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex


def validate_upload(title: str, uploads: list[tuple[str, int]]) -> str | None:
    """校验标题与待上传图片 [(文件名, 字节数), ...]，返回中文错误消息或 None。"""
    if not (title or "").strip():
        return "标题不能为空"
    if len(uploads) > MAX_IMAGES:
        return f"附图最多 {MAX_IMAGES} 张，当前 {len(uploads)} 张"
    for name, size in uploads:
        if split_ext(name) not in ALLOWED_EXTS:
            return f"不支持的图片格式：{name}（仅支持 {', '.join(sorted(ALLOWED_EXTS))}）"
        if size > MAX_IMAGE_BYTES:
            return f"图片过大：{name}（单图上限 10 MB）"
    return None


def merge_timeline(nodes, hard_changes) -> list[dict]:
    """合并 BOM 节点与硬更改为按时间排序的时间线项。

    - 已提交节点用 committed_at、硬更改用 occurred_at 排序，最新在上；
    - 工作区草稿（未提交节点）恒钉在最顶（它是「当前正在做的」）。
    返回 [{"kind": "node"|"hard", "ts": str, "is_draft": bool, "obj": 原对象}]。
    """
    items: list[dict] = []
    for n in nodes:
        committed = bool(n["is_committed"])
        items.append({
            "kind": "node",
            "ts": n["committed_at"] if committed else n["created_at"],
            "is_draft": not committed,
            "obj": n,
        })
    for h in hard_changes:
        items.append({"kind": "hard", "ts": h["occurred_at"],
                      "is_draft": False, "obj": h})
    items.sort(key=lambda it: (it["is_draft"], it["ts"] or ""), reverse=True)
    return items
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_hard_change.py -v`
Expected: PASS（8 个测试）

- [ ] **Step 5: Commit**

```bash
git add app/hard_change.py tests/test_hard_change.py
git commit -m "feat: 硬更改纯逻辑（文件名/上传校验/时间线混排） (#5)"
```

---

## Task 3: 文件存储 `app/storage.py`

**Files:**
- Create: `app/storage.py`
- Test: `tests/test_hard_change.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_storage_save_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path / "up"))
    from app import storage
    storage.save_image("a.png", b"hello")
    p = tmp_path / "up" / "a.png"
    assert p.read_bytes() == b"hello"
    storage.delete_images(["a.png", "missing.png"])  # 缺文件不报错
    assert not p.exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hard_change.py::test_storage_save_and_delete -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `app/storage.py`**

```python
"""硬更改图片的文件系统读写（上传目录由 REFLOW_UPLOAD_DIR 配置）。"""
import os


def upload_dir() -> str:
    d = os.environ.get("REFLOW_UPLOAD_DIR", "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def save_image(stored_name: str, data: bytes) -> None:
    with open(os.path.join(upload_dir(), stored_name), "wb") as f:
        f.write(data)


def delete_images(filenames) -> None:
    d = upload_dir()
    for name in filenames:
        try:
            os.remove(os.path.join(d, name))
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_hard_change.py::test_storage_save_and_delete -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/storage.py tests/test_hard_change.py
git commit -m "feat: 硬更改图片文件存储 storage.py (#5)"
```

---

## Task 4: models 硬更改 CRUD

**Files:**
- Modify: `app/models.py`（文件末尾追加；放在现有函数之后）
- Test: `tests/test_hard_change_models.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def _mk_board(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    return models.create_board(conn, "B", "v1", "bomA", "SN1")


def test_create_and_get_hard_change_with_images(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(
        conn, bid, "飞线 R1-R2", "说明", "2026-06-01T10:30",
        [("aaa.png", "原图1.png"), ("bbb.jpg", "原图2.jpg")])
    hc = models.get_hard_change(conn, hc_id)
    assert hc["title"] == "飞线 R1-R2" and hc["board_id"] == bid
    imgs = models.list_hard_change_images(conn, hc_id)
    assert [i["filename"] for i in imgs] == ["aaa.png", "bbb.jpg"]
    assert [i["sort_order"] for i in imgs] == [0, 1]


def test_list_hard_changes_by_board(conn):
    bid = _mk_board(conn)
    models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00", [])
    models.create_hard_change(conn, bid, "B", "", "2026-02-01T00:00", [])
    assert [h["title"] for h in models.list_hard_changes(conn, bid)] == ["A", "B"]


def test_update_hard_change(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(conn, bid, "旧", "x", "2026-01-01T00:00", [])
    models.update_hard_change(conn, hc_id, "新", "y", "2026-05-05T05:05")
    hc = models.get_hard_change(conn, hc_id)
    assert (hc["title"], hc["description"], hc["occurred_at"]) == ("新", "y", "2026-05-05T05:05")


def test_add_and_delete_hard_change_images(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00",
                                      [("a.png", "a")])
    models.add_hard_change_images(conn, hc_id, [("b.png", "b")])
    imgs = models.list_hard_change_images(conn, hc_id)
    assert [i["sort_order"] for i in imgs] == [0, 1]
    fns = models.delete_hard_change_images(conn, [imgs[0]["id"]])
    assert fns == ["a.png"]
    assert [i["filename"] for i in models.list_hard_change_images(conn, hc_id)] == ["b.png"]


def test_delete_hard_change_returns_filenames(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00",
                                      [("a.png", "a"), ("b.png", "b")])
    fns = models.delete_hard_change(conn, hc_id)
    assert sorted(fns) == ["a.png", "b.png"]
    assert models.get_hard_change(conn, hc_id) is None
    assert models.list_hard_change_images(conn, hc_id) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hard_change_models.py -v`
Expected: FAIL（函数不存在）

- [ ] **Step 3: 在 `app/models.py` 末尾追加**

```python
# ---------- 硬更改 ----------

def create_hard_change(conn, board_id, title, description, occurred_at, images) -> int:
    """新建硬更改 + 附图。images: [(存盘名, 原始名), ...]，按序写 sort_order。返回 id。"""
    now = _now()
    hc_id = conn.execute(
        "INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
        " VALUES(?,?,?,?,?)",
        (board_id, title, description, occurred_at, now),
    ).lastrowid
    for i, (fn, orig) in enumerate(images):
        conn.execute(
            "INSERT INTO hard_change_images"
            "(hard_change_id,filename,original_name,sort_order,created_at)"
            " VALUES(?,?,?,?,?)",
            (hc_id, fn, orig, i, now),
        )
    conn.commit()
    return hc_id


def get_hard_change(conn, hc_id) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM hard_changes WHERE id=?", (hc_id,)).fetchone()


def list_hard_changes(conn, board_id) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM hard_changes WHERE board_id=? ORDER BY occurred_at, id",
        (board_id,),
    ).fetchall()


def list_hard_change_images(conn, hc_id) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM hard_change_images WHERE hard_change_id=? ORDER BY sort_order, id",
        (hc_id,),
    ).fetchall()


def update_hard_change(conn, hc_id, title, description, occurred_at) -> None:
    conn.execute(
        "UPDATE hard_changes SET title=?, description=?, occurred_at=? WHERE id=?",
        (title, description, occurred_at, hc_id),
    )
    conn.commit()


def add_hard_change_images(conn, hc_id, images) -> None:
    """追加附图，sort_order 接续现有最大值。images: [(存盘名, 原始名), ...]。"""
    now = _now()
    start = conn.execute(
        "SELECT COALESCE(MAX(sort_order)+1, 0) AS n FROM hard_change_images"
        " WHERE hard_change_id=?",
        (hc_id,),
    ).fetchone()["n"]
    for i, (fn, orig) in enumerate(images):
        conn.execute(
            "INSERT INTO hard_change_images"
            "(hard_change_id,filename,original_name,sort_order,created_at)"
            " VALUES(?,?,?,?,?)",
            (hc_id, fn, orig, start + i, now),
        )
    conn.commit()


def delete_hard_change_images(conn, image_ids) -> list[str]:
    """按图片 id 删除行，返回被删的 filename 列表（供删盘）。"""
    if not image_ids:
        return []
    ph = ",".join("?" * len(image_ids))
    rows = conn.execute(
        f"SELECT filename FROM hard_change_images WHERE id IN ({ph})", image_ids
    ).fetchall()
    conn.execute(f"DELETE FROM hard_change_images WHERE id IN ({ph})", image_ids)
    conn.commit()
    return [r["filename"] for r in rows]


def delete_hard_change(conn, hc_id) -> list[str]:
    """删除硬更改及其附图行，返回被删的 filename 列表（供删盘）。"""
    rows = conn.execute(
        "SELECT filename FROM hard_change_images WHERE hard_change_id=?", (hc_id,)
    ).fetchall()
    conn.execute("DELETE FROM hard_change_images WHERE hard_change_id=?", (hc_id,))
    conn.execute("DELETE FROM hard_changes WHERE id=?", (hc_id,))
    conn.commit()
    return [r["filename"] for r in rows]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_hard_change_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_hard_change_models.py
git commit -m "feat: 硬更改数据访问 CRUD (#5)"
```

---

## Task 5: 级联删除（删单板/版本/组连带删硬更改与图片）

**Files:**
- Modify: `app/models.py:160`（`delete_board`/`delete_bom_version`/`delete_board_name`）
- Modify: `app/routes/hierarchy.py`（三个删除路由删盘）
- Test: `tests/test_hard_change_models.py`（追加）

- [ ] **Step 1: 追加失败测试**

```python
def test_delete_board_cascades_hard_changes(conn):
    bid = _mk_board(conn)
    models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00",
                              [("x.png", "x")])
    fns = models.delete_board(conn, bid)
    assert fns == ["x.png"]
    assert models.list_hard_changes(conn, bid) == []


def test_delete_bom_version_cascades_hard_changes(conn):
    bid = _mk_board(conn)
    models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00",
                              [("y.png", "y")])
    fns = models.delete_bom_version(conn, "B", "v1", "bomA")
    assert "y.png" in fns
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hard_change_models.py -k cascade -v`
Expected: FAIL（`delete_board` 当前返回 None）

- [ ] **Step 3a: 改 `delete_board`（`app/models.py:160`），在 `DELETE FROM boards_hierarchy` 之前插入硬更改清理，并返回 filename 列表**

把现有 `delete_board` 整体替换为：

```python
def delete_board(conn, board_id) -> list[str]:
    """删除单板及其节点、changeset、审计日志、硬更改（级联）。返回被删图片 filename。"""
    node_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM nodes WHERE board_id=?", (board_id,)
    ).fetchall()]
    if node_ids:
        ph = ",".join("?" * len(node_ids))
        conn.execute(f"DELETE FROM edit_log WHERE node_id IN ({ph})", node_ids)
        conn.execute(f"DELETE FROM node_changes WHERE node_id IN ({ph})", node_ids)
        conn.execute("DELETE FROM nodes WHERE board_id=?", (board_id,))
    hc_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM hard_changes WHERE board_id=?", (board_id,)
    ).fetchall()]
    filenames: list[str] = []
    if hc_ids:
        ph = ",".join("?" * len(hc_ids))
        filenames = [r["filename"] for r in conn.execute(
            f"SELECT filename FROM hard_change_images WHERE hard_change_id IN ({ph})",
            hc_ids,
        ).fetchall()]
        conn.execute(f"DELETE FROM hard_change_images WHERE hard_change_id IN ({ph})", hc_ids)
        conn.execute("DELETE FROM hard_changes WHERE board_id=?", (board_id,))
    conn.execute("DELETE FROM boards_hierarchy WHERE id=?", (board_id,))
    conn.commit()
    return filenames
```

- [ ] **Step 3b: 改 `delete_bom_version` 累积返回 filename**

把循环改为累积，并 `return filenames`：

```python
def delete_bom_version(conn, board_name, pcb_version, bom_version) -> list[str]:
    """删除 BOM 版本下所有单板及初始 BOM（级联）。返回被删图片 filename。"""
    boards = conn.execute(
        "SELECT id FROM boards_hierarchy WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    ).fetchall()
    filenames: list[str] = []
    for b in boards:
        filenames += delete_board(conn, b["id"])
    conn.execute(
        "DELETE FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    )
    conn.commit()
    return filenames
```

- [ ] **Step 3c: 改 `delete_board_name` 累积返回 filename**

```python
def delete_board_name(conn, board_name) -> list[str]:
    """删除单板名称下所有 BOM 版本及其数据（级联）。返回被删图片 filename。"""
    versions = conn.execute(
        "SELECT DISTINCT pcb_version, bom_version FROM initial_bom WHERE board_name=?",
        (board_name,),
    ).fetchall()
    filenames: list[str] = []
    for v in versions:
        filenames += delete_bom_version(conn, board_name, v["pcb_version"], v["bom_version"])
    conn.commit()
    return filenames
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_hard_change_models.py -v`
Expected: PASS

- [ ] **Step 5: 路由层删盘** — 改 `app/routes/hierarchy.py`

在文件顶部 import 处加入 `storage`：把 `from app import models` 改为 `from app import models, storage`。
然后给三个删除路由接收返回值并删盘：

```python
@router.delete("/board/{board_id}")
def board_delete(board_id: int):
    conn = get_conn()
    if not models.get_board(conn, board_id):
        raise HTTPException(status_code=404, detail="单板不存在")
    storage.delete_images(models.delete_board(conn, board_id))
    return _hx_redirect("/")


@router.delete("/bom-version")
def bom_version_delete(
    board_name: str = Query(...),
    pcb_version: str = Query(...),
    bom_version: str = Query(...),
):
    conn = get_conn()
    if not models.get_initial_bom(conn, board_name, pcb_version, bom_version):
        raise HTTPException(status_code=404, detail="BOM 版本不存在")
    storage.delete_images(models.delete_bom_version(conn, board_name, pcb_version, bom_version))
    return _hx_redirect("/")


@router.delete("/board-group")
def board_group_delete(board_name: str = Query(...)):
    conn = get_conn()
    storage.delete_images(models.delete_board_name(conn, board_name))
    return _hx_redirect("/")
```

- [ ] **Step 6: 跑回归确认现有删除路由仍通过**

Run: `pytest tests/test_routes.py tests/test_hard_change_models.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/models.py app/routes/hierarchy.py tests/test_hard_change_models.py
git commit -m "feat: 删除单板/版本/组级联清理硬更改与图片文件 (#5)"
```

---

## Task 6: `main.py` 挂载 /uploads + include router + `.gitignore`

**Files:**
- Modify: `app/main.py`
- Modify: `.gitignore`
- Test: `tests/test_hard_change_routes.py`

- [ ] **Step 1: 写失败测试**（建文件）

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.main import create_app
    return TestClient(create_app())


def _new_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "SN1"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]   # board_id


def test_hard_change_new_form_loads(client):
    bid = _new_board(client)
    r = client.get(f"/board/{bid}/hard-change/new")
    assert r.status_code == 200
    assert "记录硬更改" in r.text or "硬更改" in r.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hard_change_routes.py -v`
Expected: FAIL（路由 404 — 还没建 router/模板）

- [ ] **Step 3: 改 `app/main.py`**

在 `app.mount("/static", ...)` 之后加 uploads 挂载（`create_app` 内）：

```python
    upload_dir = os.environ.get("REFLOW_UPLOAD_DIR", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")
```

把 `from app.routes import hierarchy, board, log` 改为
`from app.routes import hierarchy, board, log, hard_change`，
并在其它 `include_router` 之后加 `app.include_router(hard_change.router)`。
（`os` 已在 `main.py` 顶部 import。）

- [ ] **Step 4: 追加 `.gitignore`**

在 `.gitignore` 末尾加一行：

```
uploads/
```

- [ ] **Step 5: 本测试此时仍会 FAIL（router 未建），留待 Task 7 通过。先确认 import 无误**

Run: `python -c "from app.main import create_app; create_app()"`
Expected: 报错 `ModuleNotFoundError: app.routes.hard_change`（符合预期，Task 7 建它）

- [ ] **Step 6: Commit（与 Task 7 连续，先存盘）**

```bash
git add app/main.py .gitignore
git commit -m "chore: 挂载 /uploads 静态目录 + gitignore uploads (#5)"
```

---

## Task 7: 路由 `app/routes/hard_change.py`（new/create/detail/edit/delete）

**Files:**
- Create: `app/routes/hard_change.py`
- Test: `tests/test_hard_change_routes.py`（追加）

> 模板 `hard_change_form.html` / `hard_change_detail.html` 在 Task 9 才建。本任务先建路由，测试用「创建→重定向」与「详情 200」验证；表单页渲染测试放到 Task 9 后跑全量。**本任务结束时 `test_hard_change_new_form_loads` 仍可能因模板缺失 FAIL，Task 9 后转 PASS。**先建一个最小占位模板让路由可渲染（见 Step 3 末尾）。

- [ ] **Step 1: 追加失败测试**

```python
def test_create_hard_change_redirects_and_persists(client):
    bid = _new_board(client)
    r = client.post(f"/board/{bid}/hard-change",
                    data={"title": "飞线 A", "occurred_at": "2026-06-01T10:30",
                          "description": "把 R1 飞到 R9"},
                    files=[("files", ("p.png", b"\x89PNG\r\n", "image/png"))],
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/board/{bid}")
    rg = client.get(f"/board/{bid}")
    assert "飞线 A" in rg.text


def test_create_hard_change_rejects_empty_title(client):
    bid = _new_board(client)
    r = client.post(f"/board/{bid}/hard-change",
                    data={"title": "  ", "occurred_at": "2026-06-01T10:30",
                          "description": ""},
                    follow_redirects=False)
    assert r.status_code == 200
    assert "标题不能为空" in r.text


def test_hard_change_detail_and_delete(client):
    bid = _new_board(client)
    client.post(f"/board/{bid}/hard-change",
                data={"title": "割线 X", "occurred_at": "2026-06-02T09:00",
                      "description": "割断 net5"},
                follow_redirects=False)
    rg = client.get(f"/board/{bid}")
    assert "割线 X" in rg.text
    # 找到 hard-change id：列表页详情链接
    import re
    m = re.search(rf"/board/{bid}/hard-change/(\d+)", rg.text)
    assert m, "状态图未渲染硬更改详情链接"
    hid = m.group(1)
    rd = client.get(f"/board/{bid}/hard-change/{hid}")
    assert rd.status_code == 200 and "割线 X" in rd.text
    rdel = client.post(f"/board/{bid}/hard-change/{hid}/delete")
    assert rdel.status_code == 200 and rdel.headers.get("HX-Redirect", "").startswith(f"/board/{bid}")
    rg2 = client.get(f"/board/{bid}")
    assert "割线 X" not in rg2.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_hard_change_routes.py -v`
Expected: FAIL（router 不存在）

- [ ] **Step 3: 实现 `app/routes/hard_change.py`**

```python
from datetime import datetime

from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, Response

from app.main import templates, get_conn
from app import models, hard_change, storage

router = APIRouter()


def _now_minute() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _hx_redirect(url: str) -> Response:
    resp = Response(status_code=200)
    resp.headers["HX-Redirect"] = url
    return resp


def _require_board(conn, board_id):
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    return board


def _require_hc(conn, board_id, hc_id):
    hc = models.get_hard_change(conn, hc_id)
    if hc is None or hc["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="硬更改不存在")
    return hc


@router.get("/board/{board_id}/hard-change/new")
def hc_new_form(request: Request, board_id: int):
    conn = get_conn()
    board = _require_board(conn, board_id)
    return templates.TemplateResponse(request, "hard_change_form.html", {
        "board": board, "board_id": board_id, "mode": "new",
        "hc": None, "images": [], "form": {}, "error": None,
        "default_time": _now_minute(),
    })


@router.post("/board/{board_id}/hard-change")
async def hc_create(request: Request, board_id: int,
                    title: str = Form(""), occurred_at: str = Form(""),
                    description: str = Form(""),
                    files: list[UploadFile] = File(default=[])):
    conn = get_conn()
    board = _require_board(conn, board_id)
    title = title.strip()
    blobs = [(f.filename, await f.read()) for f in files if f.filename]
    err = hard_change.validate_upload(title, [(n, len(b)) for n, b in blobs])
    if err:
        return templates.TemplateResponse(request, "hard_change_form.html", {
            "board": board, "board_id": board_id, "mode": "new", "hc": None,
            "images": [], "error": err, "default_time": occurred_at or _now_minute(),
            "form": {"title": title, "occurred_at": occurred_at, "description": description},
        }, status_code=200)
    saved = []
    for name, data in blobs:
        stored = hard_change.make_stored_name(name)
        storage.save_image(stored, data)
        saved.append((stored, name))
    occurred = occurred_at.strip() or _now_minute()
    models.create_hard_change(conn, board_id, title, description.strip(), occurred, saved)
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已记录硬更改", status_code=303)


@router.get("/board/{board_id}/hard-change/{hc_id}")
def hc_detail(request: Request, board_id: int, hc_id: int):
    conn = get_conn()
    board = _require_board(conn, board_id)
    hc = _require_hc(conn, board_id, hc_id)
    return templates.TemplateResponse(request, "hard_change_detail.html", {
        "board": board, "board_id": board_id, "hc": hc,
        "images": models.list_hard_change_images(conn, hc_id),
    })


@router.get("/board/{board_id}/hard-change/{hc_id}/edit")
def hc_edit_form(request: Request, board_id: int, hc_id: int):
    conn = get_conn()
    board = _require_board(conn, board_id)
    hc = _require_hc(conn, board_id, hc_id)
    return templates.TemplateResponse(request, "hard_change_form.html", {
        "board": board, "board_id": board_id, "mode": "edit", "hc": hc,
        "images": models.list_hard_change_images(conn, hc_id), "form": {}, "error": None,
        "default_time": hc["occurred_at"],
    })


@router.post("/board/{board_id}/hard-change/{hc_id}/edit")
async def hc_edit(request: Request, board_id: int, hc_id: int,
                  title: str = Form(""), occurred_at: str = Form(""),
                  description: str = Form(""),
                  delete_image_ids: list[int] = Form(default=[]),
                  files: list[UploadFile] = File(default=[])):
    conn = get_conn()
    board = _require_board(conn, board_id)
    hc = _require_hc(conn, board_id, hc_id)
    title = title.strip()
    blobs = [(f.filename, await f.read()) for f in files if f.filename]
    existing = models.list_hard_change_images(conn, hc_id)
    remaining = [im for im in existing if im["id"] not in set(delete_image_ids)]
    err = hard_change.validate_upload(title, [(n, len(b)) for n, b in blobs])
    if err is None and len(remaining) + len(blobs) > hard_change.MAX_IMAGES:
        err = f"附图最多 {hard_change.MAX_IMAGES} 张"
    if err:
        return templates.TemplateResponse(request, "hard_change_form.html", {
            "board": board, "board_id": board_id, "mode": "edit", "hc": hc,
            "images": existing, "error": err, "default_time": occurred_at or hc["occurred_at"],
            "form": {"title": title, "occurred_at": occurred_at, "description": description},
        }, status_code=200)
    if delete_image_ids:
        storage.delete_images(models.delete_hard_change_images(conn, list(delete_image_ids)))
    saved = []
    for name, data in blobs:
        stored = hard_change.make_stored_name(name)
        storage.save_image(stored, data)
        saved.append((stored, name))
    if saved:
        models.add_hard_change_images(conn, hc_id, saved)
    models.update_hard_change(conn, hc_id, title, description.strip(),
                              occurred_at.strip() or hc["occurred_at"])
    return RedirectResponse(
        f"/board/{board_id}/hard-change/{hc_id}?flash=✓ 已更新硬更改", status_code=303)


@router.post("/board/{board_id}/hard-change/{hc_id}/delete")
def hc_delete(board_id: int, hc_id: int):
    conn = get_conn()
    _require_board(conn, board_id)
    _require_hc(conn, board_id, hc_id)
    storage.delete_images(models.delete_hard_change(conn, hc_id))
    return _hx_redirect(f"/board/{board_id}?flash=✓ 已删除硬更改")
```

- [ ] **Step 4: 路由测试此时仍需模板**：先跑 models/纯逻辑回归，模板渲染测试待 Task 9。

Run: `pytest tests/test_hard_change.py tests/test_hard_change_models.py -v`
Expected: PASS（路由测试 `tests/test_hard_change_routes.py` 暂缓到 Task 9 后全绿）

- [ ] **Step 5: Commit**

```bash
git add app/routes/hard_change.py tests/test_hard_change_routes.py
git commit -m "feat: 硬更改路由 new/create/detail/edit/delete (#5)"
```

---

## Task 8: 状态图混排（路由 + 模板 + 入口按钮）

**Files:**
- Modify: `app/routes/board.py:66`（`state_graph`）
- Modify: `app/templates/state_graph.html`

- [ ] **Step 1: 改 `app/routes/board.py`**

顶部 import 加 `hard_change`：把 `from app import models, propagation, audit` 改为
`from app import models, propagation, audit, hard_change`。
把 `state_graph` 整体替换为：

```python
@router.get("/board/{board_id}")
def state_graph(request: Request, board_id: int):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    nodes = models.list_nodes(conn, board_id)
    hcs = [dict(h) for h in models.list_hard_changes(conn, board_id)]
    timeline = hard_change.merge_timeline(nodes, hcs)
    initial_count = len(models.get_initial_bom(
        conn, board["board_name"], board["pcb_version"], board["bom_version"]))
    return templates.TemplateResponse(
        request, "state_graph.html",
        {"board": board, "board_id": board_id, "timeline": timeline,
         "summaries": models.node_summaries(conn, board_id),
         "initial_count": initial_count})
```

- [ ] **Step 2: 改 `app/templates/state_graph.html`**

把 `{% block ctxlinks %}` 改为加入入口按钮：

```html
{% block ctxlinks %}
<a href="/board/{{ board_id }}/log">审计日志</a>
<a class="btn btn-outline" href="/board/{{ board_id }}/hard-change/new">＋ 记录硬更改</a>
{% endblock %}
```

把 `<div class="timeline"> ... </div>` 整段替换为按 `timeline` 混排渲染：

```html
<div class="timeline">
{% for it in timeline %}
{% if it.kind == 'node' %}
{% set n = it.obj %}{% set s = summaries[n.id] %}
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
{% else %}
{% set h = it.obj %}
<a class="tl-item hard" href="/board/{{ board_id }}/hard-change/{{ h.id }}">
  <span class="dot"></span>
  <div class="tl-card">
    <b>🔧 {{ h.title }}</b>
    <span class="badge badge-yellow">硬更改</span>
    <div class="muted">{{ h.occurred_at|replace('T', ' ') }}{% if h.description %} · {{ h.description[:40] }}{% endif %}</div>
  </div>
</a>
{% endif %}
{% endfor %}
</div>
```

- [ ] **Step 3: 跑回归 + 路由测试（混排链接现在能渲染）**

Run: `pytest tests/test_routes.py tests/test_hard_change_routes.py::test_create_hard_change_redirects_and_persists tests/test_hard_change_routes.py::test_hard_change_detail_and_delete -v`
Expected: 创建/详情/删除测试 PASS（详情/表单模板缺失的用例待 Task 9）

- [ ] **Step 4: Commit**

```bash
git add app/routes/board.py app/templates/state_graph.html
git commit -m "feat: 状态图混排硬更改 + 记录入口 (#5)"
```

---

## Task 9: 表单页 + 详情页模板

**Files:**
- Create: `app/templates/hard_change_form.html`
- Create: `app/templates/hard_change_detail.html`

> 先读 `app/templates/board_new.html` 与 `app/templates/node_detail.html` 对齐页面骨架（`extends base.html`、`page-head`、`crumbs`、`flash`）。

- [ ] **Step 1: 建 `app/templates/hard_change_form.html`**

```html
{% extends "base.html" %}
{% block title %}{{ '编辑' if mode == 'edit' else '记录' }}硬更改 · 板 {{ board.board_uid }} — Reflow{% endblock %}
{% block crumbs %}
<a href="/">首页</a> /
<a href="/board/{{ board_id }}">板 {{ board.board_uid }}</a> /
{{ '编辑' if mode == 'edit' else '记录' }}硬更改
{% endblock %}
{% block content %}
<div class="page-head"><h1>{{ '编辑' if mode == 'edit' else '记录' }}硬更改</h1></div>
{% if error %}<div class="flash flash-error">{{ error }}</div>{% endif %}
<form class="panel" method="post" enctype="multipart/form-data"
      action="{% if mode == 'edit' %}/board/{{ board_id }}/hard-change/{{ hc.id }}/edit{% else %}/board/{{ board_id }}/hard-change{% endif %}">
  <label>标题
    <input class="input" name="title" required
           value="{{ form.title if form.title is defined else (hc.title if hc else '') }}">
  </label>
  <label>发生时间
    <input class="input" type="datetime-local" name="occurred_at"
           value="{{ form.occurred_at if form.occurred_at else default_time }}">
  </label>
  <label>文字说明
    <textarea class="input" name="description" rows="4">{{ form.description if form.description is defined else (hc.description if hc else '') }}</textarea>
  </label>
  {% if images %}
  <div class="muted">已有附图（勾选以删除）：</div>
  <div class="hc-gallery">
    {% for im in images %}
    <label class="hc-thumb">
      <img src="/uploads/{{ im.filename }}" alt="{{ im.original_name }}">
      <span><input type="checkbox" name="delete_image_ids" value="{{ im.id }}"> 删除</span>
    </label>
    {% endfor %}
  </div>
  {% endif %}
  <label>{{ '追加附图' if mode == 'edit' else '附图（可多选）' }}
    <input class="input" type="file" name="files" multiple
           accept="image/png,image/jpeg,image/webp,image/gif">
  </label>
  <div class="form-actions">
    <button class="btn btn-primary" type="submit">{{ '保存' if mode == 'edit' else '记录' }}</button>
    <a class="btn btn-outline" href="/board/{{ board_id }}">取消</a>
  </div>
</form>
{% endblock %}
```

- [ ] **Step 2: 建 `app/templates/hard_change_detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ hc.title }} · 硬更改 — Reflow{% endblock %}
{% block crumbs %}
<a href="/">首页</a> /
<a href="/board/{{ board_id }}">板 {{ board.board_uid }}</a> /
硬更改
{% endblock %}
{% block ctxlinks %}
<a class="btn btn-outline" href="/board/{{ board_id }}/hard-change/{{ hc.id }}/edit">编辑</a>
<button class="btn-link danger" hx-post="/board/{{ board_id }}/hard-change/{{ hc.id }}/delete"
        hx-confirm="确认删除硬更改「{{ hc.title }}」？此操作将一并删除其所有附图，不可恢复。">删除</button>
{% endblock %}
{% block content %}
<div class="page-head"><h1>🔧 {{ hc.title }}</h1></div>
<div class="panel">
  <div class="muted">发生时间：{{ hc.occurred_at|replace('T', ' ') }}</div>
  {% if hc.description %}<p class="hc-desc">{{ hc.description }}</p>{% endif %}
  {% if images %}
  <div class="hc-gallery">
    {% for im in images %}
    <a class="hc-photo" href="/uploads/{{ im.filename }}" target="_blank">
      <img src="/uploads/{{ im.filename }}" alt="{{ im.original_name }}">
    </a>
    {% endfor %}
  </div>
  {% else %}
  <div class="muted">（无附图）</div>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 3: 跑硬更改全部测试**

Run: `pytest tests/test_hard_change.py tests/test_hard_change_models.py tests/test_hard_change_routes.py -v`
Expected: 全部 PASS（含 `test_hard_change_new_form_loads`、空标题、详情/删除）

- [ ] **Step 4: Commit**

```bash
git add app/templates/hard_change_form.html app/templates/hard_change_detail.html
git commit -m "feat: 硬更改表单页与详情页模板 (#5)"
```

---

## Task 10: 样式（硬更改卡片 + 表单 + 画廊）

**Files:**
- Modify: `app/static/style.css`

> 先读 `docs/前端风格指南.md`。只用 CSS 变量；改完两套主题（🌙/☀️）都要看。本任务只用现有颜色变量，不新增变量，故夜间模式自动适配。

- [ ] **Step 1: 在 `style.css` 组件区追加**

```css
/* 硬更改时间线卡片：用黄色左缘 + 实心点区分 BOM 节点 */
.tl-item.hard .dot{background:var(--yellow);border-color:var(--yellow)}
.tl-item.hard .tl-card{border-left:3px solid var(--yellow-bg)}
/* 硬更改详情/表单 */
.hc-desc{white-space:pre-wrap;margin:8px 0;font-size:14px}
.form-actions{display:flex;gap:8px;margin-top:12px}
.hc-gallery{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}
.hc-gallery img{max-width:240px;max-height:240px;border:1px solid var(--border);
  border-radius:var(--radius);display:block}
.hc-thumb{display:inline-flex;flex-direction:column;gap:4px;font-size:13px}
.hc-photo{display:inline-block}
```

> 注意：若 `--yellow` / `--yellow-bg` 变量名与 `:root` 实际不符，先 `grep -n "yellow" app/static/style.css` 确认真实变量名后替换。

- [ ] **Step 2: 人工核对两套主题**

启动 `uvicorn app.main:app --reload`，建一块板 + 记录一条硬更改（带图），查看：
状态图卡片可区分、详情页图片成排、表单可上传；右上角切换 🌙/☀️ 两套主题都正常。

- [ ] **Step 3: Commit**

```bash
git add app/static/style.css
git commit -m "style: 硬更改卡片与画廊样式 (#5)"
```

---

## Task 11: UI 测试（playwright 关键路径）+ conftest 隔离 + 全量回归

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_hard_change_ui.py`

- [ ] **Step 1: 给 `tests/conftest.py` 的 `live_server` 注入上传目录**

把 `live_server` 里的 env 行
`env = {**os.environ, "REFLOW_DB": str(db)}`
改为：

```python
    up = tmp_path_factory.mktemp("uploads")
    env = {**os.environ, "REFLOW_DB": str(db), "REFLOW_UPLOAD_DIR": str(up)}
```

- [ ] **Step 2: 写 `tests/test_hard_change_ui.py`**

```python
"""硬更改关键路径的浏览器测试。"""
import io
import httpx
from playwright.sync_api import Page, expect

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
           b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


def _make_board(base: str) -> str:
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "HCBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": "HC1"},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")})
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def test_record_hard_change_flow(live_server, page: Page):
    bid = _make_board(live_server)
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.fill("input[name=title]", "飞线 R1→R9")
    page.fill("textarea[name=description]", "演示飞线")
    page.set_input_files("input[name=files]", files=[
        {"name": "p.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")
    # 跳回状态图，时间线出现硬更改卡片
    expect(page.locator(".tl-item.hard")).to_contain_text("飞线 R1→R9")
    # 进详情页看到图片
    page.locator(".tl-item.hard").first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator(".hc-gallery img").first).to_be_visible()
```

- [ ] **Step 3: 跑 UI 测试**

Run: `pytest tests/test_hard_change_ui.py -v`
Expected: PASS（需 `pytest-playwright` + chromium 已安装；未装则 `pip install pytest-playwright && python -m playwright install chromium`）

- [ ] **Step 4: 跑全量回归**

Run: `pytest -q`
Expected: 全绿（现有 74+ 测试 + 新增硬更改测试）

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_hard_change_ui.py
git commit -m "test: 硬更改关键路径 UI 测试 + 测试上传目录隔离 (#5)"
```

---

## 完成标准

- `pytest -q` 全绿。
- 状态图能按时间混排显示硬更改与 BOM 节点；草稿恒在顶部。
- 可新建（多图）/ 查看 / 编辑（增删图）/ 删除硬更改；删除连带清磁盘文件。
- 删单板 / BOM 版本 / 整组时硬更改与图片文件一并清除。
- 两套主题人工核对通过。
- 折叠引擎与现有 BOM 流程零改动（回归测试不变）。
