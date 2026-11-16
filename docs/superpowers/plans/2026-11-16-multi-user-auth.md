# 多用户登录与按单板授权 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Reflow 加上不开放注册的多用户登录，并按单板授予所有者 / 可编辑 / 只读三种角色。现有单板在第一个系统管理员诞生时归他所有。

**Architecture:** 标准库 scrypt 存密码，随机令牌放 cookie、sha256 进 `sessions` 表。自写中间件负责「是否登录」和 CSRF；`app/authz.py` 的守卫表负责「这块板你是什么角色」，表里没有的路由直接 500。折叠引擎和传播算法不感知权限，传播只多传一个 `actor_user_id`。

**Tech Stack:** FastAPI / Starlette 1.2.1、SQLite（WAL）、Jinja2 + HTMX + Alpine.js、pytest、Playwright。不新增运行时依赖。

**设计来源：** `docs/superpowers/specs/2026-11-16-multi-user-auth-design.md`。文中的矩阵、文案、未决问题以该文档为准。未决问题没有改判之前，按该文档的建议实现，不要在代码里另选一套。

## Global Constraints

- 用中文：代码注释、docstring、UI 文案、错误消息。
- 纯逻辑 `app/auth.py` 不 import FastAPI、不碰 SQLite。权限判断可以读库，放在 `app/authz.py` 和 `app/models.py`，不要写进 `bom_engine` / `csv_import` / `validation` / `component_lint` / `propagation` 的算法里。
- `TemplateResponse` 用新签名 `templates.TemplateResponse(request, "name.html", {context})`，context 里不要放 `"request"` 键。
- 改 `app/templates/`、`app/static/` 之前必读 `docs/前端风格指南.md`。只用已有 CSS 变量和组件；登录页、403、成员页、用户管理页要看白天、夜间、≤720px。
- HTMX 未登录或要整页跳转时，返回 **200 + `HX-Redirect`**，不要把 `HX-Redirect` 放在 401/403 上。局部写操作被拒绝时返回 200 + `HX-Reswap: none` + `HX-Trigger` toast。`json.dumps` 保持默认 ASCII 转义。
- 模板向 JS 传值用 `|tojson`，属性用单引号。
- 时间继续用 `models._now()` 的 UTC 格式。
- TDD：每个任务先写失败测试，跑红，再实现，再跑绿，再提交。
- 动手前在仓库根目录跑一次 `pytest`，记下通过数，当作本分支的基线。不要引用 CLAUDE.md 里过时的数字。
- 阶段 1 合并前，业务路由只要求「已登录」，不检查单板角色。不要在这个阶段创建普通用户的入口。
- 一个阶段做完再开始下一个。每阶段结束全量 `pytest` 为绿，并且该阶段的提交可以独立部署。

---

## 文件结构

与设计文档「需要修改的文件」一致。阶段归属：

| 阶段 | 新建 | 主要修改 |
|---|---|---|
| 1 | `app/auth.py`、`app/authz.py`、`app/cli.py`、`app/routes/auth.py`、`app/templates/login.html`、`app/templates/password.html`、`tests/helpers.py`、`tests/test_auth.py`、`tests/test_accounts.py`、`tests/test_authz.py`、`tests/test_auth_routes.py` | `app/db.py`、`app/models.py`、`app/main.py`、`tests/conftest.py`、现有会访问页面的测试、`README.md`、`deploy.sh` 注释 |
| 2 | `app/routes/members.py`、`app/templates/members.html`、`app/templates/forbidden.html`、`tests/test_permissions.py`、`tests/test_auth_ui.py` | 守卫表收紧；`hierarchy.py`、`board.py`、`log.py`、`hard_change.py`、`hashes.py`；首页、状态图、节点页及片段、硬更改模板；`style.css` |
| 3 | `app/routes/admin_users.py`、`app/templates/admin_users.html`、`tests/test_admin_routes.py` | `base.html` 增加「用户管理」入口 |
| 4 | 无 | `app/audit.py`、`app/propagation.py`、`app/db.py`（加列）、`log.html`、写日志的路由、`tests/test_audit.py`、`CLAUDE.md` |

`CLAUDE.md` 放到阶段 4 一起改：届时「多用户不做」这句已经不成立，守卫表和 HTMX 约定也稳定了。阶段 1 的 README 只写部署和「登录还不是授权」。

---

## 阶段 1 — 登录闸门

### Task 1: 用户、会话、成员三张表

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Produces: `users`、`sessions`、`board_members` 三张表，以及部分唯一索引 `board_one_owner`。`connect()` 使用 `sqlite3.connect(path, timeout=5.0)`，并执行 `PRAGMA journal_mode=WAL`。`init_db` 仍先 `SCHEMA` 再 `_migrate`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_accounts.py
import sqlite3
import pytest
from app.db import connect, init_db


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def test_auth_tables_exist(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "sessions", "board_members"} <= names


def test_only_one_owner_per_board(conn):
    conn.execute(
        "INSERT INTO users(username,password_hash,is_admin,created_at)"
        " VALUES('a','x',1,'t'),('b','x',0,'t')")
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v','bom','1')")
    conn.execute(
        "INSERT INTO board_members(board_id,user_id,role,created_at) VALUES(1,1,'owner','t')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO board_members(board_id,user_id,role,created_at) VALUES(1,2,'owner','t')")


def test_connect_enables_wal(tmp_path):
    path = tmp_path / "t.sqlite"
    c = connect(str(path))
    init_db(c)
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_accounts.py -v`
Expected: FAIL，表不存在。

- [ ] **Step 3: 把设计文档里的三张表和索引追加到 `SCHEMA`，并让 `connect` 打开 WAL 与 timeout**

`_migrate` 里再执行一遍同样的 `CREATE TABLE IF NOT EXISTS` 和 `CREATE UNIQUE INDEX IF NOT EXISTS`，这样已有的 `reflow.sqlite` 在下次 `init_db` 时也能补上。不要重建 `edit_log`。

- [ ] **Step 4: 再跑测试，通过后提交**

```bash
pytest tests/test_accounts.py -v
git add app/db.py tests/test_accounts.py
git commit -m "新增用户、会话和单板成员表"
```

---

### Task 2: 密码、登录名、角色与 next 的纯逻辑

**Files:**
- Create: `app/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces:
  - `hash_password(password: str) -> str` 格式 `scrypt$16384$8$1$<salt>$<hash>`，salt 与 hash 用 `base64.urlsafe_b64encode` 去掉填充
  - `verify_password(password: str, stored: str) -> bool`
  - `DUMMY_PASSWORD_HASH: str` 模块级常量，供「用户不存在」时照样算一次
  - `normalize_username(raw: str) -> str` 非法时抛 `ValueError`，消息是中文（「登录名需为 3–32 位小写字母、数字、下划线或连字符，且以字母或数字开头」）。比较前 `strip().lower()`
  - `role_at_least(have: str | None, need: str) -> bool`
  - `safe_next(raw: str | None) -> str` 不合法时返回 `"/"`

- [ ] **Step 1: 写失败测试**

覆盖：往返成功、错密码为假、`verify_password` 读取哈希串里的 n/r/p 而不是忽略它们（造一条 n 不同的合法串，用对的密码应成功）、空用户名、大写被折成小写、`role_at_least(None, "viewer")` 为假、`role_at_least("owner", "editor")` 为真、`safe_next("/board/1")` 原样返回、`safe_next("//evil")` 与 `safe_next("https://evil")` 都是 `"/"`。

- [ ] **Step 2: 跑红**

Run: `pytest tests/test_auth.py -v`

- [ ] **Step 3: 实现 `app/auth.py`**

`verify_password` 在格式拆不开时返回 False，不要抛。比较用 `hmac.compare_digest`。salt 用 `secrets.token_bytes(16)`。

- [ ] **Step 4: 跑绿并提交**

```bash
git commit -m "加入密码哈希与登录名的纯逻辑"
```

---

### Task 3: 账号、会话、认领无主单板

**Files:**
- Modify: `app/models.py`、`tests/test_accounts.py`

**Interfaces:**
- Produces:
  - `create_user(conn, username, password, is_admin: bool) -> int` 用户名为空库时 `is_admin` 必须为真，否则 `ValueError("第一个账号必须是系统管理员")`。密码少于 8 位 `ValueError("密码至少 8 位")`。用户名走 `normalize_username`。
  - `get_user_by_username(conn, username) -> Row | None`
  - `create_session(conn, user_id, days: int = 14) -> str` 返回 cookie 原文，库中存 sha256 十六进制
  - `user_by_session_token(conn, token: str) -> Row | None` 过期、用户已停用、找不到都返回 None
  - `delete_session(conn, token: str) -> None`
  - `delete_sessions_for_user(conn, user_id) -> None`
  - `assign_boards_without_owner(conn, user_id) -> int`
  - `backfill_owners_if_unassigned(conn) -> int`
  - `create_board` 增加关键字参数 `owner_user_id: int | None = None`。非 None 时在**同一事务**写 owner 行。现有调用方不传就不写（阶段 1 的路由会传；单测里直接造板的旧代码保持可用）。

- [ ] **Step 1: 在 `tests/test_accounts.py` 追加失败测试**

至少：

- 先插入两块板（不写成员），`create_user(..., is_admin=True)` 之后这两块板的 owner 都是他；再 `create_user` 一个管理员，owner 不变。
- `users` 为空时 `create_user(..., is_admin=False)` 抛中文 `ValueError`。
- `create_session` 后 `user_by_session_token` 能取回用户；把 `expires_at` 改成过去的时间再取，得到 None；`disabled_at` 写上之后也得到 None。
- `backfill_owners_if_unassigned` 在已经有 owner 时返回 0。
- `create_board(..., owner_user_id=用户)` 后成员表有一行 owner。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

第一个用户创建成功后调用 `assign_boards_without_owner`。`init_db` 末尾调用 `backfill_owners_if_unassigned`。会话过期判断用与 `models._now()` 相同的字符串比较（都是 UTC、秒精度、固定格式，字符串序即时间序）。

sha256 用 `hashlib.sha256(token.encode()).hexdigest()`。

- [ ] **Step 4: 跑绿并提交**

```bash
git commit -m "实现账号、会话和无主单板认领"
```

---

### Task 4: CLI 与环境变量引导

**Files:**
- Create: `app/cli.py`
- Modify: `app/main.py`（`init_db` 之后的引导放在 `get_conn` 所在的启动路径里，见 Step 3）
- Test: `tests/test_accounts.py` 或 `tests/test_cli.py`

**Interfaces:**
- `app.cli.main(argv: list[str] | None = None) -> int`
- 子命令 `create-admin --username NAME`、`assign-orphans --username NAME`。`create-user` 留到阶段 2 再挂上，避免阶段 1 就能造出「登录后等于超级用户」的普通账号。
- 密码只从 `getpass.getpass` 读两次，不一致则退出码 2，stderr 是中文。
- `bootstrap_admin_from_env(conn) -> bool`：两个变量都在、用户表为空、用户名合法、密码不少于 8 位时创建管理员并认领无主板，返回 True。任一条件不满足返回 False，不抛，让进程继续。

- [ ] **Step 1: 写失败测试**

用 `monkeypatch.setattr("getpass.getpass", lambda prompt="": "correct-horse")` 测 `create-admin` 在空库上造出管理员并领走预先插入的板。密码两次不一致时不插入用户。

`bootstrap_admin_from_env`：变量齐全时造出用户；只有用户名没有密码时不造；库里已有用户时即使变量还在也不改密码（断言哈希不变）。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现 CLI，并在 `get_conn()` 里 `init_db` 之后调用一次 `bootstrap_admin_from_env`**

`get_conn()` 每个请求都会跑。引导函数必须自己先看用户表，空了才创建，避免每次请求都做无用的插入尝试。日志用 `logging`，不要打印密码。

`app/cli.py` 底部提供 `main()`，用 `python -m app.cli` 启动（这一形式走的是模块本身，不必再加 `app/__main__.py`）。

- [ ] **Step 4: 跑绿并提交**

```bash
git commit -m "增加创建管理员的命令和一次性环境变量引导"
```

---

### Task 5: 会话中间件、CSRF、登录与登出

**Files:**
- Modify: `app/authz.py`（本任务先建文件）、`app/main.py`、`app/routes/auth.py`、`app/templates/login.html`、`app/templates/password.html`、`app/templates/base.html`
- Test: `tests/test_auth_routes.py`、`tests/test_authz.py`

**Interfaces:**
- 中间件类 `SessionAuthMiddleware`。
- 白名单：`("GET","/login")`、`("POST","/login")`、`("GET","/healthz")`、`("GET","/version")`，外加路径前缀 `/static`。
- 阶段 1 守卫表：`create_app()` 里现有每一个路由（方法 + `route.path`）登记为 `"login"`，再加上 `POST /logout`、`GET /account/password`、`POST /account/password` 为 `"login"`。
- `issue_csrf(response)` / `csrf_ok(request) -> bool`。cookie 名 `reflow_csrf`，表单字段和头都叫设计文档里的名字。
- `unauthenticated_response(request) -> Response`。

- [ ] **Step 1: 写失败测试**

`tests/test_authz.py` 遍历路由，断言每个 `(method, path)` 都在白名单或守卫表里，且守卫表没有多余键。这个测试在 Task 5 结束时才转绿，可以先提交成预期失败的文件，但不要带着失败测试结束本任务。

`tests/test_auth_routes.py`：

- 不带 cookie 的 `GET /` 是 303，`Location` 以 `/login` 开头。
- `GET /healthz`、`GET /version` 仍是 200。
- `GET /login` 在零用户时正文含 `create-admin`，且没有 `type="password"` 的输入框。
- 引导出管理员后，错误密码的正文是「用户名或密码错误」，响应里没有 `reflow_session`。
- 正确密码后 `GET /` 为 200。
- `next=https://evil.example` 登录成功后落到 `/`。
- 带 `HX-Request: true` 的未登录 `POST /board/1/commit`（板可以不存在）是 200，且有 `HX-Redirect`，正文不是一整页登录 HTML。
- 登录后的 POST 不带 CSRF 被拒，toast 或正文含「页面已过期」。
- `POST /logout` 之后再 `GET /` 又是 303。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

`create_app()` 增加 `docs_url=None, redoc_url=None, openapi_url=None`。

中间件顺序：最先进入会话解析，再 CSRF，再未登录拦截，再按守卫表检查。阶段 1 的 `"login"` 在用户非空时直接放行。用户是 `request.state.user`。

登录 POST：取出用户后先 `verify_password`。用户不存在时对 `DUMMY_PASSWORD_HASH` 再验一次传入的密码，然后返回同样的错误页。`disabled_at` 非空且密码正确时，文案「账号已停用，请联系管理员」。成功则 `create_session`，`Set-Cookie`，303 到 `safe_next`。

`base.html`：

```javascript
document.body.addEventListener('htmx:configRequest', function (e) {
  var m = document.cookie.match(/(?:^|; )reflow_csrf=([^;]*)/);
  if (m) e.detail.headers['X-CSRF-Token'] = decodeURIComponent(m[1]);
});
```

退出是 ⋯ 菜单里的 `<form method="post" action="/logout">` 加隐藏域。阶段 1 还没有「当前用户名」以外的成员入口；用户名可以从 `request.state.user` 注入模板全局变量。在 `create_app` 里给 Jinja 一个函数 `current_username(request)` 不合适——模板拿不到 request 以外的上下文时，用中间件在 `request.state` 放用户，再在一个很小的上下文处理器里注入。Starlette/Jinja2Templates 没有内置上下文处理器。做法：包一层 `TemplateResponse`，自动把 `user` 放进 context。放在 `app/main.py`，所有路由改用它。context 里的键叫 `user`，不要叫 `request`。

登录页不 `{% extends "base.html" %}`。自己链 `style.css`，中间一个 `.panel`。

改密页：校验当前密码，成功后更新哈希，删掉该用户**其他**会话（保留这一次请求的令牌）。阶段 1 就可以做，避免管理员拿到初始密码后没有改口的地方。

非 htmx 的现有 POST 表单（`board_new.html`、节点页 commit、`edit-info`、`hard_change_form.html`）本阶段就要加上 `csrf_token` 隐藏域，否则阶段 1 一开 CSRF，这些表单全部失效。隐藏域的值由页面上下文提供：中间件把生成的 token 放在 `request.state.csrf`，包过的 `TemplateResponse` 注入 `csrf_token`。

- [ ] **Step 4: 路由测试与守卫表测试转绿**

现有业务测试此时期待仍会大面积失败（没有登录）。那是 Task 6 的事。本任务只要求 `tests/test_auth_routes.py` 和 `tests/test_authz.py` 通过。

- [ ] **Step 5: 提交**

```bash
git commit -m "加上登录会话、CSRF 和失败关闭的路由守卫表"
```

---

### Task 6: 让现有测试以管理员身份通过

**Files:**
- Create: `tests/helpers.py`
- Modify: `tests/conftest.py`，以及所有直接 `TestClient(create_app())` 或 `httpx.Client` 去打业务路由的测试（设计文档列了清单；以 `rg "TestClient\\(create_app" tests` 和 `rg "httpx.Client" tests` 的结果为准）

**Interfaces:**
- `auth_client(tmp_path, monkeypatch, *, username="admin", password="test-password", is_admin=True) -> TestClient`
  设置 `REFLOW_DB`，创建应用。若还没有这个用户就插入并登录。返回的客户端，其 `post` / `delete` 自动附上 CSRF 头（包一层或在 fixture 里用事件钩子，不要让每个调用点手写 token）。
- `live_server` 的环境增加 `REFLOW_BOOTSTRAP_USERNAME=admin` 和 `REFLOW_BOOTSTRAP_PASSWORD=test-password`。
- Playwright `context` fixture：用 httpx 对 `live_server` 登录，把 `reflow_session` 和 `reflow_csrf` 写进浏览器上下文。
- `insert_chain`、`seeded_server` 以及各 UI 测试里的 httpx 建板请求，改成携带同一组 cookie 和 CSRF 头。抽一个 `auth_httpx(base_url)` 放在 `tests/helpers.py`，避免每个文件复制登录过程。

- [ ] **Step 1: 先跑全量 pytest，确认失败集中在 303 到 `/login`，而不是业务断言本身**

- [ ] **Step 2: 换上夹具后再跑全量**

Expected: 与 Task 6 开始前记下的基线一致（只多出本阶段新增的测试）。`/version` 的匿名测试保持匿名，并在 `tests/test_auth_routes.py` 里已经覆盖「匿名不能看首页」。

Playwright 若本环境没有浏览器，安装项目已声明的 `pytest-playwright` 所需浏览器再跑 `tests/test_theme.py` 里一条，确认 cookie 注入后能打开 `/board/new` 而不是登录页。

- [ ] **Step 3: 更新 README 和 deploy.sh 注释**

README 增加一节「第一个管理员」：CLI 为主，环境变量为辅，写明用完删除变量、此阶段登录后的权限仍与单人使用相同、不要创建普通用户。备份一节改为 `sqlite3.Connection.backup` 的示例，说明 WAL 下不要直接 `cp` 正在使用的库文件。

`deploy.sh` 的 `docker run` 参数不增加口令。在该段上方用注释给出可选变量的例子。

- [ ] **Step 4: 全量 pytest 通过后提交**

```bash
git commit -m "现有测试改为已登录，并写明首次创建管理员的步骤"
```

阶段 1 到此可合并。合并说明里写清楚：未登录被挡住；任一登录用户仍能改所有板。

---

## 阶段 2 — 按单板授权

从阶段 1 已经合并的 master 再开分支。不要在阶段 1 的分支上把角色检查一起做完又分成两个 PR——守卫表的语义会对不上。

### Task 7: 收紧守卫表

**Files:**
- Modify: `app/authz.py`、`tests/test_authz.py`
- Test: `tests/test_permissions.py`（本任务先写「角色不够会被拒」的骨架，页面差异在 Task 9）

**Interfaces:**
- 守卫值增加 `viewer` / `editor` / `owner` / `admin` / `custom`。
- `load_membership(conn, user_id, board_id) -> str | None` 放在 `models`。
- `enforce(request, conn) -> Response | None`：返回 None 表示通过；否则是设计文档那张 HTMX 表里的响应。单板 id 从路径参数取，参数名是 `board_id`。没有这个参数的路由不能登记成三种角色之一。

按设计文档的矩阵填表。下面这些用 `custom`，处理函数里自己调 `authz`，不要在中间件猜：

- `GET /`、`GET/POST /board/new`、`POST /board/new/preview`（`login` 即可，但建板的「能否复用 BOM 版本」在处理函数里）
- `DELETE /board-group`、`DELETE /bom-version`、两级 rename 以外的 `POST /board-group/rename`、`POST /pcb-version/rename`、`POST /bom-version/rename`
- `GET /hash/{value}`、`GET /hash-lookup`
- `POST /board/{board_id}/node/{node_id}/edit` 仍登记为 `editor`（先挡住 viewer）；根节点的「每块共用板都要是 editor」在 `edit_node` 里面额外查
- `GET /board/{board_id}/compare` 与 `compare/download` 登记为 `viewer`（先保证对本板有读权限）；兄弟板可见性在处理函数里过滤

`/admin/users` 的路由阶段 3 才存在。阶段 2 不要预先登记一条还不存在的路由，否则 Task 5 的「表与路由一致」会红。阶段 3 加路由时一起登记为 `admin`。

- [ ] **Step 1: 写失败测试**

`tests/test_permissions.py` 用 `auth_client` 造 owner、editor、viewer 三个用户和一块板（owner 建板）。断言：

- viewer `GET /board/{id}` 200
- viewer `POST .../edit` 带 `HX-Request` 得到 toast「没有权限做这个操作」，且 changeset 仍为空
- editor `POST .../commit` 在有一条修改时成功（沿用现有建改的帮助函数）
- editor `DELETE /board/{id}` 被拒，板还在
- 另一个用户（哪块板都不是成员）`GET /board/{id}/node/{nodeId}` 是 403，正文含 owner 的登录名
- 不存在的 id `GET /board/999999` 是 404

- [ ] **Step 2: 跑红**（阶段 1 的守卫全是 `login`，viewer 的 POST 现在会成功）

- [ ] **Step 3: 填表并实现 `enforce`**

404 与 403 的顺序：先 `get_board`，没有则 404；有则看角色。节点不属于该板的检查仍留在各个处理函数里（成员已经通过了单板这一关）。

`owner` 的删除、重命名单板 ID、成员路由在 Task 8 之前，删除和重命名的守卫先生效。成员路由随 Task 8 一起登记。

- [ ] **Step 4: 本任务的新测试转绿。全量先跑，修因为「测试用户不是 owner」而新出现的失败**

阶段 1 的 `auth_client` 默认管理员建板时已经会写 owner（Task 3 的 `create_board`）。确认测试夹具建板走的是路由而不是直接插表。直接插 `boards_hierarchy` 的测试要补成员行，或改走 `create_board(..., owner_user_id=)`。

- [ ] **Step 5: 提交**

```bash
git commit -m "按单板角色拒绝无权的读写"
```

---

### Task 8: 成员管理与转让

**Files:**
- Create: `app/routes/members.py`、`app/templates/members.html`
- Modify: `app/models.py`、`app/authz.py`、`app/main.py`、`state_graph.html`（菜单入口；只读隐藏放到 Task 9 一起也行，入口可以本任务先加）
- Test: `tests/test_permissions.py` 追加

**Interfaces:**
- `add_member(conn, board_id, username, role) -> None` 角色只能是 editor 或 viewer。找不到用户、已停用、已是成员都抛 `ValueError`，消息给页面用。
- `set_member_role(conn, board_id, user_id, role) -> None` 不能改 owner 那一行。
- `remove_member(conn, board_id, user_id) -> None` 不能移除 owner。
- `transfer_ownership(conn, board_id, from_user_id, to_user_id) -> None` 同一事务：目标必须已是本板成员且未停用；`from` 变成 editor，`to` 变成 owner。利用部分唯一索引时要先把原 owner 降级再升级，否则中间态会有两名 owner。

路由（全部登记 `owner`）：

- `GET /board/{board_id}/members`
- `POST /board/{board_id}/members` 表单 `username`、`role`
- `POST /board/{board_id}/members/{user_id}` 表单 `role`
- `POST /board/{board_id}/members/{user_id}/remove`
- `POST /board/{board_id}/transfer` 表单 `user_id`

- [ ] **Step 1: 写失败测试**

owner 添加 viewer 后，该 viewer 能打开状态图。editor 打开成员页 403。转让后原 owner 再 `DELETE` 板被拒，新 owner 可以。把 owner 那一行 POST 成 viewer 被拒，库里仍有且仅有一名 owner。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现页面**

复用 `.panel`、`.input`、`.btn`、`.badge`。移除和转让带 `hx-confirm`，文案用设计文档里的句子。校验失败用 200 + 就地错误，不把整页刷掉。成功用 toast + 重新渲染这张表（hx-target 指向成员表容器）。

状态图 `ctxlinks` 在 `role == "owner"` 时加「成员」。`role` 由 `state_graph` 放进模板上下文。

- [ ] **Step 4: 跑绿并提交**

```bash
git commit -m "所有者可以管理成员并转让所有权"
```

---

### Task 9: 首页过滤、只读界面、共用初始 BOM、跨范围删除

**Files:**
- Modify: `app/routes/hierarchy.py`、`app/routes/board.py`、`app/models.py`
- Modify: `home.html`、`state_graph.html`、`node_detail.html`、`_edit_form.html`、`_bom_table.html`、`_changes_panel.html`、`_attachments.html`、`hard_change_detail.html`、`compare.html`（只确认数据已过滤）
- Create: `app/templates/forbidden.html`
- Test: `tests/test_permissions.py`

**Interfaces:**
- `list_visible_boards(conn, user_id)` 供首页使用，返回的结构仍能拼出现在的 `groups`，但只含该用户的板。`ref_count` 仍是该 BOM 版本的初始 BOM 位数（他能看见这个版本，才算得出来）。
- `user_owns_all(conn, user_id, *, board_name, pcb_version=None, bom_version=None) -> bool`
- `user_can_edit_initial_bom(conn, user_id, board_name, pcb_version, bom_version) -> bool` 该版本下每一块板的角色都 `>= editor`
- 新建单板：三元组尚不存在时，任何登录用户可建，`owner_user_id` 为当前用户。三元组已存在时，当前用户必须已是其中一块板的成员，否则 403，不插板。
- 下拉 `names` / `pcbs` / `boms` 只来自可见板。

- [ ] **Step 1: 写失败测试**

- 用户 A 的板不出现在用户 B 的首页 HTML 里。B 的首页空状态含「你还没有被加入任何单板」。
- A、B 两块板共用一个 BOM 版本。A 的 editor（不是 B 的成员）POST 根节点编辑，状态被拒，`initial_bom` 的值不变。把这个人加成 B 的 editor 之后，同一次编辑成功，两块板折叠出的该位号都是新值。
- A 是某单板名称下唯一可见板的 owner，但同名下还有别人的板。A 的首页不渲染「删除整组」；直接 `DELETE /board-group` 被拒，两块板都在。A `DELETE /board/{自己的}` 成功。
- viewer 的节点页 HTML 不含 `action=".../commit"`、不含 `"/edit"` 的 `hx-post`。含「你对这块单板是只读」。
- 非成员建板时复用别人的 BOM 版本，响应 403，`boards_hierarchy` 行数不变。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

根节点分支在 `edit_node` 里、调用 `update_initial_bom` 之前判断。拒绝时走与「没有权限」相同的 HTMX toast，不要先把初始 BOM 写进去再回滚——判断放在写之前。

首页按钮的显示条件调用同一个 `user_owns_all`，不要在模板里复刻 SQL。

403 页 `{% extends "base.html" %}`，`.empty` 里写设计文档的句子，所有者登录名由 `authz` 查出来传进模板。没有 owner 时用「请联系系统管理员」。

- [ ] **Step 4: 跑绿并提交**

```bash
git commit -m "首页按成员过滤，并挡住共用初始 BOM 的越权修改"
```

---

### Task 10: 哈希、对比、硬更改图片

**Files:**
- Modify: `app/routes/hashes.py`、`app/routes/board.py`（compare）、`app/routes/hard_change.py`、`app/main.py`、`hard_change_detail.html`
- Test: `tests/test_permissions.py`、必要时 `tests/test_hash_routes.py`

**Interfaces:**
- `find_hash_target` 增加 `allowed_board_ids: set[int]`。不在集合里的节点和硬更改当不存在。
- `list_sibling_boards` 的调用方在比较前滤掉当前用户不可见的 id。滤完之后原来的「节点不在兄弟集合」路径自然变成 404。
- `GET /board/{board_id}/hard-change/{hc_id}/images/{image_id}` 登记 `viewer`。核对图片行的 `hard_change_id` 与路径一致。
- 删除 `app.mount("/uploads", ...)`。

- [ ] **Step 1: 写失败测试**

- 只命中不可见板的短哈希，`/hash-lookup` 的 toast 与对空库查询一个合法但不存在的前缀相同（都含「未找到」）。
- viewer 能 `GET` 到硬更改图片的字节；非成员 403；`GET /uploads/{filename}` 是 404。
- 对比页 HTML 里不出现不可见兄弟板的 `board_uid`。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现并改模板里的图片地址**

搜 `"/uploads/` 确认没有遗漏的模板。节点附件本来就走自己的下载路由，不要改存储路径。

- [ ] **Step 4: 跑相关测试与全量 pytest**

现有硬更改 UI 测试若点到图片灯箱或 `href="/uploads/`，选择器改成新路由。

- [ ] **Step 5: 提交**

```bash
git commit -m "哈希、跨板对比和硬更改图片只对成员可见"
```

---

### Task 11: CLI `create-user` 与 Playwright

**Files:**
- Modify: `app/cli.py`（本任务才注册 `create-user`。它调用 Task 3 已有的 `create_user(..., is_admin=False)`，密码同样走 `getpass` 两次）
- Create: `tests/test_auth_ui.py`
- Modify: `README.md`（阶段 2 起普通用户用 CLI 创建，再由所有者加入单板）

- [ ] **Step 1: Playwright 失败测试（或先手写脚本再收成测试）**

`tests/test_auth_ui.py`：

- 新浏览器上下文不带管理员 cookie（不要用 conftest 里那个已登录的 `context`；这个文件自己建 context）。打开 `/`，看到登录页。密码错，看到「用户名或密码错误」。
- 用 viewer 的账号登录（先用 `auth_httpx` 把这个用户加进某块板），节点页有位号文本、没有 commit 框。
- 同一上下文 `goto` 另一块不是成员的板的节点 URL，正文是无权说明，不是 BOM 表。

登录页在 `data-theme=dark` 下密码框的背景亮度低于文字（把 `tests/test_theme.py` 的通道求和搬过来，选择器换成登录页的 `input[type=password]`）。

- [ ] **Step 2: 跑红，然后实现缺口**

UI 本身应在 Task 9 已经完成。本任务主要是把测试补上并修真正的偏差（文案、选择器、cookie）。

- [ ] **Step 3: 全量 pytest，含 Playwright**

- [ ] **Step 4: 提交**

```bash
git commit -m "补上只读与无权访问的界面测试"
```

更新 README：阶段 2 之后，登录用户只能看到自己的板；创建普通用户的命令；所有者在「成员」页授权。删掉阶段 1 那句「登录后人人都能改」，改成「若生产环境还停在只合并了阶段 1 的版本，则仍然人人都能改」——避免文档和代码各说各的。以本阶段合并后的行为作为 README 的主叙述。

阶段 2 可合并。

---

## 阶段 3 — 网页用户管理

### Task 12: 管理员的创建、停用、重置密码

**Files:**
- Create: `app/routes/admin_users.py`、`app/templates/admin_users.html`、`tests/test_admin_routes.py`
- Modify: `app/models.py`、`app/authz.py`、`app/main.py`、`app/templates/base.html`

**Interfaces:**
- `disable_user(conn, actor_id, target_id) -> None` 拒绝：actor 不是管理员；target 是 actor 自己；target 是最后一位 `disabled_at IS NULL AND is_admin = 1` 的用户。成功时写 `disabled_at` 并 `delete_sessions_for_user`。
- `enable_user(conn, target_id) -> None`
- `set_password(conn, target_id, password) -> None` 校验长度，更新哈希，吊销该用户全部会话。管理员重置别人用它；自己改密仍走阶段 1 的页面（保留当前会话）。

路由登记为 `admin`：

- `GET /admin/users`
- `POST /admin/users`
- `POST /admin/users/{user_id}/disable`
- `POST /admin/users/{user_id}/enable`
- `POST /admin/users/{user_id}/password`

- [ ] **Step 1: 写失败测试**

非管理员 GET 列表 403。管理员创建普通用户后，该用户能登录。停用后旧 cookie 访问首页得到 303，登录表单显示「账号已停用，请联系管理员」。停用自己被拒，会话仍在。两位管理员时可以停用其中另一位；只剩一位时停用被拒。重置密码后旧会话失效，新密码可以登录。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现页面**

顶栏 ⋯ 菜单在 `user.is_admin` 时显示「用户管理」。表单带 CSRF。停用按钮 `hx-confirm`，文案写明对方将不能登录。自己和最后一位管理员的停用按钮不渲染。

没有「删除用户」。

- [ ] **Step 4: 跑绿，全量 pytest，提交**

```bash
git commit -m "系统管理员可以在网页上创建和停用账号"
```

阶段 3 可合并。

---

## 阶段 4 — 审计操作人

### Task 13: edit_log 增加操作人

**Files:**
- Modify: `app/db.py`、`app/audit.py`、`app/propagation.py`、`app/models.py`（`list_board_log`）、`app/routes/board.py`、`app/templates/log.html`、`tests/test_audit.py`、相关传播测试若断言了列集合
- Modify: `CLAUDE.md`

**Interfaces:**
- `_migrate`：`PRAGMA table_info(edit_log)` 不含 `actor_user_id` 时 `ALTER TABLE edit_log ADD COLUMN actor_user_id INTEGER REFERENCES users(id)`。
- `record_edit(..., note=None, *, actor_user_id: int | None = None)`。星号后面的参数，保证现有位置参数调用不用改。
- `apply_node_edit`、`delete_node`、`resolve_conflict` 增加关键字参数 `actor_user_id: int | None = None`，传给该函数体内每一次 `record_edit`。
- 路由从 `request.state.user["id"]` 传入。根节点那条在 `board.py` 里直接调用的 `record_edit` 同样传入。

- [ ] **Step 1: 写失败测试**

`tests/test_audit.py`：写入时带 `actor_user_id`，`list_board_log` 的行上能读到用户名。不带时该字段为 None，日志页 HTML 含「（升级前）」。

传播：`resolve_conflict(..., actor_user_id=用户)` 后，`source='propagated'` 的那一行 `actor_user_id` 等于该用户。

路由：editor 改一个位号后，日志页表格里有他的登录名。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

日志表头在「来源」左侧加「操作人」。不要改 `edit_log` 的其他列语义。成员管理和用户管理不要写这张表。

`CLAUDE.md`：

- 删掉或改写「MVP 边界」里的「多用户/并发/权限」。改成指向本设计文档，并写明并发仍不是协同编辑，只是 WAL。
- 「约定」里加两条：新路由必须同时写进 `app/authz.py` 的守卫表；HTMX 未登录用 200 + `HX-Redirect`。
- 运行一节不必写口令。可加一句「首次使用见 README 的管理员初始化」。

- [ ] **Step 4: 全量 pytest 通过后提交**

```bash
git commit -m "审计日志记录操作人"
```

阶段 4 可合并。到这里设计文档里的建议方案全部落地。

---

## 自检

每个阶段合并前：

- [ ] `pytest` 全绿（含 Playwright；浏览器缺失要先装，不要把 UI 测试标成跳过）
- [ ] `rg "mount\\(\"/uploads\"" app` 无结果（阶段 2 之后）
- [ ] `rg "REFLOW_SECRET_KEY" app` 无结果
- [ ] 守卫表测试在，且新路由没有用「先合并再补登记」的方式送进去
- [ ] 登录页、成员页、用户管理页、只读节点页在白天和夜间都看过；375px 宽下登录表单不横滚
- [ ] README 的初始化步骤能在一个空目录的 sqlite 上走通：`REFLOW_DB=... python -m app.cli create-admin --username admin`，然后用该账号登录，看见迁移前就存在的板

## 不在本计划里的事

设计文档「非目标」和「未决问题」。未决问题一旦改判，先改设计文档的矩阵和本计划里对应任务的断言，再写代码。不要两处各实现一套。
