# 工作区从 CSV 导入修改项 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在工作区草稿页上传一份 CSV（Reference / Part / 可选 OP 三列），预览校验通过后把其中每行批量写进草稿的 changeset。

**Architecture:** 两层。纯逻辑层在 `app/csv_import.py` 新增 `parse_change_csv`（解析三列、拆分逗号合并位号、查 CSV 内重复）与 `plan_changes`（op 推断 + 逐条复用 `validation.validate_edit` 校验），二者零 Web/DB 依赖，是测试重点。路由层在 `app/routes/board.py` 加两个薄路由：`import/preview` 渲染预览片段并把解析结果序列化成隐藏 JSON，`import` 收 JSON 后**重新校验**、全对才逐条 `propagation.apply_node_edit`。

**Tech Stack:** Python 3 / FastAPI / Starlette 1.2.1 / Jinja2 / HTMX / pytest。依赖已装在 `.venv`。

## Global Constraints

- 设计 spec：`docs/superpowers/specs/2026-07-08-workspace-csv-import-design.md`。任务要求隐含包含 spec 全文。
- 全部沟通、代码注释、docstring、UI 文案、错误消息一律**中文**。
- TDD：每个任务先写失败测试，跑一遍确认失败，再写最小实现。
- 运行测试：`. .venv/bin/activate && pytest`。当前基线 222 passed，改完不得有回归。
- **入口只给工作区草稿**（`node["is_committed"] == 0`）。草稿挂在链末、无子节点，故 `apply_node_edit` 必返回空冲突列表，本流程不涉及冲突弹窗。
- **CSV 内位号重复判为问题行**，不做「后者覆盖前者」。因此 op 推断与逐条校验都对**静态的**折叠 BOM 跑，不维护逐行模拟态。
- 应用是**全有或全无**：存在任一问题行就一条都不写库。
- 撞车（CSV 位号在草稿已有修改）以 CSV 为准直接覆盖，预览里不特别标记。
- 改前端前必读 `docs/前端风格指南.md`。颜色只用 `style.css` 里的 CSS 变量。本计划刻意复用已有的 `.change-row`、`.problem-list`、`.flash`、`details.panel`，**不新增任何 CSS**。
- `TemplateResponse` 必须用新签名 `templates.TemplateResponse(request, "name.html", {ctx})`，`request` 是第一个位置参数，context 里不要放 `"request"` 键。
- 模板里向 `hx-vals` 传值一律 `|tojson` 且属性用单引号。

## File Structure

| 文件 | 职责 |
|---|---|
| `app/csv_import.py`（改） | 新增 `ChangeEntry`、`PlannedChange`、`parse_change_csv`、`plan_changes`。已有的 `parse_bom_csv` 不动。 |
| `app/routes/board.py`（改） | 新增 `_read_upload`、`_import_draft`、两个路由 |
| `app/templates/_import_preview.html`（新） | 预览片段：统计 + 修改清单 + 问题清单 + 隐藏 JSON + 应用按钮 |
| `app/templates/node_detail.html`（改） | 草稿分支右侧栏加导入面板入口 |
| `tests/test_csv_change_import.py`（新） | Task 1、2 的纯逻辑测试 |
| `tests/test_import_routes.py`（新） | Task 3、4 的路由与模板测试 |

---

### Task 1: 解析三列 CSV（`parse_change_csv`）

**Files:**
- Modify: `app/csv_import.py`
- Test: `tests/test_csv_change_import.py`（新建）

**Interfaces:**
- Consumes: 已有的 `CsvProblem(kind, reference, detail)` NamedTuple。
- Produces:
  - `ChangeEntry(reference: str, op: str | None, part: str)` —— `op` 为 `None` 表示待推断；`part` 已 strip，可能是 `""`。
  - `parse_change_csv(text: str) -> tuple[list[ChangeEntry], list[CsvProblem]]` —— 缺 Reference 或 Part 列时抛 `ValueError`。problem 的 `kind` 取值：`"empty_reference"` / `"duplicate"` / `"bad_op"`。

注意：**Part 为空不在本函数里判**。空 Part 的合法性取决于 op（`remove` 行允许空），而 op 可能要靠折叠 BOM 推断，所以留给 Task 2 的 `plan_changes` 通过 `validate_edit` 统一处理。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_csv_change_import.py`：

```python
"""issue #108：工作区从 CSV 导入修改项 —— 纯逻辑层。"""
import pytest

from app.csv_import import ChangeEntry, parse_change_csv


def test_headers_are_case_insensitive():
    """issue 原文的表头就是大写 PART，必须能认。"""
    csv = "reference,PART\nR1,10k\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k")]


def test_header_whitespace_tolerated():
    csv = " Reference , Part \nR1,10k\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k")]


def test_missing_required_column_raises():
    with pytest.raises(ValueError):
        parse_change_csv("Reference,Value\nR1,10k\n")


def test_strips_utf8_bom_and_handles_crlf():
    csv = "﻿Reference,Part\r\nR1,10k\r\nC1,100nF\r\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k"),
                       ChangeEntry("C1", None, "100nF")]


def test_op_column_read_case_insensitively():
    csv = "Reference,Part,OP\nR1,10k,ADD\nC1,,Remove\nR2,22k,modify\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", "add", "10k"),
                       ChangeEntry("C1", "remove", ""),
                       ChangeEntry("R2", "modify", "22k")]


def test_blank_op_cell_falls_back_to_inference():
    """有 OP 列但某行留空 → 该行 op=None，交给 plan_changes 推断。"""
    csv = "Reference,Part,Op\nR1,10k,\nC1,,remove\n"
    entries, _ = parse_change_csv(csv)
    assert entries[0] == ChangeEntry("R1", None, "10k")
    assert entries[1] == ChangeEntry("C1", "remove", "")


def test_invalid_op_value_is_a_problem():
    csv = "Reference,Part,OP\nR1,10k,delete\n"
    entries, problems = parse_change_csv(csv)
    assert entries == []
    assert len(problems) == 1
    assert problems[0].kind == "bad_op"
    assert problems[0].reference == "R1"
    assert "delete" in problems[0].detail


def test_splits_comma_merged_references_sharing_one_row():
    csv = 'Reference,Part,OP\n"R67, R24",1kR,modify\n'
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R67", "modify", "1kR"),
                       ChangeEntry("R24", "modify", "1kR")]


def test_trailing_comma_in_merged_cell_ignored():
    csv = 'Reference,Part\n"R1,",10k\n'
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k")]


def test_empty_reference_is_a_problem():
    csv = "Reference,Part\n,10k\n"
    entries, problems = parse_change_csv(csv)
    assert entries == []
    assert [p.kind for p in problems] == ["empty_reference"]


def test_duplicate_reference_within_csv_is_a_problem_first_wins():
    csv = "Reference,Part\nR1,10k\nR1,22k\n"
    entries, problems = parse_change_csv(csv)
    assert entries == [ChangeEntry("R1", None, "10k")]
    assert [p.kind for p in problems] == ["duplicate"]
    assert problems[0].reference == "R1"


def test_duplicate_detected_across_merged_cells():
    csv = 'Reference,Part\n"R1,R2",10k\nR2,22k\n'
    entries, problems = parse_change_csv(csv)
    assert len(entries) == 2
    assert [p.kind for p in problems] == ["duplicate"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/test_csv_change_import.py -v
```

Expected: 收集阶段就 FAIL —— `ImportError: cannot import name 'ChangeEntry' from 'app.csv_import'`。

- [ ] **Step 3: 写最小实现**

在 `app/csv_import.py` 末尾追加（`CsvEntry` / `CsvProblem` / `parse_bom_csv` 保持原样不动）：

```python
class ChangeEntry(NamedTuple):
    """CSV 里的一行修改。op 为 None 表示待按折叠 BOM 推断；part 可能是空串。"""
    reference: str
    op: str | None
    part: str


_VALID_OPS = ("add", "modify", "remove")


def parse_change_csv(text: str) -> tuple[list[ChangeEntry], list[CsvProblem]]:
    """解析「修改清单 CSV」：Reference / Part 两列必需，OP 列可选。

    表头大小写不敏感（issue 原文的表头是 PART）。逗号合并位号拆成多条。
    CSV 内位号重复视为问题行（首条获胜），不做后者覆盖前者。
    Part 是否可空取决于 op，故不在此判断，留给 plan_changes。
    """
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))
    fieldmap = {(name or "").strip().lower(): name for name in (reader.fieldnames or [])}
    ref_col = fieldmap.get("reference")
    part_col = fieldmap.get("part")
    op_col = fieldmap.get("op")
    if ref_col is None or part_col is None:
        raise ValueError("CSV 必须包含 Reference 和 Part 两列")

    entries: list[ChangeEntry] = []
    problems: list[CsvProblem] = []
    seen: set[str] = set()

    for row in reader:
        raw_refs = row.get(ref_col) or ""
        part = (row.get(part_col) or "").strip()
        raw_op = (row.get(op_col) or "").strip() if op_col else ""
        op = raw_op.lower()
        for ref in raw_refs.split(","):
            ref = ref.strip()
            if ref == "":
                if raw_refs.strip() != "":
                    continue  # 合并格内的空段（如尾随逗号）忽略
                problems.append(CsvProblem("empty_reference", "", "位号为空"))
                continue
            if ref in seen:
                problems.append(CsvProblem("duplicate", ref, "位号在 CSV 中重复"))
                continue
            seen.add(ref)
            if op and op not in _VALID_OPS:
                problems.append(
                    CsvProblem("bad_op", ref,
                               f"OP 值无效：{raw_op}（只能是 add / modify / remove）"))
                continue
            entries.append(ChangeEntry(ref, op or None, part))

    return entries, problems
```

- [ ] **Step 4: 跑测试确认通过**

```bash
. .venv/bin/activate && pytest tests/test_csv_change_import.py -v
```

Expected: 12 passed。

- [ ] **Step 5: 提交**

```bash
git add app/csv_import.py tests/test_csv_change_import.py
git commit -m "feat: parse_change_csv 解析修改清单 CSV（#108）"
```

---

### Task 2: op 推断与逐条校验（`plan_changes`）

**Files:**
- Modify: `app/csv_import.py`
- Test: `tests/test_csv_change_import.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `ChangeEntry`；已有的 `app.validation.validate_edit(full_bom, reference, op, part) -> str | None`（返回中文错误消息或 None）。
- Produces:
  - `PlannedChange(reference: str, op: str, part: str | None)` —— `op` 已确定；`remove` 时 `part` 为 `None`。
  - `plan_changes(full_bom: dict[str, str], entries: list[ChangeEntry]) -> tuple[list[PlannedChange], list[CsvProblem]]` —— problem 的 `kind` 恒为 `"invalid"`，`detail` 直接取自 `validate_edit` 的中文消息。

推断规则：`op is None` 时，位号在 `full_bom` 里 → `"modify"`，否则 → `"add"`。推断永远得不出 `remove`。

`full_bom` 全程**不被修改**（静态）。因为 CSV 内位号不重复，逐条独立校验是安全的。

Part 为空的处理天然落在 `validate_edit` 上：`add` 空 Part → 「新增位号必须填写 Part」；`modify` 空 Part → 「修改必须填写新 Part 值」；`remove` 的 part 传 `None`，`validate_edit` 忽略它。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_csv_change_import.py` 末尾（同时更新文件顶部的 import 行为
`from app.csv_import import ChangeEntry, PlannedChange, parse_change_csv, plan_changes`）：

```python
BOM = {"R1": "10k", "C1": "100nF"}


def test_infers_modify_for_existing_reference():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", None, "47k")])
    assert problems == []
    assert changes == [PlannedChange("R1", "modify", "47k")]


def test_infers_add_for_new_reference():
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", None, "1uF")])
    assert problems == []
    assert changes == [PlannedChange("R9", "add", "1uF")]


def test_explicit_op_wins_over_inference():
    """位号不存在但显式写 modify → 走 modify，于是校验失败。"""
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", "modify", "1uF")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]
    assert "不存在" in problems[0].detail


def test_remove_drops_part_value():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", "remove", "随便写的")])
    assert problems == []
    assert changes == [PlannedChange("R1", "remove", None)]


def test_remove_allows_empty_part():
    changes, problems = plan_changes(BOM, [ChangeEntry("C1", "remove", "")])
    assert problems == []
    assert changes == [PlannedChange("C1", "remove", None)]


def test_remove_of_unplaced_reference_is_a_problem():
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", "remove", "")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]


def test_add_of_existing_reference_is_a_problem():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", "add", "22k")])
    assert changes == []
    assert problems[0].reference == "R1"
    assert "已存在" in problems[0].detail


def test_empty_part_is_a_problem_for_add():
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", "add", "")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]


def test_empty_part_is_a_problem_for_inferred_modify():
    """无 OP 列时空 Part 推断不出「不贴」，必须报错。"""
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", None, "")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]


def test_modify_to_same_value_is_a_harmless_noop_not_a_problem():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", None, "10k")])
    assert problems == []
    assert changes == [PlannedChange("R1", "modify", "10k")]


def test_good_rows_and_bad_rows_both_reported():
    """问题行不阻止其余行进入 changes；是否应用由调用方按「全对才应用」决定。"""
    changes, problems = plan_changes(BOM, [
        ChangeEntry("R1", None, "47k"),
        ChangeEntry("R1x", "modify", "1k"),
        ChangeEntry("C1", "remove", ""),
    ])
    assert changes == [PlannedChange("R1", "modify", "47k"),
                       PlannedChange("C1", "remove", None)]
    assert [p.reference for p in problems] == ["R1x"]


def test_full_bom_is_not_mutated():
    bom = dict(BOM)
    plan_changes(bom, [ChangeEntry("R1", "remove", ""), ChangeEntry("R9", "add", "1k")])
    assert bom == BOM
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/test_csv_change_import.py -v
```

Expected: 收集阶段 FAIL —— `ImportError: cannot import name 'PlannedChange'`。

- [ ] **Step 3: 写最小实现**

在 `app/csv_import.py` 顶部的 import 区加一行：

```python
from app.validation import validate_edit
```

在文件末尾追加：

```python
class PlannedChange(NamedTuple):
    """一条已确定 op 且通过校验的修改。remove 时 part 为 None。"""
    reference: str
    op: str
    part: str | None


def plan_changes(
    full_bom: dict[str, str], entries: list[ChangeEntry]
) -> tuple[list[PlannedChange], list[CsvProblem]]:
    """把 CSV 条目落到某节点折叠后的 BOM 上：推断 op、逐条校验。

    op 为 None 时按位号是否已在 full_bom 中推断为 modify / add——推断永远
    得不出 remove，批量设不贴必须显式写 OP=remove。

    CSV 内位号不重复（parse_change_csv 已保证），故每条都对**静态的**
    full_bom 独立校验，不需要维护逐行模拟态。full_bom 不被修改。
    """
    changes: list[PlannedChange] = []
    problems: list[CsvProblem] = []
    for e in entries:
        op = e.op or ("modify" if e.reference in full_bom else "add")
        part = None if op == "remove" else e.part
        err = validate_edit(full_bom, e.reference, op, part)
        if err:
            problems.append(CsvProblem("invalid", e.reference, err))
            continue
        changes.append(PlannedChange(e.reference, op, part))
    return changes, problems
```

- [ ] **Step 4: 跑测试确认通过**

```bash
. .venv/bin/activate && pytest tests/test_csv_change_import.py -v
```

Expected: 24 passed。

再跑全量确认没有把 `parse_bom_csv` 弄坏、且新的 `csv_import → validation` 依赖没有形成循环导入：

```bash
. .venv/bin/activate && pytest
```

Expected: 246 passed（222 基线 + 24 新增）。

- [ ] **Step 5: 提交**

```bash
git add app/csv_import.py tests/test_csv_change_import.py
git commit -m "feat: plan_changes 推断 op 并逐条校验（#108）"
```

---

### Task 3: 预览与应用两个路由

**Files:**
- Modify: `app/routes/board.py`
- Create: `app/templates/_import_preview.html`
- Test: `tests/test_import_routes.py`（新建）

**Interfaces:**
- Consumes: Task 1、2 的 `ChangeEntry` / `PlannedChange` / `parse_change_csv` / `plan_changes`；已有的 `models.get_node` / `models.get_chain` / `models.workspace_node`、`bom_engine.fold_bom`、`propagation.apply_node_edit`。
- Produces:
  - `POST /board/{board_id}/node/{node_id}/import/preview`，multipart 字段名 `file` → 返回 `_import_preview.html` 片段。
  - `POST /board/{board_id}/node/{node_id}/import`，表单字段 `changes`（JSON 数组字符串）→ 成功返回 204 + `HX-Redirect`；失败返回 `_form_error.html` + `HX-Retarget: #import-error`。
  - 预览片段里应用按钮的目标容器 id 为 `#import-error`，由片段自身提供。

节点不是草稿（`is_committed == 1`）时两个路由都拒绝，不依赖前端不渲染入口。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_import_routes.py`：

```python
"""issue #108：工作区从 CSV 导入修改项 —— 路由层。"""
import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import models
from app.main import get_conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client, uid="SN1"):
    """建一块单板，初始 BOM 为 R1=10k、C1=100nF。返回 board_id。"""
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": uid},
                    files={"file": ("bom.csv",
                                    b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _workspace_id(board_id):
    return models.workspace_node(get_conn(), board_id)["id"]


def _changeset(node_id):
    """草稿 changeset：{reference: (op, part)}。"""
    return {c["reference"]: (c["op"], c["part"])
            for c in models.get_changeset(get_conn(), node_id)}


def _preview(client, board_id, node_id, csv_bytes):
    return client.post(f"/board/{board_id}/node/{node_id}/import/preview",
                       files={"file": ("changes.csv", csv_bytes, "text/csv")})


def _changes_json(html):
    """从预览片段的 hx-vals 里抠出 changes JSON 字符串。"""
    import re
    m = re.search(r"hx-vals='(.*?)'", html)
    assert m, f"预览片段里没有 hx-vals：{html}"
    return json.loads(m.group(1))["changes"]


# ── 预览 ────────────────────────────────────────────────────────

def test_preview_shows_counts_and_does_not_write_db(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws,
                 b"Reference,PART,OP\nR1,47k,\nR9,1uF,add\nC1,,remove\n")
    assert r.status_code == 200
    assert "新增 1" in r.text and "修改 1" in r.text and "不贴 1" in r.text
    assert _changeset(ws) == {}  # 预览不写库


def test_preview_lists_problems_and_omits_apply_button(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR1,22k,add\n")
    assert r.status_code == 200
    assert "已存在" in r.text
    assert "hx-vals" not in r.text  # 有问题行 → 不给应用按钮


def test_preview_rejects_csv_without_required_columns(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Value\nR1,10k\n")
    assert r.status_code == 200
    assert "必须包含 Reference 和 Part 两列" in r.text


def test_preview_rejects_non_utf8_file(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, "Reference,Part\nR1,十k\n".encode("gbk"))
    assert r.status_code == 200
    assert "UTF-8" in r.text


def test_preview_rejected_on_committed_node(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"},
                follow_redirects=False)
    committed = [n for n in models.list_nodes(get_conn(), board_id)
                 if n["is_committed"] and n["parent_id"] is not None][-1]["id"]
    r = _preview(client, board_id, committed, b"Reference,Part\nR1,22k\n")
    assert r.status_code == 400
    assert "只有工作区草稿" in r.text


# ── 应用 ────────────────────────────────────────────────────────

def test_apply_writes_all_changes(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws,
                 b"Reference,PART,OP\nR1,47k,\nR9,1uF,add\nC1,,remove\n")
    r2 = client.post(f"/board/{board_id}/node/{ws}/import",
                     data={"changes": _changes_json(r.text)})
    assert r2.status_code == 204
    # flash 在响应头里是 URL 编码的（响应头须 latin-1）
    assert "已导入 3 条修改" in unquote(r2.headers["HX-Redirect"])
    assert _changeset(ws) == {"R1": ("modify", "47k"),
                              "R9": ("add", "1uF"),
                              "C1": ("remove", None)}


def test_apply_overwrites_existing_draft_change(client):
    """撞车：草稿已把 R1 改成 10k 之外的值，CSV 覆盖之；草稿独有的 C1 保留。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "1k"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C1", "op": "modify", "part": "10nF"})
    r = _preview(client, board_id, ws, b"Reference,Part\nR1,47k\n")
    client.post(f"/board/{board_id}/node/{ws}/import",
                data={"changes": _changes_json(r.text)})
    assert _changeset(ws) == {"R1": ("modify", "47k"), "C1": ("modify", "10nF")}


def test_apply_revalidates_and_writes_nothing_when_stale(client):
    """预览之后草稿变了（R9 已被手工加上），此时再应用 add R9 必须整体拒绝。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR2,1k,add\nR9,1uF,add\n")
    payload = _changes_json(r.text)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R9", "op": "add", "part": "2uF"})
    r2 = client.post(f"/board/{board_id}/node/{ws}/import", data={"changes": payload})
    assert r2.status_code == 200
    assert "已存在" in r2.text
    assert r2.headers["HX-Retarget"] == "#import-error"
    # 整体拒绝：R2 没有被写进去，R9 保持手工填的值
    assert _changeset(ws) == {"R9": ("add", "2uF")}


def test_apply_rejects_empty_payload(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/import", data={"changes": "[]"})
    assert r.status_code == 200
    assert "没有可导入的修改" in r.text


def test_apply_rejected_on_committed_node(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"},
                follow_redirects=False)
    committed = [n for n in models.list_nodes(get_conn(), board_id)
                 if n["is_committed"] and n["parent_id"] is not None][-1]["id"]
    payload = json.dumps([{"reference": "C1", "op": "modify", "part": "1nF"}])
    r = client.post(f"/board/{board_id}/node/{committed}/import",
                    data={"changes": payload})
    assert r.status_code == 400
    assert "只有工作区草稿" in r.text


def test_apply_records_audit_log_per_change(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part\nR1,47k\nR9,1uF\n")
    client.post(f"/board/{board_id}/node/{ws}/import",
                data={"changes": _changes_json(r.text)})
    rows = get_conn().execute(
        "SELECT reference, source FROM audit_log WHERE node_id=?", (ws,)).fetchall()
    assert sorted(r["reference"] for r in rows) == ["R1", "R9"]
    assert {r["source"] for r in rows} == {"direct"}
```

跑之前先确认 `audit_log` 的表名与列名与最后一个用例里写的 `node_id` / `reference` / `source` 一致：

```bash
grep -i -A10 "audit_log" app/db.py
```

若列名不同，按实际列名改 `test_apply_records_audit_log_per_change`。

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/test_import_routes.py -v
```

Expected: 全部 FAIL —— 路由不存在，返回 405/404。

- [ ] **Step 3: 写实现**

先建 `app/templates/_import_preview.html`：

```html
{% if message %}
<div class="flash flash-error">✕ {{ message }}</div>
{% else %}
  {% if problems %}
  <div class="flash flash-error">✕ 发现 {{ problems|length }} 个问题，修正 CSV 后重新选择文件</div>
  <ul class="problem-list">
    {% for p in problems %}
    <li>{% if p.reference %}<code>{{ p.reference }}</code> {% endif %}{{ p.detail }}</li>
    {% endfor %}
  </ul>
  {% endif %}
  {% if changes %}
  <div class="flash flash-info">
    共 {{ changes|length }} 条修改：新增 {{ counts.add }} · 修改 {{ counts.modify }} · 不贴 {{ counts.remove }}
  </div>
  {% for c in changes %}
  <div class="change-row">
    <span><code>{{ c.reference }}</code>
      {% if c.op == 'remove' %}不贴{% elif c.op == 'add' %}新增 → {{ c.part }}{% else %}修改 → {{ c.part }}{% endif %}
    </span>
  </div>
  {% endfor %}
  {% elif not problems %}
  <div class="flash flash-warn">⚠ CSV 里没有可导入的修改</div>
  {% endif %}
{% endif %}
<div id="import-error"></div>
{% if ready %}
<button type="button" class="btn btn-outline"
        hx-post="/board/{{ board_id }}/node/{{ node_id }}/import"
        hx-vals='{{ {"changes": changes_json} | tojson }}'
        hx-target="#import-error" hx-swap="innerHTML">
  应用这 {{ changes|length }} 条修改
</button>
{% endif %}
```

然后改 `app/routes/board.py`。在文件顶部的 import 区补上：

```python
from fastapi import APIRouter, Request, Form, HTTPException, UploadFile, File
from app.csv_import import ChangeEntry, parse_change_csv, plan_changes
```

（`UploadFile` / `File` 加到已有的 `from fastapi import ...` 行里；`Request`、`Form`、`HTTPException` 已在。）

在 `workspace_edit` 路由之后追加：

```python
async def _read_upload(file) -> tuple[str, str | None]:
    """读取上传的 CSV 文本，返回 (text, error_message)。"""
    if file is None or not file.filename:
        return "", "请选择 CSV 文件"
    try:
        return (await file.read()).decode("utf-8"), None
    except UnicodeDecodeError:
        return "", "文件不是 UTF-8 编码"


def _import_draft(conn, board_id: int, node_id: int):
    """导入只允许对工作区草稿做。返回草稿节点行；非草稿返回 None。"""
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return None if node["is_committed"] else node


@router.post("/board/{board_id}/node/{node_id}/import/preview")
async def import_preview(request: Request, board_id: int, node_id: int,
                         file: UploadFile | None = File(None)):
    """解析上传的修改清单 CSV，渲染预览；不写库。有任一问题行则不给应用按钮。"""
    conn = get_conn()
    if _import_draft(conn, board_id, node_id) is None:
        return PlainTextResponse("只有工作区草稿支持导入修改", status_code=400)

    ctx = {"board_id": board_id, "node_id": node_id, "message": "",
           "changes": [], "problems": [], "ready": False,
           "counts": {"add": 0, "modify": 0, "remove": 0}, "changes_json": "[]"}

    text, err = await _read_upload(file)
    if err:
        ctx["message"] = err
        return templates.TemplateResponse(request, "_import_preview.html", ctx)
    try:
        entries, problems = parse_change_csv(text)
    except ValueError as e:
        ctx["message"] = str(e)
        return templates.TemplateResponse(request, "_import_preview.html", ctx)

    initial, chain = models.get_chain(conn, node_id)
    changes, invalid = plan_changes(fold_bom(initial, chain), entries)
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


@router.post("/board/{board_id}/node/{node_id}/import")
def import_apply(request: Request, board_id: int, node_id: int,
                 changes: str = Form("[]")):
    """应用预览过的修改清单。落库前重新校验（草稿可能已变），全有或全无。"""
    conn = get_conn()
    if _import_draft(conn, board_id, node_id) is None:
        return PlainTextResponse("只有工作区草稿支持导入修改", status_code=400)

    def _err(msg):
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": msg},
            headers={"HX-Retarget": "#import-error", "HX-Reswap": "innerHTML"})

    try:
        payload = json.loads(changes)
    except (ValueError, TypeError):
        payload = []
    if not payload:
        return _err("没有可导入的修改")

    entries = [ChangeEntry((c.get("reference") or "").strip(),
                           c.get("op"), c.get("part") or "")
               for c in payload]
    initial, chain = models.get_chain(conn, node_id)
    planned, problems = plan_changes(fold_bom(initial, chain), entries)
    if problems:
        return _err(f"草稿已变化，导入被拒绝：{problems[0].reference} {problems[0].detail}")

    for c in planned:
        propagation.apply_node_edit(conn, node_id, c.reference, c.op, c.part)

    flash = quote(f"✓ 已导入 {len(planned)} 条修改", safe="")
    return Response(status_code=204, headers={
        "HX-Redirect": f"/board/{board_id}/node/{node_id}?flash={flash}"})
```

注意：`import_apply` 里 `ChangeEntry` 的 `op` 直接取自 payload——预览已把 op 定死，`plan_changes` 会原样采用并重新校验。若 payload 被篡改成非法 op，`validate_edit` 会返回「未知操作类型」，落到问题行，整体拒绝。

`json`、`quote`、`PlainTextResponse`、`Response`、`fold_bom`、`propagation`、`models` 在 `board.py` 顶部均已导入。

- [ ] **Step 4: 跑测试确认通过**

```bash
. .venv/bin/activate && pytest tests/test_import_routes.py -v
```

Expected: 11 passed。

- [ ] **Step 5: 提交**

```bash
git add app/routes/board.py app/templates/_import_preview.html tests/test_import_routes.py
git commit -m "feat: 工作区 CSV 导入的预览与应用路由（#108）"
```

---

### Task 4: 草稿页导入面板入口

**Files:**
- Modify: `app/templates/node_detail.html`
- Test: `tests/test_import_routes.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `import/preview` 路由与 `#import-error` 容器约定。
- Produces: 草稿页右侧栏一个 `<details class="panel">`，内含 `<input name="file" type="file">`，change 即触发预览，预览片段落进 `#import-preview`。

先读 `docs/前端风格指南.md`。本任务**不新增 CSS**：`details.panel` / `.panel-title` / `.muted` / `.change-row` / `.problem-list` / `.flash` / `.btn-outline` 都已存在。注意风格指南规定 `.btn-primary` 每页至多一个——草稿页那一个已被「应用修正」占用，所以导入按钮用 `.btn-outline`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_import_routes.py` 末尾：

```python
# ── 页面入口 ────────────────────────────────────────────────────

def test_draft_page_shows_import_panel(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    html = client.get(f"/board/{board_id}/node/{ws}").text
    assert "从 CSV 导入修改" in html
    assert f'hx-post="/board/{board_id}/node/{ws}/import/preview"' in html
    assert 'id="import-preview"' in html


def test_committed_page_has_no_import_panel(client):
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"},
                follow_redirects=False)
    committed = [n for n in models.list_nodes(get_conn(), board_id)
                 if n["is_committed"] and n["parent_id"] is not None][-1]["id"]
    html = client.get(f"/board/{board_id}/node/{committed}").text
    assert "从 CSV 导入修改" not in html
```

- [ ] **Step 2: 跑测试确认失败**

```bash
. .venv/bin/activate && pytest tests/test_import_routes.py -k import_panel -v
```

Expected: `test_draft_page_shows_import_panel` FAIL（断言「从 CSV 导入修改」不在页面里）。

- [ ] **Step 3: 写实现**

在 `app/templates/node_detail.html` 里找到右侧栏 `<aside>` 中 `{% if not node.is_committed %}` 包裹的提交表单（`<form class="panel commit-box" ...>`），在它**之前**插入导入面板，使之与「添加修改」面板、「本节点修改」面板相邻：

```html
    {% if not node.is_committed %}
    <details class="panel">
      <summary>从 CSV 导入修改…</summary>
      <form class="edit-form" hx-post="/board/{{ board_id }}/node/{{ node.id }}/import/preview"
            hx-target="#import-preview" hx-swap="innerHTML"
            hx-encoding="multipart/form-data" hx-trigger="change">
        <label>修改清单 CSV
          <input name="file" type="file" accept=".csv"></label>
        <p class="muted">表头需含 Reference 与 Part；可选 OP 列（add / modify / remove）。
          不写 OP 时按位号是否已存在自动判为新增或修改。</p>
      </form>
      <div id="import-preview"></div>
    </details>
    {% endif %}
```

若 `<aside>` 里已有的 `{% if not node.is_committed %}` 块正好包着提交表单，就把上面这段插进同一个 `{% if %}` 内部、提交表单之前，而不是再开一个新的条件块。

- [ ] **Step 4: 跑测试确认通过**

```bash
. .venv/bin/activate && pytest tests/test_import_routes.py -v
```

Expected: 13 passed。

再跑全量：

```bash
. .venv/bin/activate && pytest
```

Expected: 259 passed（222 基线 + Task 1/2 的 24 + Task 3 的 11 + 本任务的 2）。若总数与实际略有出入，以「没有 FAILED」为准。

- [ ] **Step 5: 浏览器实测（两套主题都看）**

```bash
. .venv/bin/activate && uvicorn app.main:app --reload
```

在浏览器里走一遍：新建单板 → 进工作区草稿 → 展开「从 CSV 导入修改…」→ 选一个含 `Reference,PART,OP` 的 CSV → 看预览统计与清单 → 点应用 → 确认整页刷新、toast 显示「✓ 已导入 N 条修改」、右侧「本节点修改」面板出现这些条目。再传一个带问题行的 CSV，确认红色问题清单出现且没有应用按钮。

**切到夜间模式重看一遍**（`base.html` 里的主题切换），确认预览片段、问题清单、按钮在暗色下都正常。

- [ ] **Step 6: 提交**

```bash
git add app/templates/node_detail.html tests/test_import_routes.py
git commit -m "feat: 草稿页加入从 CSV 导入修改的面板（#108）"
```

---

## Self-Review

**Spec 覆盖检查：**

| Spec 要求 | 落在哪 |
|---|---|
| 入口只在工作区草稿页 | Task 3 `_import_draft`（后端硬拒） + Task 4（前端不渲染），两处都有测试 |
| 不需要冲突弹窗 | 设计前提；Task 3 直接调 `apply_node_edit` 且忽略返回值（草稿必为空列表） |
| 表头大小写不敏感、含 OP 列 | Task 1 `test_headers_are_case_insensitive`、`test_op_column_read_case_insensitively` |
| 逗号合并位号拆分 | Task 1 `test_splits_comma_merged_references_sharing_one_row` |
| OP 留空的行走推断 | Task 1 `test_blank_op_cell_falls_back_to_inference` + Task 2 推断测试 |
| 推断得不出 remove | Task 2 `test_empty_part_is_a_problem_for_inferred_modify` |
| remove 行 Part 可空、被忽略 | Task 2 `test_remove_allows_empty_part`、`test_remove_drops_part_value` |
| add/modify 空 Part 是问题行 | Task 2 两个 `test_empty_part_is_a_problem_*` |
| no-op modify 不算问题 | Task 2 `test_modify_to_same_value_is_a_harmless_noop_not_a_problem` |
| CSV 内重复判为问题行 | Task 1 两个 duplicate 测试 |
| 静态折叠 BOM、无模拟态 | Task 2 `test_full_bom_is_not_mutated` |
| 复用 validate_edit 的三类错误 | Task 2 `test_add_of_existing_reference_is_a_problem` 等三个 |
| 全对才应用、全有或全无 | Task 3 `test_apply_revalidates_and_writes_nothing_when_stale` |
| 确认时重新校验 | 同上 |
| 撞车覆盖、草稿独有修改保留 | Task 3 `test_apply_overwrites_existing_draft_change` |
| 审计日志逐条记 direct | Task 3 `test_apply_records_audit_log_per_change` |
| 预览不写库 | Task 3 `test_preview_shows_counts_and_does_not_write_db` |
| 成功后 flash 提示 | Task 3 `test_apply_writes_all_changes` 检查 `HX-Redirect` |

无遗漏。

**类型一致性：** `ChangeEntry(reference, op, part)` 与 `PlannedChange(reference, op, part)` 在 Task 1/2/3 中签名一致；`plan_changes(full_bom, entries)` 的调用点（Task 3 两处）参数顺序一致；`_import_draft` 在两个路由中同名同语义；`#import-error` 在预览模板、`import_apply` 的 `HX-Retarget`、路由测试三处一致。
