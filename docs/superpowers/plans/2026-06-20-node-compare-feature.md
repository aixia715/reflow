# 节点对比功能 + 时间统一 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在状态图上任选两个节点，对比其完整 BOM 差异并列出两节点之间发生的硬更改；同时把全站时间统一为「存 UTC、渲染浏览器本地」。

**Architecture:** 新增纯逻辑模块 `app/compare.py`（`diff_boms` / `hard_changes_between`）；薄路由 `GET /board/{board_id}/compare`，复用 `models.get_chain` + `bom_engine.fold_bom` 折叠双方 BOM；新建 `compare.html` 模板与状态图「对比模式」勾选入口。时间统一：硬更改 `occurred_at` 写入时由前端转 UTC、展示层用 `.local-dt` JS 渲染本地、一次性迁移脚本把旧值按 `Asia/Singapore` 补成 UTC。

**Tech Stack:** Python 3.12 / FastAPI / Starlette 1.2.1 / SQLite / Jinja2 / HTMX / Alpine.js / pytest / Playwright。

## Global Constraints

- 用中文：代码注释、docstring、UI 文案、错误消息均为中文。
- 纯逻辑模块（★）零 Web/DB 依赖，是测试投入重点；改动遵循 TDD（先写失败测试再实现）。
- `TemplateResponse` 用新签名 `templates.TemplateResponse(request, "name.html", {context})`——`request` 第一个位置参数，context 里**不要**放 `"request"` 键。
- 改前端（`app/templates/`、`app/static/`）前必读 `docs/前端风格指南.md`：只用设计令牌（CSS 变量）、新颜色同步夜间模式、先复用组件、两套主题都实际查看自检。
- 模板向 hx-vals/JS 传值一律 `|tojson` 且属性用单引号；htmx 事件在 Alpine 里监听加 `.camel` 修饰符。
- canonical 时间格式：`YYYY-MM-DDTHH:MM:SS+00:00`（UTC 带偏移、秒精度），与现有 `models._now()` 一致；前端转 UTC 须输出此格式（不要 `Z`、不要毫秒）。
- 新加坡时区固定 `+08:00`、无夏令时；迁移用 `timezone(timedelta(hours=8))`，不引入 zoneinfo 依赖。
- 测试基线：现有 74 passed 必须全绿。
- 运行测试前先 `. /home/tong/code/reflow/.venv/bin/activate`。

---

### Task 1: 纯逻辑 `diff_boms`（BOM 差异）

**Files:**
- Create: `app/compare.py`
- Test: `tests/test_compare.py`

**Interfaces:**
- Consumes: 无（纯函数，输入两个 `dict[str, str]`）。
- Produces: `diff_boms(left: dict[str, str], right: dict[str, str]) -> list[dict]`，返回按 `reference` 升序的行，每行 `{"reference": str, "left": str|None, "right": str|None, "kind": str}`，`kind ∈ {"add","modify","remove","same"}`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compare.py
from app.compare import diff_boms


def test_diff_add_modify_remove_same_sorted():
    left = {"R1": "10k", "R5": "10k", "D2": "LED红"}
    right = {"R1": "10k", "R5": "4.7k", "C12": "100nF"}
    rows = diff_boms(left, right)
    assert rows == [
        {"reference": "C12", "left": None, "right": "100nF", "kind": "add"},
        {"reference": "D2", "left": "LED红", "right": None, "kind": "remove"},
        {"reference": "R1", "left": "10k", "right": "10k", "kind": "same"},
        {"reference": "R5", "left": "10k", "right": "4.7k", "kind": "modify"},
    ]


def test_diff_identical_all_same():
    bom = {"R1": "10k", "C1": "100nF"}
    rows = diff_boms(bom, dict(bom))
    assert all(r["kind"] == "same" for r in rows)
    assert [r["reference"] for r in rows] == ["C1", "R1"]


def test_diff_empty_both():
    assert diff_boms({}, {}) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_compare.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.compare'`）

- [ ] **Step 3: 写最小实现**

```python
# app/compare.py
"""节点对比纯逻辑：BOM 差异、时间区间内硬更改（零 Web/DB 依赖）。"""


def diff_boms(left: dict[str, str], right: dict[str, str]) -> list[dict]:
    """对比两个折叠后的完整 BOM，返回按位号升序排列的差异行。

    kind 判定：仅右有→add；两边都有且值不同→modify；都有且相同→same；仅左有→remove。
    """
    rows = []
    for ref in sorted(set(left) | set(right)):
        lv = left.get(ref)
        rv = right.get(ref)
        if lv is None:
            kind = "add"
        elif rv is None:
            kind = "remove"
        elif lv == rv:
            kind = "same"
        else:
            kind = "modify"
        rows.append({"reference": ref, "left": lv, "right": rv, "kind": kind})
    return rows
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_compare.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add app/compare.py tests/test_compare.py
git commit -m "feat: 节点对比纯逻辑 diff_boms"
```

---

### Task 2: 纯逻辑 `hard_changes_between`（时间区间内硬更改）

**Files:**
- Modify: `app/compare.py`
- Test: `tests/test_compare.py`

**Interfaces:**
- Consumes: 无外部依赖。`hcs` 是硬更改 dict 列表（每项至少含 `occurred_at`，canonical UTC 带偏移格式）。
- Produces: `hard_changes_between(hcs: list[dict], lo_ts: str, hi_ts: str) -> list[dict]`，返回 `occurred_at` 落在 `[lo, hi]`（含两端，`lo`/`hi` 内部用 `sorted` 归一）的项，按 `occurred_at` 升序。用 `datetime.fromisoformat` 解析比较，不依赖字符串字典序。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_compare.py
from app.compare import hard_changes_between


def _hc(i, ts):
    return {"id": i, "occurred_at": ts, "title": f"hc{i}"}


def test_between_inclusive_and_sorted():
    hcs = [
        _hc(1, "2026-06-10T00:00:00+00:00"),  # 早于区间
        _hc(2, "2026-06-12T06:30:00+00:00"),  # 区间内
        _hc(3, "2026-06-13T01:10:00+00:00"),  # 恰为右端点
        _hc(4, "2026-06-20T00:00:00+00:00"),  # 晚于区间
    ]
    lo = "2026-06-11T00:00:00+00:00"
    hi = "2026-06-13T01:10:00+00:00"
    got = hard_changes_between(hcs, lo, hi)
    assert [h["id"] for h in got] == [2, 3]


def test_between_symmetric_lo_hi_order():
    hcs = [_hc(2, "2026-06-12T06:30:00+00:00")]
    a = "2026-06-11T00:00:00+00:00"
    b = "2026-06-13T00:00:00+00:00"
    assert hard_changes_between(hcs, a, b) == hard_changes_between(hcs, b, a)


def test_between_empty_when_none_in_range():
    hcs = [_hc(1, "2026-06-01T00:00:00+00:00")]
    assert hard_changes_between(hcs, "2026-06-10T00:00:00+00:00",
                                "2026-06-12T00:00:00+00:00") == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_compare.py -k between -v`
Expected: FAIL（`ImportError: cannot import name 'hard_changes_between'`）

- [ ] **Step 3: 写最小实现**

```python
# app/compare.py 顶部加 import
from datetime import datetime


# 文件末尾追加
def hard_changes_between(hcs: list[dict], lo_ts: str, hi_ts: str) -> list[dict]:
    """取 occurred_at 落在 [lo, hi]（含两端）的硬更改，按时间升序。

    lo/hi 顺序无关（内部归一）；时间用 fromisoformat 解析比较，避免字符串格式脆弱。
    """
    lo, hi = sorted([datetime.fromisoformat(lo_ts), datetime.fromisoformat(hi_ts)])
    picked = [h for h in hcs if lo <= datetime.fromisoformat(h["occurred_at"]) <= hi]
    return sorted(picked, key=lambda h: datetime.fromisoformat(h["occurred_at"]))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_compare.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add app/compare.py tests/test_compare.py
git commit -m "feat: 节点对比纯逻辑 hard_changes_between"
```

---

### Task 3: 对比路由 + `compare.html` 模板

**Files:**
- Modify: `app/routes/board.py`（在文件末尾加 handler；复用顶部已有的 `models`、`fold_bom`、`get_conn`、`templates` import，新增 `from app import compare`、`from app.models import _now`）
- Create: `app/templates/compare.html`
- Test: `tests/test_routes.py`

**Interfaces:**
- Consumes: `compare.diff_boms`、`compare.hard_changes_between`（Task 1/2）；`models.get_node`、`models.get_chain`、`models.list_hard_changes`、`models.get_board`；`fold_bom`；`models._now`。
- Produces: 路由 `GET /board/{board_id}/compare?left=&right=`。节点时间戳取值规则：已提交/根节点用 `committed_at`，草稿用 `_now()`。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_routes.py
def _commit_edit(client, board_id, ref, op, part, msg):
    """在工作区改一处并提交，返回新提交节点所在 board 的最新状态。"""
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": ref, "op": op, "part": part})
    client.post(f"/board/{board_id}/commit", data={"message": msg},
                follow_redirects=False)


def _node_ids(client, board_id):
    """从状态图页解析出节点 id（按页面出现顺序）。"""
    import re
    r = client.get(f"/board/{board_id}")
    return [int(x) for x in re.findall(rf"/board/{board_id}/node/(\d+)", r.text)]


def test_compare_page_renders_diff(client):
    loc = _setup_board(client)               # 初始 BOM: R1=10k
    board_id = loc.rsplit("/", 1)[-1]
    _commit_edit(client, board_id, "C9", "add", "100nF", "加 C9")
    ids = sorted(set(_node_ids(client, board_id)))
    left, right = ids[0], ids[-1]
    r = client.get(f"/board/{board_id}/compare?left={left}&right={right}")
    assert r.status_code == 200
    assert "C9" in r.text
    assert "对比" in r.text


def test_compare_same_node_redirects(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    nid = _node_ids(client, board_id)[0]
    r = client.get(f"/board/{board_id}/compare?left={nid}&right={nid}",
                   follow_redirects=False)
    assert r.status_code == 303
    assert "compare" not in r.headers["location"]


def test_compare_missing_node_404(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    nid = _node_ids(client, board_id)[0]
    r = client.get(f"/board/{board_id}/compare?left={nid}&right=999999")
    assert r.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_routes.py -k compare -v`
Expected: FAIL（404 缺路由 / 断言失败）

- [ ] **Step 3: 写路由实现**

在 `app/routes/board.py` 顶部 import 区追加：

```python
from app import compare
from app.models import _now
```

在文件末尾追加：

```python
def _node_ts(node) -> str:
    """节点用于时间区间的时间戳：已提交/根节点用 committed_at，草稿用当下。"""
    return node["committed_at"] or _now()


@router.get("/board/{board_id}/compare")
def compare_nodes(request: Request, board_id: int, left: int, right: int):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    if left == right:
        return RedirectResponse(
            f"/board/{board_id}?flash=不能和自己比", status_code=303)
    ln = models.get_node(conn, left)
    rn = models.get_node(conn, right)
    for n in (ln, rn):
        if n is None or n["board_id"] != board_id:
            raise HTTPException(status_code=404, detail="节点不存在")
    li, lc = models.get_chain(conn, left)
    ri, rc = models.get_chain(conn, right)
    left_bom = fold_bom(li, lc)
    right_bom = fold_bom(ri, rc)
    rows = compare.diff_boms(left_bom, right_bom)
    diff_rows = [r for r in rows if r["kind"] != "same"]
    same_rows = [r for r in rows if r["kind"] == "same"]
    counts = {k: sum(1 for r in rows if r["kind"] == k)
              for k in ("add", "modify", "remove", "same")}
    hcs = [dict(h) for h in models.list_hard_changes(conn, board_id)]
    between = compare.hard_changes_between(hcs, _node_ts(ln), _node_ts(rn))
    return templates.TemplateResponse(request, "compare.html", {
        "board": board, "board_id": board_id,
        "left_node": ln, "right_node": rn,
        "diff_rows": diff_rows, "same_rows": same_rows,
        "counts": counts, "hard_changes": between,
    })
```

- [ ] **Step 4: 写模板 `app/templates/compare.html`**

> 配色复用 `row-add`/`row-modify`/`row-remove`；未变行用 Alpine 折叠；时间用 `.local-dt`（Task 5 渲染本地，本任务先放占位 `<time>`，Task 5 接管 JS）。

```html
{% extends "base.html" %}
{% block title %}对比 · 板 {{ board.board_uid }} — Reflow{% endblock %}
{% block crumbs %}
<a href="/">首页</a> /
<a href="/board/{{ board_id }}">{{ board.board_name }} / {{ board.pcb_version }} / {{ board.bom_version }} / 板 {{ board.board_uid }}</a> / 对比
{% endblock %}
{% block ctxlinks %}
<a href="/board/{{ board_id }}">状态图</a>
{% endblock %}
{% block content %}
{% macro node_label(n) -%}
{% if not n.is_committed %}工作区草稿{% elif n.parent_id is none %}初始状态{% else %}#{{ n.id }} {{ n.message or '(无说明)' }}{% endif %}
{%- endmacro %}
<div class="page-head" x-data="{ showSame: false }">
  <h1>对比</h1>
  <span class="badge">{{ node_label(left_node) }}</span>
  <span class="muted">→</span>
  <span class="badge">{{ node_label(right_node) }}</span>
  <a class="btn btn-outline" href="/board/{{ board_id }}/compare?left={{ right_node.id }}&right={{ left_node.id }}">⇄ 交换</a>

  <div class="muted" style="width:100%">
    共 {{ counts.add }} 新增 · {{ counts.modify }} 修改 · {{ counts.remove }} 不贴 · {{ counts.same }} 项未变
  </div>

  {% if diff_rows %}
  <table class="bom" style="width:100%">
    <thead><tr><th>位号</th><th>{{ node_label(left_node) }} 的值</th><th></th><th>{{ node_label(right_node) }} 的值</th><th>变化</th></tr></thead>
    <tbody>
    {% for r in diff_rows %}
      <tr class="row-{{ r.kind }}">
        <td><code>{{ r.reference }}</code></td>
        <td>{% if r.left is none %}<span class="muted">—</span>{% else %}{{ r.left }}{% endif %}</td>
        <td class="muted">→</td>
        <td>{% if r.right is none %}<span class="muted">不贴</span>{% else %}{{ r.right }}{% endif %}</td>
        <td>{{ {'add':'新增','modify':'修改','remove':'不贴'}[r.kind] }}</td>
      </tr>
    {% endfor %}
    {% if same_rows %}
      <tr><td colspan="5">
        <button type="button" class="btn-link" @click="showSame = !showSame"
                x-text="showSame ? '▾ 收起未变项' : '▸ 展开 {{ same_rows|length }} 项未变'"></button>
      </td></tr>
      {% for r in same_rows %}
      <tr x-show="showSame" x-cloak>
        <td><code>{{ r.reference }}</code></td>
        <td class="muted">{{ r.left }}</td><td class="muted">=</td>
        <td class="muted">{{ r.right }}</td><td></td>
      </tr>
      {% endfor %}
    {% endif %}
    </tbody>
  </table>
  {% else %}
  <div class="flash">两节点 BOM 完全一致。</div>
  {% endif %}

  <section style="width:100%;margin-top:1.5rem">
    <h2>这段时间内的硬更改{% if hard_changes %}（{{ hard_changes|length }} 条）{% endif %}</h2>
    {% if hard_changes %}
    {% for h in hard_changes %}
    <a class="tl-item hard" href="/board/{{ board_id }}/hard-change/{{ h.id }}">
      <div class="tl-card">
        <b>🔧 {{ h.title }}</b>
        <div class="muted"><time class="local-dt" datetime="{{ h.occurred_at }}">{{ h.occurred_at }}</time>{% if h.description %} · {{ h.description[:40] }}{% endif %}</div>
      </div>
    </a>
    {% endfor %}
    {% else %}
    <div class="muted">这段时间内没有硬更改记录。</div>
    {% endif %}
  </section>
</div>
{% endblock %}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_routes.py -k compare -v`
Expected: PASS（3 passed）

- [ ] **Step 6: 提交**

```bash
git add app/routes/board.py app/templates/compare.html tests/test_routes.py
git commit -m "feat: 节点对比路由与对比页模板"
```

---

### Task 4: 状态图「对比模式」勾选入口

**Files:**
- Modify: `app/templates/state_graph.html`
- Test: `tests/test_compare_ui.py`（新建，Playwright，沿用 `tests/test_hard_change_ui.py` 的 `live_server`/`_make_board` 模式）

**Interfaces:**
- Consumes: Task 3 的 `…/compare?left=&right=` 路由。
- Produces: 状态图页一个「对比节点…」按钮（`data-testid="compare-toggle"`）；开启后每个节点卡片显示勾选框（`.cmp-check`）；选满 2 个，底部浮条（`data-testid="compare-bar"`）出现「开始对比」链接（`data-testid="compare-go"`）。纯 Alpine，硬更改卡片不显示勾选框。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_compare_ui.py
"""对比入口的浏览器测试。"""
import httpx
from playwright.sync_api import Page, expect


def _make_board(base: str, uid: str = "CMP1") -> str:
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "CmpBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")})
        bid = r.headers["location"].split("?")[0].rsplit("/", 1)[-1]
        # 多提交一个节点，保证至少两个可选节点
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "C9", "op": "add", "part": "100nF"})
        c.post(f"/board/{bid}/commit", data={"message": "加 C9"})
    return bid


def test_compare_mode_select_two_and_go(live_server, page: Page):
    bid = _make_board(live_server)
    page.goto(f"{live_server}/board/{bid}")
    page.click("[data-testid=compare-toggle]")
    checks = page.locator(".cmp-check")
    expect(checks.first).to_be_visible()
    checks.nth(0).click()
    checks.nth(1).click()
    bar = page.locator("[data-testid=compare-bar]")
    expect(bar).to_be_visible()
    go = page.locator("[data-testid=compare-go]")
    href = go.get_attribute("href")
    assert "/compare?left=" in href and "right=" in href
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_compare_ui.py -v`
Expected: FAIL（找不到 `[data-testid=compare-toggle]`）

- [ ] **Step 3: 改 `state_graph.html`**

把 `{% block ctxlinks %}` 内追加对比按钮，并把 `.timeline` 包进 Alpine 作用域、给节点卡片加勾选框、底部加浮条。完整替换 `{% block ctxlinks %}` 与 `{% block content %}`：

```html
{% block ctxlinks %}
<a href="/board/{{ board_id }}/log">审计日志</a>
<a class="btn btn-outline" href="/board/{{ board_id }}/hard-change/new">＋ 记录硬更改</a>
<button type="button" class="btn btn-outline" data-testid="compare-toggle"
        @click="cmp = !cmp; sel = []">对比节点…</button>
{% endblock %}
{% block content %}
<div class="page-head"><h1>状态演进</h1></div>
<div class="timeline" x-data="{ cmp: false, sel: [],
       toggle(id){ const i = this.sel.indexOf(id);
         if (i > -1) this.sel.splice(i, 1);
         else if (this.sel.length < 2) this.sel.push(id); },
       href(){ return `/board/{{ board_id }}/compare?left=${this.sel[0]}&right=${this.sel[1]}`; } }">
{% for it in timeline %}
{% if it.kind == 'node' %}
{% set n = it.obj %}{% set s = summaries[n.id] %}
<div class="tl-item {{ 'draft' if not n.is_committed else '' }} {{ 'root' if n.parent_id is none else '' }}">
  <span class="dot"></span>
  <label class="cmp-check" x-show="cmp" x-cloak>
    <input type="checkbox" :checked="sel.includes({{ n.id }})"
           @change="toggle({{ n.id }})" :disabled="!sel.includes({{ n.id }}) && sel.length >= 2">
  </label>
  <a class="tl-card" href="/board/{{ board_id }}/node/{{ n.id }}">
    <b>{% if not n.is_committed %}工作区草稿{% elif n.parent_id is none %}初始状态{% else %}#{{ n.id }} {{ n.message or '(无说明)' }}{% endif %}</b>
    {% if n.parent_id is none %}
    <span class="badge badge-purple">初始 BOM · {{ initial_count }} 位号</span>
    {% elif not n.is_committed %}
    <span class="badge badge-blue">{{ s|length }} 条未提交</span>
    {% else %}
    <span class="badge">{{ s|length }} 条修改</span>
    {% endif %}
    {% if n.is_committed %}<code class="muted" title="哈希 {{ node_hash(n.id) }}">{{ node_short(n.id) }}</code>{% endif %}
    <div class="muted">
      {%- for c in s[:4] -%}
        {{ c.reference }} {{ {'add': '新增', 'modify': '修改', 'remove': '不贴'}[c.op] }}{% if not loop.last %} · {% endif %}
      {%- endfor -%}
      {%- if s|length > 4 %} …{% endif -%}
      {%- if s %} · {% endif %}<time class="local-dt" datetime="{{ n.committed_at or n.created_at }}">{{ n.committed_at or n.created_at }}</time>
    </div>
  </a>
</div>
{% else %}
{% set h = it.obj %}
<a class="tl-item hard" href="/board/{{ board_id }}/hard-change/{{ h.id }}">
  <span class="dot"></span>
  <div class="tl-card">
    <b>🔧 {{ h.title }}</b>
    <span class="badge badge-yellow">硬更改</span>
    <code class="muted" title="哈希 {{ hard_hash(h.id) }}">{{ hard_short(h.id) }}</code>
    <div class="muted"><time class="local-dt" datetime="{{ h.occurred_at }}">{{ h.occurred_at }}</time>{% if h.description %} · {{ h.description[:40] }}{% endif %}</div>
  </div>
</a>
{% endif %}
{% endfor %}
<div class="compare-bar" data-testid="compare-bar" x-show="cmp && sel.length === 2" x-cloak>
  已选 2 个节点
  <a class="btn btn-primary" data-testid="compare-go" :href="href()">开始对比 →</a>
</div>
</div>
{% endblock %}
```

> 注意：原模板节点项是 `<a class="tl-item">` 整块可点；这里改为外层 `<div class="tl-item">` + 内层 `<a class="tl-card">`，以便勾选框与卡片并存。请同步在 `app/static/style.css` 确认 `.tl-item` 仍能正常排版（`.tl-card` 继承原 `.tl-item > .tl-card` 样式）。

- [ ] **Step 4: 加样式 `app/static/style.css`**

> 先读 `docs/前端风格指南.md`，新颜色用 CSS 变量并同步夜间模式。本任务只需中性布局，无新颜色。

```css
/* 对比入口 */
.cmp-check{display:inline-flex;align-items:center;margin-right:.4rem}
a.tl-card{display:block}   /* 节点项内层由整块 a 改为卡片 a，保持块级排版 */
.compare-bar{position:sticky;bottom:0;display:flex;align-items:center;gap:.75rem;
  padding:.6rem .9rem;margin-top:1rem;border-radius:var(--radius);
  background:var(--surface);border:1px solid var(--border)}
```

> 令牌已与 `app/static/style.css` 实际定义对齐：`--surface`（面板背景）、`--border`、`--radius`。

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_compare_ui.py -v`
Expected: PASS（1 passed）

- [ ] **Step 6: 双主题自检**

手动启动 `uvicorn app.main:app --reload`，白天/夜间各看一次状态图：对比按钮、勾选框、浮条排版正常，节点卡片仍可点进详情。

- [ ] **Step 7: 提交**

```bash
git add app/templates/state_graph.html app/static/style.css tests/test_compare_ui.py
git commit -m "feat: 状态图对比模式勾选入口"
```

---

### Task 5: 时间展示统一（渲染浏览器本地）

**Files:**
- Modify: `app/templates/base.html`（在底部 `<script>` 内加 `.local-dt` 渲染逻辑）
- Modify: `app/templates/node_detail.html:15`、`app/templates/log.html:28`、`app/templates/hard_change_detail.html:19`（把裸时间输出包成 `<time class="local-dt" datetime="…">`）
- Test: `tests/test_compare_ui.py`（追加一个本地渲染断言）

**Interfaces:**
- Consumes: 各模板里 `class="local-dt"` 且 `datetime` 为 canonical UTC 的 `<time>` 元素（compare.html、state_graph.html 已在 Task 3/4 产出）。
- Produces: 页面加载后，`.local-dt` 文本被替换为浏览器本地时间字符串（`toLocaleString('zh-CN')`）。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_compare_ui.py
def test_local_dt_rendered_to_local(live_server, page: Page):
    bid = _make_board(live_server)
    page.goto(f"{live_server}/board/{bid}")
    # 节点提交时间已是 UTC（含 +00:00）；渲染后文本不应再带 'T...+00:00'
    el = page.locator("time.local-dt").first
    expect(el).to_be_visible()
    text = el.inner_text()
    assert "+00:00" not in text and "T" not in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_compare_ui.py -k local_dt -v`
Expected: FAIL（未渲染，文本仍含 `T`/`+00:00`）

- [ ] **Step 3: 在 `base.html` 底部 `<script>` 内追加渲染逻辑**

在 `document.body.addEventListener('showToast', …)` 之后追加：

```javascript
  function renderLocalDates(root){
    (root || document).querySelectorAll('time.local-dt').forEach(el => {
      const iso = el.getAttribute('datetime');
      const d = iso ? new Date(iso) : null;
      if (d && !isNaN(d)) el.textContent = d.toLocaleString('zh-CN', { hour12: false });
    });
  }
  renderLocalDates();
  document.body.addEventListener('htmx:afterSwap', e => renderLocalDates(e.target));
```

- [ ] **Step 4: 把现有裸时间输出包成 `<time class="local-dt">`**

`node_detail.html` 第 15 行：

```html
  <span class="badge">已提交 · <time class="local-dt" datetime="{{ node.committed_at }}">{{ node.committed_at }}</time></span>
```

`log.html` 第 28 行：

```html
    <td class="muted"><time class="local-dt" datetime="{{ r.created_at }}">{{ r.created_at }}</time></td>
```

`hard_change_detail.html` 第 19 行：

```html
  <div class="muted">发生时间：<time class="local-dt" datetime="{{ hc.occurred_at }}">{{ hc.occurred_at }}</time></div>
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_compare_ui.py -v`
Expected: PASS（全部 passed）

- [ ] **Step 6: 提交**

```bash
git add app/templates/base.html app/templates/node_detail.html app/templates/log.html app/templates/hard_change_detail.html tests/test_compare_ui.py
git commit -m "feat: 时间统一渲染为浏览器本地时间"
```

---

### Task 6: 硬更改 `occurred_at` 写入时转 UTC

**Files:**
- Modify: `app/templates/hard_change_form.html`（datetime-local 输入改为本地显示 + 隐藏 UTC 字段；编辑回填把 UTC 转本地）
- Test: `tests/test_hard_change_routes.py`

**Interfaces:**
- Consumes: 路由 `hc_create`/`hc_edit` 仍读 `occurred_at` 表单字段（Task 不改路由签名）。
- Produces: 表单提交的 `occurred_at` 为 canonical UTC（`YYYY-MM-DDTHH:MM:SS+00:00`）；`datetime-local` 输入框名改为 `occurred_at_local`（仅展示/编辑），隐藏字段 `name="occurred_at"` 由 JS 在 `input`/提交时同步为 UTC。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_hard_change_routes.py（沿用本文件已有的 client/_make_board 模式）
def test_create_hard_change_stores_utc(client):
    # 直接 POST canonical UTC（模拟前端已转换），断言存库即为该值
    bid = _make_board(client)
    client.post(f"/board/{bid}/hard-change",
                data={"title": "飞线", "occurred_at": "2026-06-13T01:10:00+00:00",
                      "description": "x"})
    from app.main import get_conn
    from app import models
    hcs = models.list_hard_changes(get_conn(), int(bid))
    assert hcs[-1]["occurred_at"] == "2026-06-13T01:10:00+00:00"
```

> 若 `tests/test_hard_change_routes.py` 尚无 `_make_board`/`client`，复用该文件已有的等价 fixture/helper（参考文件顶部）；仅断言「后端原样存入传入的 UTC 值」。

- [ ] **Step 2: 运行测试确认失败 / 或确认现状**

Run: `pytest tests/test_hard_change_routes.py -k utc -v`
Expected: 若后端已原样存储则可能直接 PASS；本任务核心改动在前端模板，测试用于锁定「后端按传入值存储」不被破坏。若 FAIL 则按 Step 3 排查路由是否 strip 破坏格式（`occurred_at.strip()` 不影响）。

- [ ] **Step 3: 改 `hard_change_form.html` 的发生时间块**

把第 17–24 行的 `<label>发生时间…</label>` 整块替换为：

```html
  <label>发生时间
    <input class="input" type="datetime-local" name="occurred_at_local" required
           x-data="{
             toLocalInput(iso){
               if(!iso) return '';
               const d = new Date(iso);
               return new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,16);
             },
             toUtc(localVal){
               if(!localVal) return '';
               const d = new Date(localVal);
               const pad = n => String(n).padStart(2,'0');
               return `${d.getUTCFullYear()}-${pad(d.getUTCMonth()+1)}-${pad(d.getUTCDate())}T`
                    + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:00+00:00`;
             },
             sync(){ this.$refs.utc.value = this.toUtc(this.$el.value); }
           }"
           x-init="
             $el.value = '{{ default_time }}'
               ? ('{{ default_time }}'.includes('+') || '{{ default_time }}'.endsWith('Z')
                   ? toLocalInput('{{ default_time }}') : '{{ default_time }}')
               : toLocalInput(new Date().toISOString());
             sync();
           "
           @input="sync()">
    <input type="hidden" name="occurred_at" x-ref="utc">
  </label>
```

> 说明：`default_time` 在新建时为空、编辑时为已存 UTC。`toLocalInput` 把 UTC 转本地填进可见输入框；`sync()` 把可见的本地值转回 canonical UTC 写入隐藏字段 `occurred_at`，后端原样存储。旧的本地无偏移 `default_time`（迁移前）走 else 分支按原样填。

- [ ] **Step 4: 运行回归**

Run: `pytest tests/test_hard_change_routes.py tests/test_hard_change_ui.py -v`
Expected: PASS（含新建/编辑流程不回归）

- [ ] **Step 5: 双主题 + 真机自检**

启动应用，新建一条硬更改：填本地时间提交，进详情页确认显示的本地时间与所填一致；编辑该条确认 datetime-local 回填为同一本地时间。

- [ ] **Step 6: 提交**

```bash
git add app/templates/hard_change_form.html tests/test_hard_change_routes.py
git commit -m "feat: 硬更改发生时间写入时转 canonical UTC"
```

---

### Task 7: 旧数据迁移脚本（occurred_at → UTC）

**Files:**
- Create: `app/migrations.py`（纯逻辑转换函数，★ 可测）
- Create: `scripts/migrate_occurred_at_utc.py`（命令行入口，连库执行）
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `to_utc_from_singapore(value: str) -> str`：把「无偏移本地（新加坡 +08:00）」字符串转 canonical UTC（`…+00:00`，秒精度）；已带偏移（含 `+`/`-` 偏移或 `Z`）的原样返回（幂等）。
  - `migrate_occurred_at(conn) -> int`：遍历 `hard_changes`，对需要转换的行更新 `occurred_at`，返回转换条数。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_migrations.py
from app.migrations import to_utc_from_singapore


def test_naive_singapore_to_utc():
    # 新加坡 +08:00：09:10 → UTC 01:10
    assert to_utc_from_singapore("2026-06-13T09:10") == "2026-06-13T01:10:00+00:00"


def test_already_offset_is_idempotent():
    s = "2026-06-13T01:10:00+00:00"
    assert to_utc_from_singapore(s) == s
    assert to_utc_from_singapore(to_utc_from_singapore("2026-06-13T09:10")) \
        == "2026-06-13T01:10:00+00:00"


def test_z_suffix_left_untouched():
    s = "2026-06-13T01:10:00Z"
    assert to_utc_from_singapore(s) == s
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_migrations.py -v`
Expected: FAIL（`No module named 'app.migrations'`）

- [ ] **Step 3: 写转换实现**

```python
# app/migrations.py
"""一次性数据迁移的纯逻辑：把无偏移的本地 occurred_at 转为 canonical UTC。"""
from datetime import datetime, timedelta, timezone

SGT = timezone(timedelta(hours=8))   # 新加坡，固定 +08:00，无夏令时


def to_utc_from_singapore(value: str) -> str:
    """无偏移本地时间（视为新加坡 +08:00）→ canonical UTC（YYYY-MM-DDTHH:MM:SS+00:00）。
    已带偏移或 Z 后缀的原样返回（幂等）。"""
    if not value:
        return value
    if value.endswith("Z") or "+" in value or value.count("-") > 2:
        return value
    dt = datetime.fromisoformat(value).replace(tzinfo=SGT)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def migrate_occurred_at(conn) -> int:
    """把 hard_changes.occurred_at 中无偏移的旧值转为 UTC，返回转换条数。"""
    rows = conn.execute("SELECT id, occurred_at FROM hard_changes").fetchall()
    n = 0
    for r in rows:
        new = to_utc_from_singapore(r["occurred_at"])
        if new != r["occurred_at"]:
            conn.execute("UPDATE hard_changes SET occurred_at=? WHERE id=?",
                         (new, r["id"]))
            n += 1
    conn.commit()
    return n
```

> `value.count("-") > 2` 用于识别形如 `…-08:00` 的负偏移（日期里已有 2 个 `-`，第 3 个即偏移符）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 写命令行入口 + 端到端测试**

```python
# scripts/migrate_occurred_at_utc.py
"""把 hard_changes.occurred_at 旧的无偏移本地时间按新加坡时区补成 UTC。

用法： REFLOW_DB=reflow.sqlite python scripts/migrate_occurred_at_utc.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.migrations import migrate_occurred_at   # noqa: E402


def main():
    db = os.environ.get("REFLOW_DB", "reflow.sqlite")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    n = migrate_occurred_at(conn)
    print(f"已转换 {n} 条 occurred_at → UTC（库：{db}）")


if __name__ == "__main__":
    main()
```

追加端到端测试：

```python
# 追加到 tests/test_migrations.py
import sqlite3
from app.migrations import migrate_occurred_at
from app.db import init_db   # 若初始化函数名不同，用 app/db.py 中实际的建表入口


def test_migrate_occurred_at_end_to_end(tmp_path):
    db = tmp_path / "m.sqlite"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    init_db(conn)            # 建表
    conn.execute("INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
                 " VALUES(1,'a','',?,?)", ("2026-06-13T09:10", "2026-06-13T01:00:00+00:00"))
    conn.execute("INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
                 " VALUES(1,'b','',?,?)", ("2026-06-14T00:00:00+00:00", "2026-06-13T01:00:00+00:00"))
    conn.commit()
    n = migrate_occurred_at(conn)
    assert n == 1   # 仅无偏移那条被转换
    vals = [r["occurred_at"] for r in conn.execute("SELECT occurred_at FROM hard_changes ORDER BY id")]
    assert vals == ["2026-06-13T01:10:00+00:00", "2026-06-14T00:00:00+00:00"]
    assert migrate_occurred_at(conn) == 0   # 幂等：再跑无变化
```

> 执行前先确认 `app/db.py` 的建表函数名（打开文件查 `def`）；若不是 `init_db`，替换为实际名称。

- [ ] **Step 6: 运行测试确认通过**

Run: `pytest tests/test_migrations.py -v`
Expected: PASS（4 passed）

- [ ] **Step 7: 提交**

```bash
git add app/migrations.py scripts/migrate_occurred_at_utc.py tests/test_migrations.py
git commit -m "feat: occurred_at 迁移脚本（新加坡时区补 UTC，幂等）"
```

---

### Task 8: 全量回归 + 文档收尾

**Files:**
- Modify: `CLAUDE.md`（在「约定/注意事项」补一行时间约定；测试计数更新）

- [ ] **Step 1: 跑全量测试**

Run: `pytest`
Expected: 全绿（原 74 + 新增用例）。失败则定位修复，不得跳过。

- [ ] **Step 2: 在 `CLAUDE.md` 补时间约定**

在「约定 / 注意事项」列表末尾追加：

```markdown
- **时间统一**：存储层一律 canonical UTC（`YYYY-MM-DDTHH:MM:SS+00:00`，见 `models._now`）；硬更改 `occurred_at` 由前端在提交时转 UTC；展示层用 `<time class="local-dt" datetime="UTC">` + `base.html` 的 `renderLocalDates()` 渲染为浏览器本地时间。历史旧数据用 `scripts/migrate_occurred_at_utc.py`（按新加坡 +08:00）一次性迁移。
```

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 补充时间统一约定"
```

---

## 自检对照（spec coverage）

- 统一差异表 + 未变折叠 → Task 1、Task 3。
- 状态图勾选入口、所有节点可比 → Task 4。
- 硬更改区间（含端点、左右对称、草稿用当下）→ Task 2、Task 3（`_node_ts`）。
- 交换、404、left==right、完全一致/空硬更改 → Task 3。
- 存 UTC + 渲染本地 → Task 5；occurred_at 写入转 UTC → Task 6。
- 旧数据按新加坡迁移、幂等 → Task 7。
- 回归 74 全绿、文档 → Task 8。
