# Header 三点菜单收纳 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 header 的所有功能入口（各页面 `ctxlinks` 块 + 主题切换）收进一个 ⋯ 弹出菜单，桌面单行、窄屏三行。

**Architecture:** 只改 `base.html`（header DOM）、`style.css`（桌面 + 窄屏覆盖）和两个 UI 测试文件；各页面模板的 `ctxlinks` 块零改动。弹出菜单复用现有 `.menu` / `.menu-btn` / `.menu-pop` 组件与 Alpine 模式（`@click.outside` 关闭）。

**Tech Stack:** Jinja2 + Alpine.js（CDN，无构建）、纯 CSS、pytest + Playwright。

**设计文档:** `docs/superpowers/specs/2026-07-18-topnav-kebab-menu-design.md`

## Global Constraints

- 所有 UI 文案、注释、commit message 用中文。
- 颜色只用 CSS 变量（`var(--…)`），不引入新颜色值；新样式两套主题（light/dark）都必须能看。
- 测试基线约 491 passed；`test_rename_ui` 偶发端口竞态，失败时单独重跑该文件即可，不算回归。
- 运行环境：`cd /home/tong/code/reflow/.claude/worktrees/mobile-adaptation && . ../../../.venv/bin/activate`（worktree 内无独立 venv 时用仓库根的 `.venv`；若 worktree 自带 `.venv` 优先用它）。
- commit 尾部加 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: base.html header 重构 + 桌面样式

**Files:**
- Modify: `app/templates/base.html`（header 块，现第 27-43 行）
- Modify: `app/static/style.css`（`.topnav` 区、`.theme-toggle` 规则、「⋯ 操作菜单」区末尾）
- Test: `tests/test_topnav_menu.py`（新建）

**Interfaces:**
- Produces: `.topnav-menu-btn`（⋯ 按钮，Task 3 的测试用它展开菜单）、`.topnav-actions`（弹出面板，包住 `{% block ctxlinks %}` 与主题切换按钮）。
- 各页面 `ctxlinks` 块的内容（`a`、`.btn`、`.btn-outline`、`.btn-link danger` 等）原样渲染进面板，交互不变。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_topnav_menu.py`：

```python
"""header 三点菜单收纳（2026-07-18 设计）的模板级检验。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def test_topnav_has_kebab_menu(client):
    """首页 header 含 ⋯ 菜单按钮与收纳面板，旧的行内功能区容器已移除。"""
    r = client.get("/")
    assert r.status_code == 200
    assert "topnav-menu-btn" in r.text
    assert "topnav-actions" in r.text
    assert 'class="ctx"' not in r.text


def test_ctxlinks_still_render(client):
    """页面 ctxlinks 内容（首页的「＋ 新建单板」）仍然渲染，主题切换按钮也在。"""
    r = client.get("/")
    assert "＋ 新建单板" in r.text
    assert "theme-toggle" in r.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_topnav_menu.py -v`
Expected: `test_topnav_has_kebab_menu` FAIL（无 `topnav-menu-btn`）；`test_ctxlinks_still_render` 可能已过（无妨，守护用）。

- [ ] **Step 3: 重写 base.html 的 header**

把 `app/templates/base.html` 中整个 `<header class="topnav">…</header>`（现第 27-43 行）替换为：

```html
<header class="topnav">
  <a class="brand" href="/">⟲ Reflow</a>
  <nav class="crumbs">{% block crumbs %}{% endblock %}</nav>
  <form class="hash-jump" hx-get="/hash-lookup" hx-swap="none" autocomplete="off"
        onsubmit="return false">
    <input class="input" type="text" name="q" placeholder="哈希跳转…"
           title="输入提交节点/硬更改的哈希值（至少 4 位），回车跳转到详情页"
           aria-label="哈希跳转">
  </form>
  <span class="menu" x-data="{nav:false}" @click.outside="nav=false">
    <button type="button" class="menu-btn topnav-menu-btn" title="菜单" aria-label="菜单"
            @click="nav=!nav">⋯</button>
    <nav class="menu-pop topnav-actions" x-show="nav" x-cloak @click="nav=false">
      {% block ctxlinks %}{% endblock %}
      <button type="button" class="theme-toggle" title="切换白天/夜间模式"
        x-data="{t: document.documentElement.getAttribute('data-theme')}"
        @click="t = t === 'dark' ? 'light' : 'dark';
                document.documentElement.setAttribute('data-theme', t);
                localStorage.setItem('theme', t)"
        x-text="t === 'dark' ? '☀️ 切换白天模式' : '🌙 切换夜间模式'"></button>
    </nav>
  </span>
</header>
```

要点：`crumbs` 前移、`.ctx` 容器删除；`hash-jump` 保持原样只换位置；面板容器上的 `@click="nav=false"` 靠冒泡实现「点任意菜单项即关闭」；主题切换按钮自带的内层 `x-data` 与外层 `nav` 作用域嵌套，互不干扰。

- [ ] **Step 4: 桌面样式调整（style.css）**

4a. `.topnav` 区（现第 27-34 行）：删除 `.topnav .ctx{…}` 一行，`.hash-jump` 加 `margin-left:auto`，改后为：

```css
.topnav{background:var(--surface);border-bottom:1px solid var(--border);padding:10px 24px;
  display:flex;align-items:center;gap:16px}
.topnav .brand{font-weight:700;color:var(--fg)}
.topnav .crumbs{color:var(--muted);font-size:13px}
.hash-jump{display:flex;align-items:center;margin-left:auto}
.hash-jump .input{width:150px;padding-top:4px;padding-bottom:4px;font-size:12px}
```

4b. 删除现第 237-238 行的主题切换按钮规则及其注释（样式改由 `.topnav-actions button` 统一提供）：

```css
/* 主题切换按钮（topnav 右侧） */
.theme-toggle{border:none;background:none;cursor:pointer;font-size:16px;line-height:1;padding:0}
```

4c. 在「⋯ 操作菜单」区的 `.menu-pop button.del{color:var(--red)}` 之后追加：

```css
/* topnav ⋯ 菜单：header 功能入口统一收纳（桌面+移动同构），面板内 a/按钮一律拍平成菜单项 */
.topnav-menu-btn{opacity:.6;font-size:18px;padding:4px 8px}
.topnav-menu-btn:hover,.topnav-menu-btn:focus-visible{opacity:1}
.topnav-actions{min-width:180px}
.topnav-actions a,.topnav-actions button{display:block;width:100%;text-align:left;
  border:none;background:none;cursor:pointer;font:inherit;font-size:13px;font-weight:400;
  padding:7px 10px;border-radius:4px;color:var(--fg);white-space:nowrap;text-decoration:none}
.topnav-actions a:hover,.topnav-actions button:hover{background:var(--surface-2)}
.topnav-actions .danger{color:var(--red)}
```

特异性说明（不用写进注释）：`.topnav-actions a/button`（0,1,1）压过 `.btn`/`.btn-outline`/`.btn-link`/`.theme-toggle`（0,1,0），而 `.btn-outline.danger`、`.btn-link.danger`（0,2,0）的红色保留——危险项和「退出对比」的 danger 态自动仍是红色。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_topnav_menu.py tests/test_theme.py::test_theme_toggle_present -v`
Expected: 全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add tests/test_topnav_menu.py app/templates/base.html app/static/style.css
git commit -m "header 三点菜单收纳：功能入口与主题切换统一进 ⋯ 弹出面板

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 窄屏三行布局

**Files:**
- Modify: `app/static/style.css`（`@media (max-width: 720px)` 内 header 段）

**Interfaces:**
- Consumes: Task 1 的 DOM 顺序 brand → crumbs → hash-jump → `.menu`。
- Produces: 窄屏三行——logo+⋯ / 面包屑 / 哈希框。纯 CSS，无对外接口。

- [ ] **Step 1: 重写媒体查询里的 header 段**

把 `@media (max-width: 720px)` 开头的 header 段（现内容如下）：

```css
  /* header：品牌+主题切换占首行，面包屑/上下文链接/哈希框依次换行 */
  .topnav{flex-wrap:wrap;padding:8px 12px;gap:6px 12px}
  .topnav .theme-toggle{order:1;margin-left:auto}
  .topnav .crumbs{order:2;flex-basis:100%;font-size:12px}
  .topnav .ctx{order:3;flex-basis:100%;margin-left:0;flex-wrap:wrap;row-gap:6px}
  .topnav .hash-jump{order:4;flex-basis:100%}
  .hash-jump .input{width:100%}
```

替换为：

```css
  /* header 三行：logo+⋯菜单 / 面包屑 / 哈希框（功能入口都在 ⋯ 菜单里） */
  .topnav{flex-wrap:wrap;padding:8px 12px;gap:6px 12px}
  .topnav .menu{margin-left:auto}
  .topnav .crumbs{order:2;flex-basis:100%;font-size:12px}
  .topnav .hash-jump{order:3;flex-basis:100%;margin-left:0}
  .hash-jump .input{width:100%}
```

（brand 与 `.menu` 保持默认 `order:0` 占首行，`.menu` 靠 `margin-left:auto` 顶到右端；crumbs、hash-jump 各占整行。）

- [ ] **Step 2: 快速回归**

Run: `pytest tests/test_topnav_menu.py tests/test_theme.py -v`
Expected: 全部 PASS（纯 CSS 改动，守护性运行）。

- [ ] **Step 3: Commit**

```bash
git add app/static/style.css
git commit -m "窄屏 header 改三行：logo+⋯ / 面包屑 / 哈希框

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 修复点击 ctxlinks 元素的 UI 测试 + 全量基线

**Files:**
- Modify: `tests/test_compare_ui.py`
- Modify: `tests/test_cross_compare_ui.py`

**Interfaces:**
- Consumes: `.topnav-menu-btn`（Task 1）。`[data-testid=compare-toggle]` 现在藏在关闭的菜单里，点它之前必须先点 ⋯ 展开；点完任意菜单项菜单会自动关闭，所以**每次**点 toggle 前都要重新展开。
- 只读断言（`inner_text`、`to_have_class`、`get_attribute`）对隐藏元素照常工作，不用动。

- [ ] **Step 1: 两个文件各加辅助函数**

在 `tests/test_compare_ui.py` 和 `tests/test_cross_compare_ui.py` 的 import 之后各加：

```python
def _open_menu(page):
    """功能入口收在 header ⋯ 菜单里（2026-07-18 设计），点击前先展开。"""
    page.click(".topnav-menu-btn")
```

- [ ] **Step 2: 每处点击 compare-toggle 前插入展开**

`tests/test_compare_ui.py` 共 8 处，逐一在前面插入 `_open_menu(page)`：

- `test_compare_mode_select_two_and_go`：第 27 行 `toggle.click()` 前、第 47 行 `toggle.click()` 前（各一次）。
- `test_compare_mode_click_node_does_not_navigate`：第 55 行 `page.click("[data-testid=compare-toggle]")` 前。
- `test_exit_compare_button_is_danger_styled`：第 78 行与第 80 行两次 `toggle.click()` 前各一次。
- `test_compare_bar_shows_immediately_with_count_and_disabled_go`：第 88 行前。
- `test_compare_go_href_and_aria_disabled_before_two_selected`：第 108 行前。
- `test_hard_change_disabled_in_compare_mode`：第 133 行前。

`tests/test_cross_compare_ui.py` 共 3 处：

- `test_cross_button_enabled_only_with_exactly_one_selected`：第 38 行前。
- `test_cross_button_absent_without_sibling`：第 64 行前。
- `test_cross_flow_lands_on_compare_with_sibling_default`：第 71 行前。

示例（第一处改完的样子）：

```python
    toggle = page.locator("[data-testid=compare-toggle]")
    # 默认按钮文案是「对比节点」
    assert toggle.inner_text().strip() == "对比节点"
    _open_menu(page)
    toggle.click()
```

- [ ] **Step 3: 跑这两个文件**

Run: `pytest tests/test_compare_ui.py tests/test_cross_compare_ui.py -v`
Expected: 全部 PASS。若某处因菜单未开而 timeout，检查是不是漏插了 `_open_menu`。

- [ ] **Step 4: 全量测试**

Run: `pytest`
Expected: ~491 passed（数字随本计划新增测试 +2）。若只有 `tests/test_rename_ui.py` 失败，单独重跑 `pytest tests/test_rename_ui.py`，过了就不算回归。若其他 UI 测试因点不到 ctxlinks 里的元素失败，用同样手法在点击前插入展开菜单步骤（加同样的 `_open_menu` 辅助函数）。

- [ ] **Step 5: Commit**

```bash
git add tests/test_compare_ui.py tests/test_cross_compare_ui.py
git commit -m "test：对比相关 UI 测试适配 header ⋯ 菜单（点击前先展开）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 文档同步 + 目检

**Files:**
- Modify: `docs/前端风格指南.md`（组件清单 + 页面骨架说明）

**Interfaces:** 无代码接口；纯文档与人工验收。

- [ ] **Step 1: 更新风格指南**

- 第 56 行的 block 说明：`ctxlinks`（右侧上下文链接/主操作按钮）改为 `ctxlinks`（header ⋯ 菜单里的功能入口；内容会被拍平成纵向菜单项，danger 类保持红色）。
- 组件清单表格（`.hash-jump` 附近）加一行：

```markdown
| `.topnav-actions` | header ⋯ 菜单弹出面板（`.menu-pop` 变体；`ctxlinks` 块与主题切换都收纳于此，内部 a/按钮自动拍平为菜单项） |
```

- [ ] **Step 2: 两套主题 × 两种宽度目检**

启动 `uvicorn app.main:app --reload`，用浏览器（DevTools 375px + 桌面宽度 × light/dark 四种组合）检查：首页、状态图页、节点详情页、硬更改详情页——⋯ 菜单展开收起、菜单项 hover、危险项红色、「退出对比」danger 态、点外部关闭。发现问题按设计文档修正。

- [ ] **Step 3: Commit**

```bash
git add docs/前端风格指南.md
git commit -m "docs：风格指南同步 header ⋯ 菜单收纳约定

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
