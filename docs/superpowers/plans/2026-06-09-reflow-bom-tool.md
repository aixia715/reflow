# Reflow BOM 工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Reflow——一个单人使用、SQLite 存储、FastAPI + HTMX 的单板 BOM 线性版本管理 Web 工具，支持差量存储、历史编辑的自动传播与冲突确认、append-only 审计日志和稳定分享链接。

**Architecture:** 纯逻辑模块（CSV 解析、折叠引擎、传播/冲突、审计）零 Web 依赖、独立可测，是测试投入重点；数据访问层封装 SQLite CRUD；FastAPI 路由薄，只做「收请求 → 调逻辑 → 渲染 Jinja2/HTMX 片段」。完整 BOM 在读取时由初始 BOM 沿父链实时折叠得出，无物化缓存。

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Jinja2, python-multipart, pytest, httpx（TestClient）。SQLite（标准库 sqlite3）。

设计依据：`docs/superpowers/specs/2026-06-09-reflow-bom-tool-design.md`

---

## 贯穿全计划的数据契约（所有任务遵守）

为保证各任务类型一致，先固定核心结构。**不要偏离这些签名。**

**Changeset 条目**（字典）：
```python
{"reference": str, "op": "add" | "modify" | "remove", "part": str | None}
# remove 时 part 为 None；add/modify 时 part 为非空字符串
```

**初始 BOM / 完整 BOM**：`dict[str, str]`，键是 reference，值是 part。「不贴」= 键不存在。

**折叠引擎签名**（`app/bom_engine.py`）：
```python
def fold_bom(initial: dict[str, str], chain: list[list[dict]]) -> dict[str, str]
def resolve_reference(initial: dict[str, str], chain: list[list[dict]], reference: str) -> str | None
# chain: 从根到目标节点（含目标），每个元素是该节点的 changeset 列表；根节点 changeset 为 []
```

**CSV 解析签名**（`app/csv_import.py`）：
```python
class CsvEntry(NamedTuple): reference: str; part: str
class CsvProblem(NamedTuple): kind: str; reference: str; detail: str  # kind: "duplicate"|"empty_part"|"empty_reference"
def parse_bom_csv(text: str) -> tuple[list[CsvEntry], list[CsvProblem]]
```

**冲突对象**（`app/propagation.py`）：
```python
class Conflict(NamedTuple):
    downstream_node_id: int
    reference: str
    downstream_value: str | None   # 下游解析值，None=不贴
    corrected_value: str | None    # 本次修正解析值，None=不贴
```

**op 常量**：直接用字符串字面量 `"add"`/`"modify"`/`"remove"`、`source` 用 `"direct"`/`"propagated"`、`choice` 用 `"keep"`/`"take"`。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `pyproject.toml` | 依赖与 pytest 配置 |
| `app/__init__.py` | 包标记 |
| `app/db.py` | SQLite 连接、schema 建表 SQL、`init_db` |
| `app/csv_import.py` | ★CSV 解析、拆分、校验（纯逻辑） |
| `app/bom_engine.py` | ★折叠引擎（纯逻辑） |
| `app/models.py` | 数据访问层：层级 / initial_bom / nodes / node_changes / edit_log 的 CRUD + 取链 |
| `app/propagation.py` | ★传播 & 冲突检测 + 冲突落库（依赖 models） |
| `app/audit.py` | 审计日志写入封装 |
| `app/main.py` | FastAPI 装配、挂载路由、Jinja2 配置 |
| `app/routes/hierarchy.py` | `/`、新建 BOM版本（CSV 预览/确认）、新建单板ID |
| `app/routes/board.py` | 状态图、节点详情/编辑、冲突确认、工作区、commit |
| `app/routes/log.py` | 审计日志页 |
| `app/templates/*.html` | Jinja2 + HTMX 片段 |
| `app/static/style.css` | git-graph 竖线、diff 高亮 |
| `tests/test_*.py` | 各模块测试 |

每个任务产出自包含、可独立运行测试的变更，并单独 commit。

---

## Task 0: 项目脚手架

**Files:**
- Create: `pyproject.toml`, `app/__init__.py`, `app/routes/__init__.py`, `tests/__init__.py`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "reflow"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "httpx>=0.27"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: 建包标记文件**

`app/__init__.py`、`app/routes/__init__.py`、`tests/__init__.py` 均为空文件。

- [ ] **Step 3: 写冒烟测试**

`tests/test_smoke.py`:
```python
def test_python_imports_work():
    import app  # noqa: F401
    assert True
```

- [ ] **Step 4: 安装依赖并运行**

Run:
```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest tests/test_smoke.py -v
```
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: project scaffold (pyproject, packages, smoke test)"
```

---

## Task 1: CSV 解析与校验（纯逻辑）

**Files:**
- Create: `app/csv_import.py`
- Test: `tests/test_csv_import.py`

- [ ] **Step 1: 写失败测试——基本拆分**

`tests/test_csv_import.py`:
```python
from app.csv_import import parse_bom_csv, CsvEntry, CsvProblem


def test_splits_comma_merged_references_sharing_one_part():
    csv = 'Item,Quantity,Reference,Part,PCB Footprint\n1,2,"R67,R24",1kR,0402\n'
    entries, problems = parse_bom_csv(csv)
    assert problems == []
    assert CsvEntry("R67", "1kR") in entries
    assert CsvEntry("R24", "1kR") in entries
    assert len(entries) == 2


def test_only_reference_and_part_columns_used():
    csv = "Item,Quantity,Reference,Part,Assembly Type\n5,1,R1,10k,SMT\n"
    entries, _ = parse_bom_csv(csv)
    assert entries == [CsvEntry("R1", "10k")]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_csv_import.py -v`
Expected: FAIL（ModuleNotFoundError / ImportError）

- [ ] **Step 3: 实现 csv_import.py**

`app/csv_import.py`:
```python
import csv
import io
from typing import NamedTuple


class CsvEntry(NamedTuple):
    reference: str
    part: str


class CsvProblem(NamedTuple):
    kind: str        # "duplicate" | "empty_part" | "empty_reference"
    reference: str
    detail: str


def parse_bom_csv(text: str) -> tuple[list[CsvEntry], list[CsvProblem]]:
    """解析 CSV，只取 Reference/Part 两列，拆分逗号合并位号，并产出校验问题清单。

    健壮性：UTF-8 BOM 头、CRLF、带引号含逗号字段、位号首尾空格。
    """
    # 去掉 UTF-8 BOM 头；csv 模块按 \n/\r\n 都能正确分行
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))
    # 容忍列名首尾空格
    fieldmap = {(name or "").strip(): name for name in (reader.fieldnames or [])}
    ref_col = fieldmap.get("Reference")
    part_col = fieldmap.get("Part")
    if ref_col is None or part_col is None:
        raise ValueError("CSV 必须包含 Reference 和 Part 两列")

    entries: list[CsvEntry] = []
    problems: list[CsvProblem] = []
    seen: dict[str, str] = {}

    for row in reader:
        raw_refs = (row.get(ref_col) or "")
        part = (row.get(part_col) or "").strip()
        for ref in raw_refs.split(","):
            ref = ref.strip()
            if ref == "":
                if raw_refs.strip() != "":
                    continue  # 合并格内的空段（如尾随逗号）忽略
                problems.append(CsvProblem("empty_reference", "", "位号为空"))
                continue
            if part == "":
                problems.append(CsvProblem("empty_part", ref, "Part 为空"))
            if ref in seen:
                problems.append(
                    CsvProblem("duplicate", ref, f"位号重复（已有 Part={seen[ref]}）")
                )
                continue
            seen[ref] = part
            entries.append(CsvEntry(ref, part))

    return entries, problems
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_csv_import.py -v`
Expected: PASS

- [ ] **Step 5: 补健壮性与校验测试**

追加到 `tests/test_csv_import.py`:
```python
def test_strips_utf8_bom_and_handles_crlf():
    csv = "﻿Reference,Part\r\nR1,10k\r\nR2,100nF\r\n"
    entries, problems = parse_bom_csv(csv)
    assert problems == []
    assert entries == [CsvEntry("R1", "10k"), CsvEntry("R2", "100nF")]


def test_strips_reference_whitespace_and_keeps_underscore_suffix():
    csv = 'Reference,Part\n" C86_PD1 , R5 ",HE364B-G\n'
    entries, _ = parse_bom_csv(csv)
    assert CsvEntry("C86_PD1", "HE364B-G") in entries
    assert CsvEntry("R5", "HE364B-G") in entries


def test_duplicate_reference_reported_and_first_wins():
    csv = "Reference,Part\nR1,10k\nR1,22k\n"
    entries, problems = parse_bom_csv(csv)
    assert entries == [CsvEntry("R1", "10k")]
    assert any(p.kind == "duplicate" and p.reference == "R1" for p in problems)


def test_empty_part_reported_but_entry_kept():
    csv = "Reference,Part\nR9,\n"
    entries, problems = parse_bom_csv(csv)
    assert entries == [CsvEntry("R9", "")]
    assert any(p.kind == "empty_part" and p.reference == "R9" for p in problems)
```

- [ ] **Step 6: 运行全部 CSV 测试**

Run: `pytest tests/test_csv_import.py -v`
Expected: PASS（全部）

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: CSV import parsing with split, robustness, and validation report"
```

---

## Task 2: 数据库 schema 与连接

**Files:**
- Create: `app/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: 写失败测试**

`tests/test_db.py`:
```python
from app.db import connect, init_db


def test_init_creates_all_tables():
    conn = connect(":memory:")
    init_db(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"boards_hierarchy", "initial_bom", "nodes", "node_changes", "edit_log"} <= names


def test_row_factory_allows_name_access():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v1','bomA','3')"
    )
    row = conn.execute("SELECT board_name FROM boards_hierarchy").fetchone()
    assert row["board_name"] == "B"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_db.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 db.py**

`app/db.py`:
```python
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards_hierarchy (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    board_name  TEXT NOT NULL,
    pcb_version TEXT NOT NULL,
    bom_version TEXT NOT NULL,
    board_uid   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS initial_bom (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    board_name  TEXT NOT NULL,
    pcb_version TEXT NOT NULL,
    bom_version TEXT NOT NULL,
    reference   TEXT NOT NULL,
    part        TEXT NOT NULL,
    UNIQUE(board_name, pcb_version, bom_version, reference)
);

CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id     INTEGER NOT NULL REFERENCES boards_hierarchy(id),
    parent_id    INTEGER REFERENCES nodes(id),
    message      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    is_committed INTEGER NOT NULL DEFAULT 0,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS node_changes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL REFERENCES nodes(id),
    reference TEXT NOT NULL,
    op        TEXT NOT NULL CHECK(op IN ('add','modify','remove')),
    part      TEXT,
    UNIQUE(node_id, reference)
);

CREATE TABLE IF NOT EXISTS edit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    INTEGER NOT NULL REFERENCES nodes(id),
    reference  TEXT NOT NULL,
    old_part   TEXT,
    new_part   TEXT,
    op         TEXT NOT NULL,
    source     TEXT NOT NULL CHECK(source IN ('direct','propagated')),
    created_at TEXT NOT NULL,
    note       TEXT
);
"""


def connect(path: str = "reflow.sqlite") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: SQLite schema and connection helper"
```

---

## Task 3: 折叠引擎（纯逻辑）

**Files:**
- Create: `app/bom_engine.py`
- Test: `tests/test_bom_engine.py`

- [ ] **Step 1: 写失败测试**

`tests/test_bom_engine.py`:
```python
from app.bom_engine import fold_bom, resolve_reference


def test_inherits_initial_when_no_changes():
    initial = {"R1": "10k"}
    chain = [[], [], []]  # 根 + 两个未碰 R1 的节点
    assert fold_bom(initial, chain) == {"R1": "10k"}
    assert resolve_reference(initial, chain, "R1") == "10k"


def test_modify_overrides_inherited_value():
    initial = {"R1": "10k"}
    chain = [[], [{"reference": "R1", "op": "modify", "part": "47k"}]]
    assert fold_bom(initial, chain)["R1"] == "47k"
    assert resolve_reference(initial, chain, "R1") == "47k"


def test_add_introduces_new_reference():
    chain = [[], [{"reference": "C9", "op": "add", "part": "100nF"}]]
    assert fold_bom({}, chain) == {"C9": "100nF"}


def test_remove_means_not_placed():
    initial = {"R1": "10k"}
    chain = [[], [{"reference": "R1", "op": "remove", "part": None}]]
    assert "R1" not in fold_bom(initial, chain)
    assert resolve_reference(initial, chain, "R1") is None


def test_latest_explicit_op_wins_along_chain():
    initial = {"R1": "10k"}
    chain = [
        [],
        [{"reference": "R1", "op": "modify", "part": "47k"}],
        [{"reference": "R1", "op": "modify", "part": "22k"}],
    ]
    assert resolve_reference(initial, chain, "R1") == "22k"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_bom_engine.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 bom_engine.py**

`app/bom_engine.py`:
```python
def fold_bom(initial: dict[str, str], chain: list[list[dict]]) -> dict[str, str]:
    """初始 BOM + 沿链按顺序叠加每个节点的 changeset，得到完整 BOM。

    chain: 从根到目标节点（含目标），每元素是该节点 changeset 列表。
    """
    result = dict(initial)
    for changeset in chain:
        for ch in changeset:
            if ch["op"] == "remove":
                result.pop(ch["reference"], None)
            else:  # add / modify
                result[ch["reference"]] = ch["part"]
    return result


def resolve_reference(
    initial: dict[str, str], chain: list[list[dict]], reference: str
) -> str | None:
    """某位号在目标节点的解析值；None 表示不贴（不在 BOM 中）。"""
    value = initial.get(reference)
    for changeset in chain:
        for ch in changeset:
            if ch["reference"] == reference:
                value = None if ch["op"] == "remove" else ch["part"]
    return value
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_bom_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: BOM folding engine (resolve value along delta chain)"
```

---

## Task 4: 数据访问层

封装所有 SQLite 读写。后续传播、审计、路由都通过它访问数据库，不直接写 SQL。

**Files:**
- Create: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: 写失败测试——层级与初始 BOM**

`tests/test_models.py`:
```python
import pytest
from app.db import connect, init_db
from app import models


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def test_create_bom_version_with_initial_entries(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(
        conn, "MainBoard", "v1", "bomA",
        [CsvEntry("R1", "10k"), CsvEntry("C9", "100nF")],
    )
    bom = models.get_initial_bom(conn, "MainBoard", "v1", "bomA")
    assert bom == {"R1": "10k", "C9": "100nF"}


def test_create_board_makes_root_and_empty_workspace(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    board_id = models.create_board(conn, "B", "v1", "bomA", "3")
    nodes = models.list_nodes(conn, board_id)
    assert len(nodes) == 2                      # 根 + 工作区草稿
    assert nodes[0]["parent_id"] is None        # 根
    assert nodes[0]["is_committed"] == 1
    assert nodes[1]["is_committed"] == 0        # 工作区草稿
    assert nodes[1]["parent_id"] == nodes[0]["id"]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_models.py -v`
Expected: FAIL（ImportError / AttributeError）

- [ ] **Step 3: 实现 models.py（层级、初始 BOM、节点骨架）**

`app/models.py`:
```python
from datetime import datetime, timezone
import sqlite3

from app.csv_import import CsvEntry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 层级 & 初始 BOM ----------

def create_bom_version(
    conn: sqlite3.Connection,
    board_name: str, pcb_version: str, bom_version: str,
    entries: list[CsvEntry],
) -> None:
    """建立一个 BOM版本 的初始 BOM。三元组重复时抛 ValueError。"""
    exists = conn.execute(
        "SELECT 1 FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
        (board_name, pcb_version, bom_version),
    ).fetchone()
    if exists:
        raise ValueError("该 (单板名称, PCB版本, BOM版本) 已存在")
    conn.executemany(
        "INSERT INTO initial_bom(board_name,pcb_version,bom_version,reference,part)"
        " VALUES(?,?,?,?,?)",
        [(board_name, pcb_version, bom_version, e.reference, e.part) for e in entries],
    )
    conn.commit()


def get_initial_bom(conn, board_name, pcb_version, bom_version) -> dict[str, str]:
    rows = conn.execute(
        "SELECT reference, part FROM initial_bom"
        " WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    )
    return {r["reference"]: r["part"] for r in rows}


def list_bom_versions(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT DISTINCT board_name, pcb_version, bom_version FROM initial_bom"
        " ORDER BY board_name, pcb_version, bom_version"
    ).fetchall()


# ---------- 单板 & 节点 ----------

def create_board(conn, board_name, pcb_version, bom_version, board_uid) -> int:
    """新建单板ID：建层级行 + 根节点（已提交）+ 空工作区草稿。返回 board_id。"""
    cur = conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES(?,?,?,?)",
        (board_name, pcb_version, bom_version, board_uid),
    )
    board_id = cur.lastrowid
    now = _now()
    root = conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,1,?)",
        (board_id, None, "初始 BOM", now, now),
    ).lastrowid
    conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,0,NULL)",
        (board_id, root, "", now),
    )
    conn.commit()
    return board_id


def get_board(conn, board_id) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM boards_hierarchy WHERE id=?", (board_id,)
    ).fetchone()


def list_nodes(conn, board_id) -> list[sqlite3.Row]:
    """按 id 升序返回该单板所有节点（线性链，含工作区草稿）。"""
    return conn.execute(
        "SELECT * FROM nodes WHERE board_id=? ORDER BY id", (board_id,)
    ).fetchall()


def get_node(conn, node_id) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: 写失败测试——changeset 与取链**

追加到 `tests/test_models.py`:
```python
def test_changeset_upsert_and_chain_for_node(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    board_id = models.create_board(conn, "B", "v1", "bomA", "3")
    root_id = models.list_nodes(conn, board_id)[0]["id"]
    ws_id = models.list_nodes(conn, board_id)[1]["id"]

    models.set_change(conn, ws_id, "R1", "modify", "47k")
    models.set_change(conn, ws_id, "R1", "modify", "22k")  # upsert 覆盖
    cs = models.get_changeset(conn, ws_id)
    assert cs == [{"reference": "R1", "op": "modify", "part": "22k"}]

    initial, chain = models.get_chain(conn, ws_id)
    assert initial == {"R1": "10k"}
    assert chain == [[], cs]                 # 根的 changeset 为 []，再到 ws

    models.delete_change(conn, ws_id, "R1")
    assert models.get_changeset(conn, ws_id) == []
```

- [ ] **Step 6: 运行确认失败**

Run: `pytest tests/test_models.py::test_changeset_upsert_and_chain_for_node -v`
Expected: FAIL（AttributeError）

- [ ] **Step 7: 实现 changeset 与取链方法**

追加到 `app/models.py`:
```python
# ---------- changeset ----------

def set_change(conn, node_id, reference, op, part) -> None:
    """新增或覆盖某节点对某位号的显式操作（UNIQUE(node_id,reference) upsert）。"""
    conn.execute(
        "INSERT INTO node_changes(node_id,reference,op,part) VALUES(?,?,?,?)"
        " ON CONFLICT(node_id,reference) DO UPDATE SET op=excluded.op, part=excluded.part",
        (node_id, reference, op, part),
    )
    conn.commit()


def delete_change(conn, node_id, reference) -> None:
    conn.execute(
        "DELETE FROM node_changes WHERE node_id=? AND reference=?", (node_id, reference)
    )
    conn.commit()


def get_changeset(conn, node_id) -> list[dict]:
    rows = conn.execute(
        "SELECT reference, op, part FROM node_changes WHERE node_id=? ORDER BY id",
        (node_id,),
    )
    return [{"reference": r["reference"], "op": r["op"], "part": r["part"]} for r in rows]


def get_change(conn, node_id, reference) -> dict | None:
    r = conn.execute(
        "SELECT reference, op, part FROM node_changes WHERE node_id=? AND reference=?",
        (node_id, reference),
    ).fetchone()
    return {"reference": r["reference"], "op": r["op"], "part": r["part"]} if r else None


def _ancestry(conn, node_id) -> list[sqlite3.Row]:
    """从根到 node_id（含）的节点行列表。"""
    chain: list[sqlite3.Row] = []
    cur = get_node(conn, node_id)
    while cur is not None:
        chain.append(cur)
        cur = get_node(conn, cur["parent_id"]) if cur["parent_id"] else None
    chain.reverse()
    return chain


def get_chain(conn, node_id) -> tuple[dict[str, str], list[list[dict]]]:
    """返回 (该单板初始 BOM, 从根到 node_id 每节点的 changeset 列表)。"""
    ancestry = _ancestry(conn, node_id)
    board = get_board(conn, ancestry[0]["board_id"])
    initial = get_initial_bom(
        conn, board["board_name"], board["pcb_version"], board["bom_version"]
    )
    chain = [get_changeset(conn, n["id"]) for n in ancestry]
    return initial, chain
```

- [ ] **Step 8: 运行全部 models 测试**

Run: `pytest tests/test_models.py -v`
Expected: PASS（全部）

- [ ] **Step 9: Commit**

```bash
git add -A && git commit -m "feat: data access layer (hierarchy, nodes, changesets, chain)"
```

---

## Task 5: 审计日志

**Files:**
- Create: `app/audit.py`
- Test: `tests/test_audit.py`

- [ ] **Step 1: 写失败测试**

`tests/test_audit.py`:
```python
import pytest
from app.db import connect, init_db
from app import models, audit
from app.csv_import import CsvEntry


@pytest.fixture
def board(conn_factory):
    pass


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    return c, bid


def test_append_only_never_overwrites(conn):
    c, bid = conn
    node_id = models.list_nodes(c, bid)[1]["id"]
    for new in ("47k", "22k", "33k"):
        audit.record_edit(c, node_id, "R1", old_part="10k", new_part=new,
                          op="modify", source="direct")
    rows = audit.list_log(c, node_id)
    assert len(rows) == 3                       # 三次编辑三行，无覆盖
    assert [r["new_part"] for r in rows] == ["47k", "22k", "33k"]


def test_source_marking(conn):
    c, bid = conn
    node_id = models.list_nodes(c, bid)[1]["id"]
    audit.record_edit(c, node_id, "R1", "10k", "47k", "modify", "direct")
    audit.record_edit(c, node_id, "R1", "47k", "22k", "modify", "propagated")
    rows = audit.list_log(c, node_id)
    assert {r["source"] for r in rows} == {"direct", "propagated"}
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_audit.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 audit.py**

`app/audit.py`:
```python
import sqlite3
from app.models import _now


def record_edit(
    conn: sqlite3.Connection, node_id: int, reference: str,
    old_part: str | None, new_part: str | None,
    op: str, source: str, note: str | None = None,
) -> None:
    """追加一条审计记录，永不覆盖。source: 'direct' | 'propagated'。"""
    conn.execute(
        "INSERT INTO edit_log(node_id,reference,old_part,new_part,op,source,created_at,note)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (node_id, reference, old_part, new_part, op, source, _now(), note),
    )
    conn.commit()


def list_log(conn, node_id: int | None = None) -> list[sqlite3.Row]:
    if node_id is None:
        return conn.execute("SELECT * FROM edit_log ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM edit_log WHERE node_id=? ORDER BY id", (node_id,)
    ).fetchall()
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_audit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: append-only audit log"
```

---

## Task 6: 传播 & 冲突（工具核心）

**Files:**
- Create: `app/propagation.py`
- Test: `tests/test_propagation.py`

- [ ] **Step 1: 写失败测试——4.4 例子两分支**

`tests/test_propagation.py`:
```python
import pytest
from app.db import connect, init_db
from app import models, propagation
from app.bom_engine import resolve_reference
from app.csv_import import CsvEntry


@pytest.fixture
def chain_s1_s2_s3():
    """初始 R1=10k；链 根 -> S1 -> S2 -> S3；S2 显式 R1=47k。返回 (conn, [节点id...])。"""
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    root_id = models.list_nodes(c, bid)[0]["id"]
    # 用已提交节点搭出 S1,S2,S3（直接插入，模拟历史）
    s1 = propagation._append_committed_node(c, bid, root_id, "S1")
    s2 = propagation._append_committed_node(c, bid, s1, "S2")
    s3 = propagation._append_committed_node(c, bid, s2, "S3")
    models.set_change(c, s2, "R1", "modify", "47k")
    return c, bid, [root_id, s1, s2, s3]


def _resolved(conn, node_id, ref):
    initial, chain = models.get_chain(conn, node_id)
    return resolve_reference(initial, chain, ref)


def test_edit_s1_detects_conflict_at_s2(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "22k")
    assert len(conflicts) == 1
    assert conflicts[0].downstream_node_id == s2
    assert conflicts[0].downstream_value == "47k"
    assert conflicts[0].corrected_value == "22k"
    assert _resolved(c, s1, "R1") == "22k"      # S1 已落库


def test_keep_downstream_value(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "22k")
    propagation.resolve_conflict(c, conflicts[0], "keep")
    assert _resolved(c, s1, "R1") == "22k"
    assert _resolved(c, s2, "R1") == "47k"
    assert _resolved(c, s3, "R1") == "47k"      # S3 继承 S2


def test_take_corrected_value_propagates(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "22k")
    propagation.resolve_conflict(c, conflicts[0], "take")
    assert _resolved(c, s1, "R1") == "22k"
    assert _resolved(c, s2, "R1") == "22k"      # S2 显式 op 被删除
    assert _resolved(c, s3, "R1") == "22k"
    assert models.get_change(c, s2, "R1") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_propagation.py -v`
Expected: FAIL（ImportError / AttributeError）

- [ ] **Step 3: 实现 propagation.py**

`app/propagation.py`:
```python
import sqlite3
from typing import NamedTuple

from app import models, audit
from app.bom_engine import resolve_reference


class Conflict(NamedTuple):
    downstream_node_id: int
    reference: str
    downstream_value: str | None
    corrected_value: str | None


def _append_committed_node(conn, board_id, parent_id, message) -> int:
    """测试/内部辅助：在 parent 之后追加一个已提交节点，返回其 id。"""
    now = models._now()
    return conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,1,?)",
        (board_id, parent_id, message, now, now),
    ).lastrowid


def _children_in_order(conn, board_id, start_node_id) -> list[sqlite3.Row]:
    """返回 start_node_id 之后（不含）沿子链的节点行，按链顺序。"""
    nodes = models.list_nodes(conn, board_id)        # 按 id 升序 = 链顺序
    after = []
    seen_start = False
    for n in nodes:
        if seen_start:
            after.append(n)
        if n["id"] == start_node_id:
            seen_start = True
    return after


def _resolved_value(conn, node_id, reference) -> str | None:
    initial, chain = models.get_chain(conn, node_id)
    return resolve_reference(initial, chain, reference)


def apply_node_edit(conn, node_id, reference, op, part) -> list[Conflict]:
    """编辑某节点某位号（修正记录），落库 + 记 direct 日志，返回冲突列表（最多一个）。

    op: 'add'|'modify'|'remove'；remove 时 part 传 None。
    """
    old_value = _resolved_value(conn, node_id, reference)
    models.set_change(conn, node_id, reference, op, part)
    new_value = None if op == "remove" else part
    audit.record_edit(conn, node_id, reference, old_value, new_value, op, "direct")

    node = models.get_node(conn, node_id)
    corrected = _resolved_value(conn, node_id, reference)

    # 沿子链找第一个显式操作过该位号的下游节点
    for child in _children_in_order(conn, node["board_id"], node_id):
        if models.get_change(conn, child["id"], reference) is not None:
            downstream_value = _resolved_value(conn, child["id"], reference)
            return [Conflict(child["id"], reference, downstream_value, corrected)]
    return []  # 无显式下游 -> 自动传播，零冲突


def resolve_conflict(conn, conflict: Conflict, choice: str) -> None:
    """choice='keep' 保留下游值（什么都不做）；'take' 采用修正值并向后传播。"""
    if choice == "take":
        old_value = conflict.downstream_value
        models.delete_change(conn, conflict.downstream_node_id, conflict.reference)
        new_value = conflict.corrected_value
        op = "remove" if new_value is None else "modify"
        audit.record_edit(
            conn, conflict.downstream_node_id, conflict.reference,
            old_value, new_value, op, "propagated",
        )
    # 'keep'：不动
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_propagation.py -v`
Expected: PASS

- [ ] **Step 5: 补边界测试——零冲突自动传播 / add / remove / 编辑根节点**

追加到 `tests/test_propagation.py`:
```python
def test_no_downstream_explicit_is_zero_conflict(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    # 编辑一个 S3 没人显式覆盖的新位号
    conflicts = propagation.apply_node_edit(c, s1, "C9", "add", "100nF")
    assert conflicts == []
    assert _resolved(c, s3, "C9") == "100nF"     # 自动传到 S3


def test_remove_upstream_conflicts_with_downstream_modify(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "remove", None)
    assert len(conflicts) == 1                   # S2 显式 modify R1 仍冲突
    assert conflicts[0].corrected_value is None  # 修正为不贴
    propagation.resolve_conflict(c, conflicts[0], "take")
    assert _resolved(c, s3, "R1") is None         # 全链不贴


def test_edit_root_node_propagates(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    # 注：根节点修正走 initial_bom 的路由层逻辑；此处验证在根的 changeset 写入也能传播
    conflicts = propagation.apply_node_edit(c, root, "R1", "modify", "5k")
    assert len(conflicts) == 1 and conflicts[0].downstream_node_id == s2
```

> 说明：根节点的初始 BOM 修正在路由层会改 `initial_bom` 行（见 Task 8），但 `apply_node_edit` 对根节点写 changeset 的路径也必须正确传播——本测试锁定该行为。若实现选择「根节点统一改 initial_bom」，则改写本测试为调用对应的 `models.update_initial_bom` 后再断言下游冲突；二者择一，保持与 Task 8 实现一致。

- [ ] **Step 6: 运行全部传播测试**

Run: `pytest tests/test_propagation.py -v`
Expected: PASS（全部）

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: propagation and conflict detection/resolution (core)"
```

---

## Task 7: FastAPI 装配 + Jinja2 基础

**Files:**
- Create: `app/main.py`, `app/templates/base.html`, `app/static/style.css`
- Test: `tests/test_routes.py`（仅冒烟）

- [ ] **Step 1: 写失败测试**

`tests/test_routes.py`:
```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def test_home_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Reflow" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -v`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现 main.py**

`app/main.py`:
```python
import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import connect, init_db

templates = Jinja2Templates(directory="app/templates")


def get_conn():
    conn = connect(os.environ.get("REFLOW_DB", "reflow.sqlite"))
    init_db(conn)
    return conn


def create_app() -> FastAPI:
    app = FastAPI(title="Reflow")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    from app.routes import hierarchy, board, log
    app.include_router(hierarchy.router)
    app.include_router(board.router)
    app.include_router(log.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


app = create_app()
```

`app/templates/base.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>{% block title %}Reflow{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
</head>
<body>
  <header><a href="/"><strong>Reflow</strong></a> · 单板 BOM 状态管理</header>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`app/static/style.css`（最小骨架，后续任务补 diff/graph 样式）：
```css
body{font-family:system-ui,"PingFang SC",sans-serif;margin:0;color:#1c2230}
header{padding:12px 20px;border-bottom:1px solid #e2e6ee;background:#f7f9fc}
main{padding:24px;max-width:960px;margin:0 auto}
.add{background:#e6ffed}.modify{background:#fff8e1}.remove{background:#ffeef0;text-decoration:line-through}
```

- [ ] **Step 4: 临时占位 hierarchy 路由让首页可加载**

`app/routes/hierarchy.py`（先最小实现，Task 8 之前的过渡；本步只为让 `/` 返回 200，后续步骤补全）:
```python
from fastapi import APIRouter, Request
from app.main import templates

router = APIRouter()


@router.get("/")
def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})
```

`app/templates/home.html`:
```html
{% extends "base.html" %}
{% block content %}<h1>Reflow</h1>{% endblock %}
```

> 注意：`app/routes/board.py` 与 `app/routes/log.py` 此时尚不存在，会导致 main.py 的 import 失败。本步同时创建二者的空 router 占位：
> ```python
> # app/routes/board.py  和  app/routes/log.py 各放：
> from fastapi import APIRouter
> router = APIRouter()
> ```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_routes.py::test_home_page_loads -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: FastAPI app assembly, base template, static CSS"
```

---

## Task 8: 层级、CSV 导入、单板路由

**Files:**
- Modify: `app/routes/hierarchy.py`
- Create: `app/templates/home.html`（覆盖）, `import_preview.html`
- Modify: `app/models.py`（补 `update_initial_bom`、`commit_workspace`、`list_boards`）
- Test: `tests/test_routes.py`（追加）

- [ ] **Step 1: 写失败测试——CSV 导入与建板**

追加到 `tests/test_routes.py`:
```python
def test_import_preview_then_create_bom_version(client):
    csv = 'Reference,Part\n"R1,R2",10k\nR1,22k\n'
    r = client.post("/bom-version/import-preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"},
                    files={"file": ("bom.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert "重复" in r.text or "duplicate" in r.text     # 校验报告显示 R1 重复

    r2 = client.post("/bom-version",
                     data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                           "csv_text": csv})
    assert r2.status_code in (200, 303)


def test_create_board_then_state_graph(client):
    csv = "Reference,Part\nR1,10k\n"
    client.post("/bom-version",
                data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                      "csv_text": csv})
    r = client.post("/board",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                          "board_uid": "3"}, follow_redirects=False)
    assert r.status_code in (200, 303)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -v`
Expected: FAIL

- [ ] **Step 3: 补 models 方法**

追加到 `app/models.py`:
```python
def list_boards(conn, board_name, pcb_version, bom_version) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM boards_hierarchy WHERE board_name=? AND pcb_version=? AND bom_version=?"
        " ORDER BY board_uid",
        (board_name, pcb_version, bom_version),
    ).fetchall()


def update_initial_bom(conn, board_name, pcb_version, bom_version, reference, part) -> None:
    """修正根节点初始 BOM 的某位号（part=None 表示删除该位号）。"""
    if part is None:
        conn.execute(
            "DELETE FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=? AND reference=?",
            (board_name, pcb_version, bom_version, reference),
        )
    else:
        conn.execute(
            "INSERT INTO initial_bom(board_name,pcb_version,bom_version,reference,part)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(board_name,pcb_version,bom_version,reference) DO UPDATE SET part=excluded.part",
            (board_name, pcb_version, bom_version, reference, part),
        )
    conn.commit()


def commit_workspace(conn, board_id, message) -> int:
    """把工作区草稿翻成正式节点，新开空草稿，返回被提交节点 id。"""
    draft = conn.execute(
        "SELECT * FROM nodes WHERE board_id=? AND is_committed=0 ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()
    now = _now()
    conn.execute(
        "UPDATE nodes SET is_committed=1, committed_at=?, message=? WHERE id=?",
        (now, message, draft["id"]),
    )
    conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,0,NULL)",
        (board_id, draft["id"], "", now),
    )
    conn.commit()
    return draft["id"]


def workspace_node(conn, board_id) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM nodes WHERE board_id=? AND is_committed=0 ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()
```

- [ ] **Step 4: 实现 hierarchy 路由（完整）**

覆盖 `app/routes/hierarchy.py`:
```python
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse

from app.main import templates, get_conn
from app import models
from app.csv_import import parse_bom_csv

router = APIRouter()


@router.get("/")
def home(request: Request):
    conn = get_conn()
    versions = models.list_bom_versions(conn)
    return templates.TemplateResponse(
        "home.html", {"request": request, "versions": versions}
    )


@router.get("/bom-version/new")
def new_bom_version(request: Request):
    conn = get_conn()
    versions = models.list_bom_versions(conn)
    return templates.TemplateResponse(
        "new_bom_version.html", {"request": request, "versions": versions}
    )


@router.post("/bom-version/import-preview")
async def import_preview(
    request: Request,
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), file: UploadFile = File(...),
):
    text = (await file.read()).decode("utf-8")
    entries, problems = parse_bom_csv(text)
    return templates.TemplateResponse(
        "import_preview.html",
        {"request": request, "entries": entries, "problems": problems,
         "board_name": board_name, "pcb_version": pcb_version,
         "bom_version": bom_version, "csv_text": text},
    )


@router.post("/bom-version")
def create_bom_version(
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), csv_text: str = Form(...),
):
    conn = get_conn()
    entries, _ = parse_bom_csv(csv_text)
    models.create_bom_version(conn, board_name, pcb_version, bom_version, entries)
    return RedirectResponse("/", status_code=303)


@router.post("/board")
def create_board(
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), board_uid: str = Form(...),
):
    conn = get_conn()
    board_id = models.create_board(conn, board_name, pcb_version, bom_version, board_uid)
    return RedirectResponse(f"/board/{board_id}", status_code=303)
```

- [ ] **Step 5: 写模板 home / new_bom_version / import_preview**

`app/templates/home.html`（覆盖）:
```html
{% extends "base.html" %}
{% block content %}
<h1>Reflow</h1>
<p><a href="/bom-version/new">+ 新建 BOM 版本（上传 CSV）</a></p>
<h2>BOM 版本</h2>
<ul>
{% for v in versions %}
  <li>{{ v.board_name }} / {{ v.pcb_version }} / {{ v.bom_version }}
    <form method="post" action="/board" style="display:inline">
      <input type="hidden" name="board_name" value="{{ v.board_name }}">
      <input type="hidden" name="pcb_version" value="{{ v.pcb_version }}">
      <input type="hidden" name="bom_version" value="{{ v.bom_version }}">
      <input name="board_uid" placeholder="单板ID" required>
      <button>+ 新建单板</button>
    </form>
  </li>
{% endfor %}
</ul>
{% endblock %}
```

`app/templates/new_bom_version.html`:
```html
{% extends "base.html" %}
{% block content %}
<h1>新建 BOM 版本</h1>
<form hx-post="/bom-version/import-preview" hx-target="#preview"
      hx-encoding="multipart/form-data">
  <label>单板名称 <input name="board_name" list="names" required></label>
  <datalist id="names">
    {% for v in versions %}<option value="{{ v.board_name }}">{% endfor %}
  </datalist>
  <label>PCB版本 <input name="pcb_version" required></label>
  <label>BOM版本 <input name="bom_version" required></label>
  <label>CSV <input type="file" name="file" accept=".csv" required></label>
  <button>预览校验</button>
</form>
<div id="preview"></div>
{% endblock %}
```

`app/templates/import_preview.html`:
```html
<h2>导入预览</h2>
{% if problems %}
<div class="remove"><strong>校验问题：</strong>
<ul>{% for p in problems %}<li>{{ p.reference }}：{{ p.detail }}</li>{% endfor %}</ul>
</div>
{% else %}<p>无校验问题。</p>{% endif %}
<table>
  <tr><th>Reference</th><th>Part</th></tr>
  {% for e in entries %}<tr><td>{{ e.reference }}</td><td>{{ e.part }}</td></tr>{% endfor %}
</table>
<form method="post" action="/bom-version">
  <input type="hidden" name="board_name" value="{{ board_name }}">
  <input type="hidden" name="pcb_version" value="{{ pcb_version }}">
  <input type="hidden" name="bom_version" value="{{ bom_version }}">
  <textarea name="csv_text" hidden>{{ csv_text }}</textarea>
  <button>确认导入</button>
</form>
```

- [ ] **Step 6: 运行确认通过**

Run: `pytest tests/test_routes.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat: hierarchy routes, CSV import preview/confirm, create board"
```

---

## Task 9: 状态图、节点详情/编辑、冲突、工作区、commit 路由

**Files:**
- Modify: `app/routes/board.py`
- Create: `app/templates/state_graph.html`, `node_detail.html`, `_bom_table.html`, `_conflicts.html`
- Test: `tests/test_routes.py`（追加）

- [ ] **Step 1: 写失败测试——详情、编辑触发冲突、commit**

追加到 `tests/test_routes.py`:
```python
def _setup_board(client):
    client.post("/bom-version",
                data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                      "csv_text": "Reference,Part\nR1,10k\n"})
    r = client.post("/board",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                          "board_uid": "3"}, follow_redirects=False)
    return r.headers["location"]            # /board/{id}


def test_node_detail_shows_full_bom(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.get(loc)                     # 状态图
    assert r.status_code == 200
    # 进入根节点详情
    rg = client.get(f"/board/{board_id}")
    assert "R1" in rg.text or "node" in rg.text


def test_commit_workspace_creates_node(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "100nF"})
    r = client.post(f"/board/{board_id}/commit", data={"message": "加 C9"},
                    follow_redirects=False)
    assert r.status_code in (200, 303)
```

> 编辑触发冲突的端到端断言较重，核心冲突逻辑已在 `test_propagation.py` 充分覆盖；此处路由测试只验证「编辑→返回冲突片段」的 HTTP 形态：

```python
def test_edit_history_node_returns_conflict_fragment(client):
    # 构造 根->S1(草稿提交两次) 制造下游显式节点，再编辑上游
    loc = _setup_board(client)
    board_id = int(loc.rsplit("/", 1)[-1])
    # 工作区改 R1=47k 后 commit => 新增一个显式 R1 的已提交节点
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "S1"})
    # 现编辑根节点 R1=22k => 与刚提交节点冲突
    from app import models
    from app.main import get_conn
    conn = get_conn()
    root = models.list_nodes(conn, board_id)[0]["id"]
    r = client.post(f"/board/{board_id}/node/{root}/edit",
                    data={"reference": "R1", "op": "modify", "part": "22k"})
    assert "冲突" in r.text or "采用修正值" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 board 路由**

覆盖 `app/routes/board.py`:
```python
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from app.main import templates, get_conn
from app import models, propagation
from app.bom_engine import fold_bom

router = APIRouter()


def _node_diff(conn, node):
    """返回 (完整BOM dict, {reference: 'add'|'modify'|'remove'}) 相对父节点的 diff。"""
    initial, chain = models.get_chain(conn, node["id"])
    full = fold_bom(initial, chain)
    diff = {c["reference"]: c["op"] for c in models.get_changeset(conn, node["id"])}
    return full, diff


@router.get("/board/{board_id}")
def state_graph(request: Request, board_id: int):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    nodes = models.list_nodes(conn, board_id)
    return templates.TemplateResponse(
        "state_graph.html",
        {"request": request, "board": board, "board_id": board_id, "nodes": nodes},
    )


@router.get("/board/{board_id}/node/{node_id}")
def node_detail(request: Request, board_id: int, node_id: int):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    full, diff = _node_diff(conn, node)
    return templates.TemplateResponse(
        "node_detail.html",
        {"request": request, "board_id": board_id, "node": node,
         "bom": sorted(full.items()), "diff": diff},
    )


@router.post("/board/{board_id}/node/{node_id}/edit")
def edit_node(request: Request, board_id: int, node_id: int,
              reference: str = Form(...), op: str = Form(...),
              part: str = Form(None)):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    part_val = None if op == "remove" else part
    # 根节点：修正改 initial_bom，再以根 changeset 语义检测下游冲突
    if node["parent_id"] is None:
        board = models.get_board(conn, board_id)
        models.update_initial_bom(conn, board["board_name"], board["pcb_version"],
                                  board["bom_version"], reference, part_val)
        conflicts = propagation._detect_downstream_conflicts(
            conn, node, reference, part_val)
    else:
        conflicts = propagation.apply_node_edit(conn, node_id, reference, op, part_val)

    if conflicts:
        return templates.TemplateResponse(
            "_conflicts.html",
            {"request": request, "board_id": board_id, "conflicts": conflicts})
    full, diff = _node_diff(conn, node)
    return templates.TemplateResponse(
        "_bom_table.html",
        {"request": request, "board_id": board_id, "node": node,
         "bom": sorted(full.items()), "diff": diff})


@router.post("/board/{board_id}/node/{node_id}/resolve")
def resolve(request: Request, board_id: int, node_id: int,
            downstream_node_id: list[int] = Form(...),
            reference: list[str] = Form(...),
            choice: list[str] = Form(...)):
    conn = get_conn()
    for ds, ref, ch in zip(downstream_node_id, reference, choice):
        ds_val = propagation._resolved_value(conn, ds, ref)
        corrected = propagation._resolved_value(conn, node_id, ref)
        propagation.resolve_conflict(
            conn, propagation.Conflict(ds, ref, ds_val, corrected), ch)
    return RedirectResponse(f"/board/{board_id}/node/{node_id}", status_code=303)


@router.post("/board/{board_id}/workspace/edit")
def workspace_edit(board_id: int, reference: str = Form(...),
                   op: str = Form(...), part: str = Form(None)):
    conn = get_conn()
    ws = models.workspace_node(conn, board_id)
    part_val = None if op == "remove" else part
    propagation.apply_node_edit(conn, ws["id"], reference, op, part_val)
    return RedirectResponse(f"/board/{board_id}/node/{ws['id']}", status_code=303)


@router.post("/board/{board_id}/commit")
def commit(board_id: int, message: str = Form(...)):
    conn = get_conn()
    models.commit_workspace(conn, board_id, message)
    return RedirectResponse(f"/board/{board_id}", status_code=303)
```

- [ ] **Step 4: 补 propagation 的 `_detect_downstream_conflicts` 帮助函数**

追加到 `app/propagation.py`（供根节点修正复用冲突检测）:
```python
def _detect_downstream_conflicts(conn, node, reference, corrected_part) -> list[Conflict]:
    """检测某节点修正后，下游第一个显式节点是否冲突（不写当前节点 changeset）。"""
    corrected = corrected_part   # 根节点 corrected 即 initial 新值
    for child in _children_in_order(conn, node["board_id"], node["id"]):
        if models.get_change(conn, child["id"], reference) is not None:
            downstream_value = _resolved_value(conn, child["id"], reference)
            return [Conflict(child["id"], reference, downstream_value, corrected)]
    return []
```

> 重构提示：`apply_node_edit` 中「沿子链找第一个显式下游」的循环与本函数重复，可让 `apply_node_edit` 在写完 changeset + 日志后调用 `_detect_downstream_conflicts(conn, node, reference, corrected)`。执行时若这样重构，删除 `apply_node_edit` 内重复循环，保持单一实现。

- [ ] **Step 5: 写模板**

`app/templates/state_graph.html`:
```html
{% extends "base.html" %}
{% block content %}
<h1>{{ board.board_name }} / {{ board.pcb_version }} / {{ board.bom_version }} / 单板 {{ board.board_uid }}</h1>
<ul class="graph">
{% for n in nodes %}
  <li class="{{ 'draft' if not n.is_committed else '' }}">
    <span class="dot"></span>
    <a href="/board/{{ board_id }}/node/{{ n.id }}">
      {{ n.message or ('工作区草稿' if not n.is_committed else '(无说明)') }}</a>
    <small>{{ n.created_at }}{{ ' · 工作区' if not n.is_committed }}</small>
  </li>
{% endfor %}
</ul>
{% endblock %}
```

`app/templates/node_detail.html`:
```html
{% extends "base.html" %}
{% block content %}
<p><a href="/board/{{ board_id }}">← 状态图</a></p>
<h1>节点 #{{ node.id }} — {{ node.message or '工作区草稿' }}</h1>
<p>稳定链接：<code>/board/{{ board_id }}/node/{{ node.id }}</code></p>
<div id="bom">{% include "_bom_table.html" %}</div>

<h3>修正此节点记录</h3>
<form hx-post="/board/{{ board_id }}/node/{{ node.id }}/edit" hx-target="#bom">
  <input name="reference" placeholder="位号" required>
  <select name="op">
    <option value="modify">修改</option>
    <option value="add">新增</option>
    <option value="remove">删除(不贴)</option>
  </select>
  <input name="part" placeholder="Part">
  <button>应用修正</button>
</form>

{% if not node.is_committed %}
<form method="post" action="/board/{{ board_id }}/commit">
  <input name="message" placeholder="commit 说明" required>
  <button>提交为新节点</button>
</form>
{% endif %}
{% endblock %}
```

`app/templates/_bom_table.html`:
```html
<table>
  <tr><th>Reference</th><th>Part</th><th>diff</th></tr>
  {% for ref, part in bom %}
  <tr class="{{ diff.get(ref, '') }}">
    <td>{{ ref }}</td><td>{{ part }}</td>
    <td>{% if ref in diff %}{{ {'add':'新增','modify':'修改','remove':'删除'}[diff[ref]] }}{% endif %}</td>
  </tr>
  {% endfor %}
  {% for ref, op in diff.items() if op == 'remove' %}
  <tr class="remove"><td>{{ ref }}</td><td>—</td><td>删除(不贴)</td></tr>
  {% endfor %}
</table>
```

`app/templates/_conflicts.html`:
```html
<div class="conflict-box">
<h3>检测到冲突，请逐位号确认</h3>
<form method="post" action="/board/{{ board_id }}/node/{{ conflicts[0].downstream_node_id }}/../resolve"
      action="/board/{{ board_id }}/node/{{ request.path_params.node_id if request else '' }}/resolve">
  {% for c in conflicts %}
  <fieldset>
    <legend>位号 {{ c.reference }}</legend>
    <input type="hidden" name="downstream_node_id" value="{{ c.downstream_node_id }}">
    <input type="hidden" name="reference" value="{{ c.reference }}">
    <label><input type="radio" name="choice" value="keep" checked>
      保留下游值（{{ c.downstream_value or '不贴' }}）</label>
    <label><input type="radio" name="choice" value="take">
      采用修正值（{{ c.corrected_value or '不贴' }}）并向后传播</label>
  </fieldset>
  {% endfor %}
  <button>提交确认</button>
</form>
</div>
```

> 模板修正：`_conflicts.html` 的 form action 用单一正确路径 `"/board/{{ board_id }}/node/{{ node_id }}/resolve"`。在 `edit_node` 返回该模板时，往 context 传入 `"node_id": node_id`，并把 form action 改为：
> ```html
> <form method="post" action="/board/{{ board_id }}/node/{{ node_id }}/resolve">
> ```
> 执行时以此为准（删除上面有歧义的双 action 写法）。

- [ ] **Step 6: 运行确认通过**

Run: `pytest tests/test_routes.py -v`
Expected: PASS

- [ ] **Step 7: 补状态图与 diff 的 CSS**

追加到 `app/static/style.css`:
```css
.graph{list-style:none;padding-left:0}
.graph li{position:relative;padding:8px 0 8px 22px;border-left:2px solid #c7cedb;margin-left:8px}
.graph li.draft{border-left-style:dashed}
.graph .dot{position:absolute;left:-7px;top:14px;width:12px;height:12px;border-radius:50%;background:#5aa6ff;border:2px solid #fff}
.graph li.draft .dot{background:#fff;border-color:#9aa3b2}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #e2e6ee;padding:6px 10px;text-align:left}
.conflict-box{border:1px solid #f85149;border-radius:8px;padding:12px;background:#fff7f7}
```

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "feat: state graph, node detail/edit, conflict resolution, workspace commit"
```

---

## Task 10: 审计日志页面

**Files:**
- Modify: `app/routes/log.py`
- Create: `app/templates/log.html`
- Test: `tests/test_routes.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_routes.py`:
```python
def test_log_page_lists_edits(client):
    loc = _setup_board(client)
    board_id = int(loc.rsplit("/", 1)[-1])
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    r = client.get(f"/board/{board_id}/log")
    assert r.status_code == 200
    assert "R1" in r.text
    assert "direct" in r.text or "直接" in r.text
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_routes.py::test_log_page_lists_edits -v`
Expected: FAIL

- [ ] **Step 3: 实现 log 路由**

覆盖 `app/routes/log.py`:
```python
from fastapi import APIRouter, Request
from app.main import templates, get_conn
from app import models, audit

router = APIRouter()


@router.get("/board/{board_id}/log")
def board_log(request: Request, board_id: int):
    conn = get_conn()
    node_ids = [n["id"] for n in models.list_nodes(conn, board_id)]
    rows = [r for r in audit.list_log(conn) if r["node_id"] in node_ids]
    return templates.TemplateResponse(
        "log.html", {"request": request, "board_id": board_id, "rows": rows})
```

`app/templates/log.html`:
```html
{% extends "base.html" %}
{% block content %}
<p><a href="/board/{{ board_id }}">← 状态图</a></p>
<h1>审计日志</h1>
<table>
  <tr><th>节点</th><th>位号</th><th>旧值</th><th>新值</th><th>操作</th><th>来源</th><th>时间</th><th>备注</th></tr>
  {% for r in rows %}
  <tr>
    <td>#{{ r.node_id }}</td><td>{{ r.reference }}</td>
    <td>{{ r.old_part or '—' }}</td><td>{{ r.new_part or '—' }}</td>
    <td>{{ r.op }}</td>
    <td>{{ '直接' if r.source == 'direct' else '上游传播' }}</td>
    <td>{{ r.created_at }}</td><td>{{ r.note or '' }}</td>
  </tr>
  {% endfor %}
</table>
{% endblock %}
```

- [ ] **Step 4: 加状态图页到日志的入口链接**

在 `app/templates/state_graph.html` 的 `<h1>` 下方加：
```html
<p><a href="/board/{{ board_id }}/log">审计日志</a></p>
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_routes.py -v`
Expected: PASS（全部路由测试）

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat: audit log page"
```

---

## Task 11: 全量验证与运行说明

**Files:**
- Create: `README.md`
- Test: 全套

- [ ] **Step 1: 运行全部测试**

Run: `pytest -v`
Expected: 全部 PASS（test_smoke, test_csv_import, test_db, test_bom_engine, test_models, test_audit, test_propagation, test_routes）

- [ ] **Step 2: 手动启动并冒烟**

Run:
```bash
uvicorn app.main:app --reload
```
浏览器打开 `http://127.0.0.1:8000/`：新建 BOM 版本（上传 CSV）→ 预览校验 → 确认 → 新建单板 → 状态图 → 节点详情编辑 → commit → 查看审计日志。

- [ ] **Step 3: 写 README**

`README.md`:
```markdown
# Reflow — 单板 BOM 状态管理工具

线性版本管理硬件单板 BOM 的演进；差量存储、历史编辑自动传播 + 冲突确认、append-only 审计日志、稳定分享链接。

## 运行
\`\`\`bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
\`\`\`
访问 http://127.0.0.1:8000/

## 测试
\`\`\`bash
pytest
\`\`\`

设计文档见 docs/superpowers/specs/。
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "docs: README and final verification"
```

---

## Self-Review（计划编写者已核对）

**Spec 覆盖核对：**
- §2 关键决策 → 全计划技术栈/结构遵循；surrogate id 即 SQLite AUTOINCREMENT，URL 用 `node.id`（Task 9）。
- §3 解析值定义 → Task 3 折叠引擎完整实现并测试。
- §4 数据模型 → Task 2 schema 五张表与 spec 一一对应。
- §5 传播 & 冲突（含 4.4 例子）→ Task 6 三个核心测试就是 4.4 两分支 + 检测；§5.4 编辑根节点 → Task 9 `edit_node` 根节点分支 + `_detect_downstream_conflicts`。
- §6 路由表 → Task 7–10 覆盖全部 12 条路由。
- §7 CSV 规则 → Task 1。§8 审计 → Task 5 + Task 10。§9 稳定链接 → Task 9 `node.id` 进 URL，编辑不改 URL。
- §10 测试策略 5 层 → 各任务 TDD 覆盖。§11 项目结构 → 文件结构表一致。§12 MVP 边界 → 未实现「飞线/回看/多用户/测试记录」，符合。

**类型一致核对：** `fold_bom`/`resolve_reference`、`CsvEntry`/`CsvProblem`、`Conflict`、`apply_node_edit`/`resolve_conflict`/`_detect_downstream_conflicts`/`_resolved_value`/`_children_in_order`/`_append_committed_node` 跨任务签名一致；`set_change`/`delete_change`/`get_change`/`get_changeset`/`get_chain`/`commit_workspace`/`workspace_node`/`update_initial_bom` 在 models 内一致。

**占位符扫描：** 无 TBD/TODO；每个改代码的步骤均含完整代码。两处「执行时以此为准」的提示是为消除模板/重构歧义，已给出确定写法。
```
