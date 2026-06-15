# 集群 A：硬更改 UI（时间本地化 + 图片灯箱）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让硬更改「发生时间」默认值取浏览器本地时间（issue #14），并把详情页附图改为缩略图 + 同页灯箱查看（issue #11）。

**Architecture:** 两项均为前端小改动，落在硬更改子系统。#14 把服务器端预填的时间改为客户端 Alpine `x-init` 用浏览器本地时间填充，服务器不再注入硬编码时间；#11 用已加载的 Alpine 在详情页做一个局部灯箱组件，遮罩层背景与现有 `.modal-overlay` 同风格，新增 `.lightbox` 系列类（中性 rgba 叠层，两套主题通用，无需新增主题色变量）。

**Tech Stack:** FastAPI + Jinja2 模板、HTMX、Alpine.js（CDN/vendor，无构建）、纯 CSS 变量令牌、pytest + Playwright。

**前置阅读（执行前必读）：** `docs/前端风格指南.md`（设计令牌、组件清单「先复用再新建」、改完自检清单——两套主题都要实际查看）。

**约定提醒：**
- Starlette `TemplateResponse` 用新签名 `templates.TemplateResponse(request, "name.html", {ctx})`，context 里不要放 `"request"` 键。
- 模板向 JS 传值一律 `|tojson`，且承载该值的属性用单引号。
- 不写内联 `style`、不引 CSS 框架；新颜色变量必须同步 `[data-theme="dark"]` 块（本计划不新增颜色变量）。
- UI 文案、注释、错误信息均为中文。

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `app/routes/hard_change.py` | 修改 | 新建表单与校验失败重渲染时不再注入服务器时间（`default_time` 置空），仅保留提交兜底 |
| `app/templates/hard_change_form.html` | 修改 | 「发生时间」字段设 `required`，为空时由 Alpine `x-init` 填浏览器本地时间 |
| `app/templates/hard_change_detail.html` | 修改 | 附图画廊包进 Alpine 灯箱组件，点击缩略图同页放大查看 |
| `app/static/style.css` | 修改 | 缩略图尺寸；新增 `[x-cloak]` 与 `.lightbox` / `.lightbox-img` / `.lightbox-close` |
| `tests/test_hard_change_ui.py` | 修改 | 新增 #14、#11 的 Playwright/httpx 测试 |

---

## Task 1：#14 发生时间默认值改用浏览器本地时间

**Files:**
- Modify: `app/routes/hard_change.py:41-49`（`hc_new_form`）、`app/routes/hard_change.py:64-69`（创建校验失败重渲染分支）
- Modify: `app/templates/hard_change_form.html:17-20`（发生时间字段）
- Test: `tests/test_hard_change_ui.py`

说明：`_now_minute()`（`hard_change.py:14`）保留，仅用于提交时的服务器兜底（`occurred = occurred_at.strip() or _now_minute()`，line 75）——配合字段 `required` + 客户端预填后，该兜底几乎不会触发。新建表单与校验失败重渲染**不再**把服务器时间塞进 `default_time`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_hard_change_ui.py` 末尾追加（文件顶部已 `import httpx`；新增 `import re`）：

```python
import re


def test_new_form_time_is_browser_local(live_server, page: Page):
    bid = _make_board(live_server)

    # 服务器不再注入硬编码时间：原始 HTML 中 occurred_at 的 value 为空
    with httpx.Client(base_url=live_server) as c:
        html = c.get(f"/board/{bid}/hard-change/new").text
    m = re.search(r'name="occurred_at"[^>]*\bvalue="([^"]*)"', html)
    assert m is not None, "未找到 occurred_at 字段"
    assert m.group(1) == "", f"服务器仍注入了时间：{m.group(1)!r}"

    # 浏览器加载后由客户端填入本地当前时间（datetime-local 格式）
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.wait_for_function(
        "document.querySelector('input[name=occurred_at]').value !== ''")
    val = page.input_value("input[name=occurred_at]")
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", val), val
    browser_now = page.evaluate(
        "() => { const d = new Date();"
        " return new Date(d.getTime() - d.getTimezoneOffset()*60000)"
        ".toISOString().slice(0,16); }")
    # 到「年月日时」一致即可证明取的是浏览器本地时间（容忍跨分钟）
    assert val[:13] == browser_now[:13], f"{val} vs {browser_now}"
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_hard_change_ui.py::test_new_form_time_is_browser_local -v`
Expected: FAIL —— 原始 HTML 的 `value` 当前是服务器时间（非空），第一个断言即失败。

- [ ] **Step 3: 实现——路由不再注入服务器时间**

在 `app/routes/hard_change.py` 的 `hc_new_form` 中，把 `default_time` 改为空字符串：

```python
@router.get("/board/{board_id}/hard-change/new")
def hc_new_form(request: Request, board_id: int):
    conn = get_conn()
    board = _require_board(conn, board_id)
    return templates.TemplateResponse(request, "hard_change_form.html", {
        "board": board, "board_id": board_id, "mode": "new",
        "hc": None, "images": [], "form": {}, "error": None,
        "default_time": "",
    })
```

并把创建校验失败重渲染分支里的 `occurred_at or _now_minute()` 改为只回填用户提交值（为空则交给客户端补）：

```python
        return templates.TemplateResponse(request, "hard_change_form.html", {
            "board": board, "board_id": board_id, "mode": "new", "hc": None,
            "images": [], "error": err, "default_time": occurred_at,
            "form": {"title": title, "occurred_at": occurred_at, "description": description},
        }, status_code=200)
```

（`hc_edit_form` 的 `default_time` 仍为 `hc["occurred_at"]`，不改；提交兜底 line 75 的 `_now_minute()` 保留。）

- [ ] **Step 4: 实现——模板字段设 required + 客户端本地时间预填**

把 `app/templates/hard_change_form.html` 的「发生时间」字段替换为：

```html
  <label>发生时间
    <input class="input" type="datetime-local" name="occurred_at" required
           value="{{ form.occurred_at if form.occurred_at else default_time }}"
           x-data
           x-init="if (!$el.value) { const d = new Date();
             $el.value = new Date(d.getTime() - d.getTimezoneOffset()*60000)
               .toISOString().slice(0,16); }">
  </label>
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `pytest tests/test_hard_change_ui.py::test_new_form_time_is_browser_local -v`
Expected: PASS

- [ ] **Step 6: 跑硬更改相关测试，确认无回归**

Run: `pytest tests/test_hard_change_ui.py tests/test_hard_change_routes.py -v`
Expected: 全部 PASS（`test_record_hard_change_flow` 不涉及 occurred_at 预填，应不受影响）。

- [ ] **Step 7: 提交**

```bash
git add app/routes/hard_change.py app/templates/hard_change_form.html tests/test_hard_change_ui.py
git commit -m "$(cat <<'MSG'
feat: 硬更改发生时间默认值改用浏览器本地时间 (#14)

服务器不再注入硬编码时间；新建表单为空时由 Alpine x-init
用浏览器本地时间填入 datetime-local，字段设 required。

Closes #14

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
)"
```

---

## Task 2：#11 详情页附图改为缩略图 + 同页灯箱

**Files:**
- Modify: `app/templates/hard_change_detail.html:18-28`（附图画廊）
- Modify: `app/static/style.css:127-131`（`.hc-gallery img` 改缩略图）、并在「弹窗/toast 区」（`.modal-foot` 之后、`#toast-zone` 之前附近）新增灯箱样式
- Test: `tests/test_hard_change_ui.py`

设计：单个 Alpine 灯箱组件挂在画廊容器上（`x-data`），点击任一缩略图记录其 `src/alt` 并 `open=true`；遮罩为固定全屏中性黑色叠层（与 `.modal-overlay` 同风格的 rgba，无需新增主题色变量），图片 `max 92vw/92vh`，右上角半透明圆形「关闭」按钮；点遮罩空白或按 ESC 关闭，点图片本身不关闭。保留 `<a href>` 作为无 JS 兜底。

- [ ] **Step 1: 写失败测试**

在 `tests/test_hard_change_ui.py` 末尾追加：

```python
def test_detail_image_lightbox(live_server, page: Page):
    bid = _make_board(live_server)
    # 记录一条带附图的硬更改
    page.goto(f"{live_server}/board/{bid}/hard-change/new")
    page.fill("input[name=title]", "带图硬更改")
    page.set_input_files("input[name=files]", files=[
        {"name": "p.png", "mimeType": "image/png", "buffer": PNG_1PX}])
    page.click("button[type=submit]")
    page.wait_for_load_state("networkidle")

    # 进入详情页
    page.locator(".tl-item.hard").first.click()
    page.wait_for_load_state("networkidle")

    # 初始：灯箱不可见
    expect(page.locator(".lightbox")).to_be_hidden()
    # 点击缩略图 → 灯箱与大图可见
    page.locator(".hc-gallery img").first.click()
    expect(page.locator(".lightbox")).to_be_visible()
    expect(page.locator(".lightbox-img")).to_be_visible()
    # 点关闭按钮 → 灯箱隐藏
    page.locator(".lightbox-close").click()
    expect(page.locator(".lightbox")).to_be_hidden()
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_hard_change_ui.py::test_detail_image_lightbox -v`
Expected: FAIL —— `.lightbox` 不存在，定位/断言超时失败。

- [ ] **Step 3: 实现——详情页画廊改灯箱组件**

把 `app/templates/hard_change_detail.html` 的附图块（`{% if images %}` 到对应 `{% endif %}`）替换为：

```html
  {% if images %}
  <div class="hc-gallery" x-data="{ open: false, src: '', alt: '' }">
    {% for im in images %}
    <a class="hc-photo" href="/uploads/{{ im.filename }}"
       @click.prevent='src={{ ("/uploads/" ~ im.filename)|tojson }}; alt={{ im.original_name|tojson }}; open = true'>
      <img src="/uploads/{{ im.filename }}" alt="{{ im.original_name }}" loading="lazy">
    </a>
    {% endfor %}
    <div class="lightbox" x-cloak x-show="open"
         @click="open = false" @keydown.escape.window="open = false">
      <button type="button" class="lightbox-close" @click="open = false" aria-label="关闭">✕</button>
      <img class="lightbox-img" :src="src" :alt="alt" @click.stop>
    </div>
  </div>
  {% else %}
  <div class="muted">（无附图）</div>
  {% endif %}
```

- [ ] **Step 4: 实现——CSS：缩略图 + 灯箱样式**

在 `app/static/style.css` 把 `.hc-gallery img` 一条（当前约 line 128-129）替换为统一缩略图：

```css
.hc-gallery img{width:120px;height:120px;object-fit:cover;border:1px solid var(--border);
  border-radius:var(--radius);display:block;cursor:pointer}
```

并在「弹窗/toast 区」`.modal-foot{...}` 规则之后、`#toast-zone{...}` 之前，新增：

```css
/* Alpine 初始化前隐藏带 x-cloak 的元素，避免闪现 */
[x-cloak]{display:none !important}

/* 图片灯箱（硬更改详情附图）——中性黑色叠层，两套主题通用 */
.lightbox{position:fixed;inset:0;background:rgba(0,0,0,.8);display:flex;
  align-items:center;justify-content:center;padding:24px;z-index:70}
.lightbox-img{max-width:92vw;max-height:92vh;border-radius:var(--radius);
  box-shadow:0 8px 32px rgba(0,0,0,.5)}
.lightbox-close{position:fixed;top:16px;right:20px;width:40px;height:40px;
  border:none;border-radius:50%;background:rgba(0,0,0,.45);color:#fff;
  font-size:20px;line-height:1;cursor:pointer}
.lightbox-close:hover{background:rgba(0,0,0,.7)}
```

（说明：z-index 70 高于 modal-overlay 50 与 toast 60；全用中性 rgba 黑/白，未引入新颜色变量，故无需改 `[data-theme="dark"]` 块。）

- [ ] **Step 5: 运行测试，确认通过**

Run: `pytest tests/test_hard_change_ui.py::test_detail_image_lightbox -v`
Expected: PASS

- [ ] **Step 6: 人工自检（前端风格指南要求）**

启动应用 `uvicorn app.main:app --reload`，建一条带图硬更改，进详情页：
- [ ] 缩略图为 120px 方形、`object-fit:cover` 不变形
- [ ] 点击缩略图弹出灯箱、大图居中且不超出视口；点空白处 / 按 ESC / 点关闭按钮均能关闭；点大图本身不关闭
- [ ] 右上角 🌙/☀️ 切换，**白天与夜间两套主题都实际查看**：遮罩、关闭按钮、大图边框在两套主题下都清晰，无纯白闪块
- [ ] 无内联 style、复用了现有令牌、未新增未配对的颜色变量

- [ ] **Step 7: 提交**

```bash
git add app/templates/hard_change_detail.html app/static/style.css tests/test_hard_change_ui.py
git commit -m "$(cat <<'MSG'
feat: 硬更改详情附图改为缩略图 + 同页灯箱查看 (#11)

画廊缩略图统一 120px 方形；点击在固定遮罩层上放大查看，
半透明关闭按钮，点空白/ESC 关闭。新增 .lightbox 系列类，
中性 rgba 叠层两套主题通用。

Closes #11

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG
)"
```

---

## Task 3：全量回归与收尾

- [ ] **Step 1: 跑全部测试**

Run: `pytest`
Expected: 全绿（原 74 passed + 本计划新增 2 个用例 = 76 passed）。若某用例因环境（Playwright 浏览器未装）跳过，记录原因。

- [ ] **Step 2: 开 PR**

```bash
git push -u origin worktree-cluster-a-hc-ui
gh pr create --base master --head worktree-cluster-a-hc-ui \
  --title "feat: 硬更改 UI——发生时间本地化(#14) + 附图灯箱(#11)" \
  --body "集群 A。Closes #14 / #11。详见各提交。"
```

---

## Self-Review（计划自查）

**1. Spec 覆盖**
- #14「时间用浏览器本地时间」→ Task 1（服务器停注入 + 客户端 `x-init` 本地时间 + `required`）。✓
- #11「缩略图 + 点击放大到接近可视区、Mask 层、半透明关闭按钮」→ Task 2（120px 缩略图 + `.lightbox` 全屏遮罩 + 92vw/92vh 大图 + `.lightbox-close` 半透明按钮）。✓

**2. 占位符扫描**：无 TBD/TODO；每个改代码的步骤都给了完整代码与确切命令/预期。✓

**3. 类型/命名一致**：模板里 Alpine 变量 `open/src/alt` 与 CSS 类 `.lightbox/.lightbox-img/.lightbox-close` 在 Task 2 各步一致；测试选择器（`.lightbox`、`.lightbox-img`、`.lightbox-close`、`.hc-gallery img`）与模板/CSS 一致；`default_time` 键在路由各分支与模板用法一致。✓

**4. 注意点**：`_now_minute()` 仅在提交兜底保留（不删），避免 `import datetime` 悬空；`occurred_at` 存储与展示语义不变（裸字符串），符合 issue 范围。
