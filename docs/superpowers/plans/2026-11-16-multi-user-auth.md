# 多用户登录与按单板授权 实现计划

**Goal:** 给 Reflow 加上不开放注册的多用户登录，并按单板授予所有者 / 可编辑 / 只读三种角色。现有单板和现有 BOM 版本在第一个系统管理员诞生时归他所有。初始 BOM 只在 BOM 版本页修正，单板页的根节点只读。

**Architecture:** 标准库 scrypt 存密码，随机令牌放 cookie、sha256 进 `sessions` 表。自写中间件负责「是否登录」和 CSRF；`app/authz.py` 的守卫表负责「这块板你是什么角色」，表里没有的路由直接 500。折叠引擎不感知权限。传播算法不改判定，只多传 `actor_user_id` 和乐观锁的 revision。共享的 `initial_bom` 不拆表。

**Tech Stack:** FastAPI / Starlette 1.2.1、SQLite（WAL）、Jinja2 + HTMX + Alpine.js、pytest、Playwright。不新增运行时依赖。

**设计来源：** `docs/superpowers/specs/2026-11-16-multi-user-auth-design.md`（已定稿）。矩阵、文案和「已决定」一节就是实现依据。

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
| 2 | `app/routes/members.py`、`app/templates/members.html`、`app/templates/forbidden.html`、`app/templates/_scope_impact.html`、`tests/test_permissions.py`、`tests/test_auth_ui.py` | 守卫表收紧；`bom_versions`；根节点编辑改 403；`hierarchy.py`、`board.py`、`log.py`、`hard_change.py`、`hashes.py`；首页、状态图、节点页及片段、硬更改模板；去掉 `/uploads`；`style.css` |
| 3 | `app/routes/admin_users.py`、`app/templates/admin_users.html`、`tests/test_admin_routes.py` | `base.html` 增加「用户管理」；强制转让与 `reflow.admin` 日志 |
| 4 | 无 | `app/audit.py`、`app/propagation.py`、`app/db.py`（`actor_user_id`）、`log.html`、写日志的路由、`tests/test_audit.py` |
| 5 | `app/routes/bom_version.py`、`app/templates/bom_version.html`、`tests/test_bom_version.py` | 版本页单事务提交；根节点页的提示链接；`CLAUDE.md` 可放到本阶段末尾，此时「多用户不做」已经不成立 |
| 6 | `tests/test_revision.py` | `nodes.revision` / `revised_by`；各写路径和冲突弹窗；`propagation.py` 的事务边界 |

`CLAUDE.md` 放在阶段 5：版本页和审计列都已落地，写进去的约定不会马上过时。阶段 6 改完冲突弹窗后，再补一句乐观锁。

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
- `POST /board/{board_id}/node/{node_id}/edit` 登记为 `editor`（先挡住 viewer）。根节点在处理函数里一律 403，不写 `initial_bom`。本阶段不要做「editor 够了就可以改根节点」
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

### Task 9: 首页过滤、只读界面、根节点只读、负责人、影响清单

**Files:**
- Modify: `app/db.py`、`app/models.py`、`app/routes/hierarchy.py`、`app/routes/board.py`
- Modify: `home.html`、`state_graph.html`、`node_detail.html`、`_edit_form.html`、`_bom_table.html`、`_changes_panel.html`、`_attachments.html`
- Create: `app/templates/forbidden.html`、`app/templates/_scope_impact.html`
- Test: `tests/test_permissions.py`、`tests/test_accounts.py`（`bom_versions`）

**Interfaces:**
- `bom_versions` 表，定义见设计文档。`create_bom_version` 在写入 `initial_bom` 的同一事务里插入负责人。
- `ensure_bom_version(conn, board_name, pcb_version, bom_version, owner_user_id) -> int` 已有三元组则返回 id，不改负责人。
- 迁移与 `assign-orphans`：没有 `bom_versions` 行的三元组，负责人设为第一个管理员（或 CLI 指定的用户）。已有负责人的不动。
- `list_visible_boards(conn, user_id)` 只含该用户的板。`ref_count` 仍是该 BOM 版本的初始 BOM 位数。
- `user_owns_all(conn, user_id, *, board_name, pcb_version=None, bom_version=None) -> bool`
- 新建单板：三元组不存在时，任何登录用户可建，他是板的 owner，也是版本负责人。三元组已存在时，只有该版本负责人能建，新板 owner 是他；其他人 403，不插板。
- 下拉只来自可见板，加上自己担任负责人的版本。
- `edit_node` 在 `parent_id is None` 时返回 403，正文含「请到 BOM 版本页修正」，不调用 `update_initial_bom`。
- 跨范围重命名/删除：`user_owns_all` 失败则 403，且响应正文不含范围内他不是成员的 `board_uid`。通过之后返回 `_scope_impact.html`，列出每块板的 `board_uid`。删除的真正执行要多一个表单字段，值等于当前名称；不对就 400，数据不变。重命名在清单确认后才 `UPDATE`，并在同一事务里改 `bom_versions` 的对应列。

- [ ] **Step 1: 写失败测试**

- 用户 A 的板不出现在用户 B 的首页 HTML 里。B 的首页空状态含「你还没有被加入任何单板」。
- owner、editor、viewer 以及另一个版本的负责人，POST 根节点编辑都被拒，`initial_bom` 逐行不变。根节点 HTML 没有指向 `/edit` 的 `hx-post`。本阶段的拒绝文案只要说明根节点不能在单板页修改；「请到 BOM 版本页修正」这句话和页面上的链接在阶段 5 再锁定，那时版本页才存在。
- 已有版本下，普通成员建板 403，行数不变。负责人建板成功，且他是新板的 owner。全新三元组仍可由非负责人创建，创建后他是 `bom_versions.owner_user_id`。
- 只拥有同名分组中一块板的人，`DELETE /board-group` 被拒，响应不含另一块的 `board_uid`。范围内每块都是 owner 时，第一次请求返回清单且含全部 `board_uid`，未带对的确认名称则板还在；名称正确才删除。
- viewer 的非根节点页不含 `action=".../commit"`，含「你对这块单板是只读」。
- 现有会在根节点上 POST 编辑并期望写入 `initial_bom` 的测试（至少 `tests/test_lint_async.py` 里根节点那两条）改成期望 403。不要为了让旧断言继续通过而保留写入路径。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

403 页 `{% extends "base.html" %}`。没有 owner 时用「请联系系统管理员」。

首页按钮的显示条件调用 `user_owns_all`，不要在模板里复刻 SQL。影响清单用 `.modal`，删除输入框用 `.input`。

阶段 2 没有 `/bom-version/{id}`。根节点上的那句链接放到阶段 5，避免链到 404。本阶段的 403 文案可以先只说「根节点不能在单板页修改」。阶段 5 再改成「请到 BOM 版本页修正」，并补上链接。测试在阶段 2 锁定「不写 `initial_bom`」；文案后半句在阶段 5 收紧。

- [ ] **Step 4: 跑绿并提交**

```bash
git commit -m "按成员过滤首页，根节点不再修改初始 BOM"
```

---

### Task 10: 哈希、对比，以及全部文件出口

**Files:**
- Modify: `app/routes/hashes.py`、`app/routes/board.py`、`app/routes/hard_change.py`、`app/main.py`
- Modify: `app/templates/hard_change_detail.html`、`app/templates/hard_change_form.html`、`app/templates/compare.html`（只确认没有不可见板的链接）
- Test: `tests/test_permissions.py`、必要时 `tests/test_hash_routes.py`、`tests/test_node_attachments_routes.py`

设计文档「文件下载与静态路径」那张表是本任务的清单，不要只处理硬更改图片。

**Interfaces:**
- `find_hash_target` 增加 `allowed_board_ids: set[int]`。不在集合里的节点和硬更改当不存在。
- `list_sibling_boards` 的调用方在比较前滤掉当前用户不可见的 id。滤完之后原来的「节点不在兄弟集合」路径自然变成 404。`compare/download` 使用同一过滤。
- `GET /board/{board_id}/hard-change/{hc_id}/images/{image_id}` 登记 `viewer`。核对图片行的 `hard_change_id` 与路径一致，再 `FileResponse`。
- 附件下载路由登记 `viewer`，保留「附件不属于该节点则 404」。
- `import/template` 登记 `editor`。`download`（full 与 changes）登记 `viewer`。
- 删除 `app.mount("/uploads", ...)`。磁盘目录和 `storage.save_image` / `save_attachment` 的相对路径不变。

- [ ] **Step 1: 写失败测试**

- 只命中不可见板的短哈希，`/hash-lookup` 的 toast 与对空库查询一个合法但不存在的前缀相同（都含「未找到」）。
- 对比差异 CSV：一侧板不可见时 404，正文不含那块板的 `board_uid`。两侧都可见时 200，且是 CSV。
- viewer `GET` 硬更改图片鉴权路由，响应体等于磁盘文件字节；非成员 403；未登录是 303 到 `/login`。
- `hard_change_detail.html` 与 `hard_change_form.html` 渲染结果不含字面量 `/uploads/`。灯箱用的 `data-src` 是鉴权路由。
- viewer 下载节点附件得到字节。用该行的 `storage_path` 请求 `/uploads/<storage_path>` 得到 404。用硬更改的 `filename` 请求 `/uploads/<filename>` 也是 404。
- viewer 下载 `scope=full` 与 `scope=changes` 为 200。非成员 403。viewer 请求 `import/template` 为 403，editor 为 200。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

`rg '"/uploads/' app/templates` 必须为空。`rg 'mount\\("/uploads"' app` 必须为空。不要把节点附件改存到别的目录。

- [ ] **Step 4: 跑相关测试。硬更改 UI 测试里点灯箱或断言 `/uploads/` 的，改成新路由。**

- [ ] **Step 5: 提交**

```bash
git commit -m "下载与图片只经成员校验，关掉上传目录的静态挂载"
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

更新 README：阶段 2 之后，登录用户只能看到自己的板；创建普通用户的命令；所有者在「成员」页授权；单板页不能改根节点，初始 BOM 要等版本页（阶段 5）。删掉阶段 1 那句「登录后人人都能改」，改成「若生产环境还停在只合并了阶段 1 的版本，则仍然人人都能改」。以本阶段合并后的行为作为 README 的主叙述。

阶段 2 可合并。

---

## 阶段 3 — 网页用户管理

### Task 12: 管理员的创建、停用、重置密码与强制转让

**Files:**
- Create: `app/routes/admin_users.py`、`app/templates/admin_users.html`、`tests/test_admin_routes.py`
- Modify: `app/models.py`、`app/authz.py`、`app/main.py`、`app/templates/base.html`

**Interfaces:**
- `disable_user(conn, actor_id, target_id) -> None` 拒绝：actor 不是管理员；target 是 actor 自己；target 是最后一位 `disabled_at IS NULL AND is_admin = 1` 的用户。成功时写 `disabled_at` 并 `delete_sessions_for_user`。
- `enable_user(conn, target_id) -> None`
- `set_password(conn, target_id, password) -> None` 校验长度，更新哈希，吊销该用户全部会话。管理员重置别人用它；自己改密仍走阶段 1 的页面（保留当前会话）。
- `force_transfer_board(conn, actor_id, board_id, new_username) -> None` 只允许管理员。新用户必须存在且未停用。效果与普通转让相同：新用户成为 owner，原 owner 降为 editor。不读取 changeset，不返回 BOM。
- `force_transfer_bom_version(conn, actor_id, version_id, new_username) -> None` 只改 `bom_versions.owner_user_id`，不改各板成员。
- 两者成功后 `logging.getLogger("reflow.admin").info(...)`，字段为操作者 id、对象种类、对象 id、新用户 id。不写 `edit_log`。

路由登记为 `admin`：

- `GET /admin/users`
- `POST /admin/users`
- `POST /admin/users/{user_id}/disable`
- `POST /admin/users/{user_id}/enable`
- `POST /admin/users/{user_id}/password`
- `POST /admin/boards/{board_id}/transfer`
- `POST /admin/bom-versions/{version_id}/transfer`

- [ ] **Step 1: 写失败测试**

非管理员 GET 列表 403。管理员创建普通用户后，该用户能登录。停用后旧 cookie 访问首页得到 303，登录表单显示「账号已停用，请联系管理员」。停用自己被拒，会话仍在。两位管理员时可以停用其中另一位；只剩一位时停用被拒。重置密码后旧会话失效，新密码可以登录。

另用 `caplog`：停用某板 owner 后，管理员页 HTML 含 `board_uid` 和单板名称，不含该板初始 BOM 里的位号值，不含 `/board/{id}` 链接。强制转让后新用户是 owner，管理员 `GET /board/{id}` 仍是 403。`reflow.admin` 有一条 INFO。对 BOM 版本做同样的断言：页面有三个名称，没有位号；转让后 `owner_user_id` 变了，各板成员行数不变。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现页面**

顶栏 ⋯ 菜单在 `user.is_admin` 时显示「用户管理」。表单带 CSRF。停用按钮 `hx-confirm`，文案写明对方将不能登录。自己和最后一位管理员的停用按钮不渲染。

没有「删除用户」。停用清单用两张 `.panel`，不要做成进入单板的目录。转让输入的是登录名。

- [ ] **Step 4: 跑绿，全量 pytest，提交**

```bash
git commit -m "系统管理员可以创建、停用账号并移交无主的板"
```

阶段 3 可合并。

---

## 阶段 4 — 审计操作人

### Task 13: edit_log 增加操作人

**Files:**
- Modify: `app/db.py`、`app/audit.py`、`app/propagation.py`、`app/models.py`（`list_board_log`）、`app/routes/board.py`、`app/templates/log.html`、`tests/test_audit.py`、相关传播测试若断言了列集合

**Interfaces:**
- `_migrate`：`PRAGMA table_info(edit_log)` 不含 `actor_user_id` 时 `ALTER TABLE edit_log ADD COLUMN actor_user_id INTEGER REFERENCES users(id)`。
- `record_edit(..., note=None, *, actor_user_id: int | None = None)`。星号后面的参数，保证现有位置参数调用不用改。
- `apply_node_edit`、`delete_node`、`resolve_conflict` 增加关键字参数 `actor_user_id: int | None = None`，传给该函数体内每一次 `record_edit`。
- 路由从 `request.state.user["id"]` 传入。阶段 2 已经去掉根节点上那条直接 `record_edit`，这里不要加回来。

- [ ] **Step 1: 写失败测试**

`tests/test_audit.py`：写入时带 `actor_user_id`，`list_board_log` 的行上能读到用户名。不带时该字段为 None，日志页 HTML 含「（升级前）」。

传播：`resolve_conflict(..., actor_user_id=用户)` 后，`source='propagated'` 的那一行 `actor_user_id` 等于该用户。

路由：editor 改一个位号后，日志页表格里有他的登录名。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

日志表头在「来源」左侧加「操作人」。不要改 `edit_log` 的其他列语义，也不要新增 source 值。成员管理、用户管理和强制转让不要写这张表。`CLAUDE.md` 留到阶段 5。

- [ ] **Step 4: 全量 pytest 通过后提交**

```bash
git commit -m "审计日志记录操作人"
```

阶段 4 可合并。

---

## 阶段 5 — BOM 版本页

### Task 14: 影响清单、单事务修正、负责人转让

**Files:**
- Create: `app/routes/bom_version.py`、`app/templates/bom_version.html`、`tests/test_bom_version.py`
- Modify: `app/models.py`、`app/authz.py`、`app/main.py`、`app/routes/board.py`（根节点 403 文案与页面链接）、`app/templates/node_detail.html`、`CLAUDE.md`、`README.md`

**Interfaces:**
- `can_correct_bom_version(conn, user_id, version_id) -> bool` 合取：是负责人，且每一块板的角色都 `>= editor`。实现时不要把失败的板 id 返回给路由拼进文案。
- `preview_bom_version_change(...) -> list` 只算不写。每块板一项：`board_uid`、折叠差异、传播冲突。冲突检测复用现有函数。
- `apply_bom_version_change(conn, version_id, actor_user_id, expected_revision, reference, op, part, choices) -> None` 一个事务：`bom_versions.revision` 不符则抛错且不改 `initial_bom`；缺任何一条冲突的选择则抛错；成功时更新初始 BOM、按选择处理每块板的下游、每块板根节点写 `edit_log`（`note` 以「BOM 版本修正」开头，`actor_user_id` 为提交者，传播行同样带这个前缀），最后 `revision + 1`。中途失败全部回滚。
- 路由：`GET /bom-version/{version_id}`、`POST /bom-version/{version_id}/preview`、`POST /bom-version/{version_id}`、`POST /bom-version/{version_id}/transfer`。登记 `custom`。GET：负责人或任一成员。POST 修正：`can_correct_bom_version`。转让：仅现任负责人，对象是未停用用户，不改各板成员。
- 根节点页增加「初始清单在 BOM 版本页维护。」403 文案改为「根节点是初始 BOM，请到 BOM 版本页修正。」

- [ ] **Step 1: 写失败测试**

按设计文档 `tests/test_bom_version.py` 那一段：清单含两块 `board_uid`；未选冲突则 `initial_bom` 不变；全部确认后两块折叠一起变，且 note 前缀与操作人正确；第二块写入失败时第一块也不变；负责人被降为 viewer 时没有表单，403 正文不含那块 `board_uid`。再加：转让负责人后原负责人不能再 POST 修正（若他因此不再满足合取），新负责人在也是各板 editor 时可以。`revision` 用旧值提交时不写库，提示「已被 {用户名} 修改，请刷新」。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

预览与提交分成两次请求。提交请求带上预览时的 `revision` 和每条冲突的选择。不要在预览请求里写库。页面复用 `.panel`、`.modal`、冲突弹窗里已有的「保留 / 采用」文案。

`CLAUDE.md`：MVP 边界里的「多用户/并发/权限」改为指向本设计；约定里写新路由必须登记守卫表、HTMX 未登录用 200 + `HX-Redirect`、单板页不写 `initial_bom`。运行一节指向 README 的管理员初始化。README 写明版本页是改初始 BOM 的地方；若生产还停在阶段 2 到阶段 4，则网页暂时不能改初始 BOM。

- [ ] **Step 4: 全量 pytest 后提交**

```bash
git commit -m "在 BOM 版本页统一修正初始清单"
```

阶段 5 可合并。

---

## 阶段 6 — 节点乐观锁

### Task 15: 草稿与节点的版本号

**Files:**
- Modify: `app/db.py`、`app/models.py`、`app/propagation.py`、`app/routes/board.py`、相关模板（`_edit_form.html`、`node_detail.html`、`_conflict_modal.html`、`_delete_conflict_modal.html`、`insert_node.html`、`_bom_table.html`、`_changes_panel.html`、`_attachments.html`）
- Create: `tests/test_revision.py`
- Modify: `README.md`（删掉「单板编辑仍会后写覆盖」那句）、`CLAUDE.md` 补一句乐观锁文案

**Interfaces:**
- `nodes.revision INTEGER NOT NULL DEFAULT 0`，`nodes.revised_by INTEGER REFERENCES users(id)`。`_migrate` 用 `PRAGMA table_info` 判断后再 `ALTER`。
- `bump_revision(conn, node_id, expected, user_id) -> bool`。SQL 为 `UPDATE nodes SET revision = revision + 1, revised_by = ? WHERE id = ? AND revision = ?`。返回是否更新到一行。
- 写路径增加关键字参数 `commit: bool = True`。乐观锁的那一次请求自己 `BEGIN`，内部传 `commit=False`，全部 bump 成功才 `commit`，否则 `rollback`。
- 有下游冲突的编辑：第一个请求不写库，响应是确认弹窗，隐藏域带被编辑节点和各下游节点当时的 revision。确认请求才写入。取消不写。
- 冲突响应：200、`HX-Reswap: none`，`#form-error` 与 toast 为「已被 {用户名} 修改，请刷新」。`revised_by` 为空时用「其他人」。

- [ ] **Step 1: 写失败测试**

`tests/test_revision.py`：两个已登录用户先后编辑同一草稿。后一个带着先打开页面时的 `revision`，changeset 仍是前一个写入的值，响应含前一个用户的登录名。冲突确认里下游 revision 过期时，被编辑节点的 changeset 也不变。插入保存带上过期的父节点 revision 时不产生新节点。

- [ ] **Step 2: 跑红**

- [ ] **Step 3: 实现**

隐藏域和 `hx-vals` 按设计文档。插入页把父节点 revision 放进页面的 Alpine 状态，check 与保存都提交它；失败写进 `editError`。

不要在这个阶段改 BOM 版本页已经用着的 `bom_versions.revision`。

- [ ] **Step 4: 全量 pytest 后提交**

```bash
git commit -m "保存节点时用版本号拒绝后一次覆盖"
```

阶段 6 可合并。到这里定稿方案全部落地。

---

## 自检

每个阶段合并前：

- [ ] `pytest` 全绿（含 Playwright；浏览器缺失要先装，不要把 UI 测试标成跳过）
- [ ] `rg 'mount\\("/uploads"' app` 无结果（阶段 2 的 Task 10 之后）
- [ ] `rg '"/uploads/' app/templates` 无结果（同上）
- [ ] `rg "REFLOW_SECRET_KEY" app` 无结果
- [ ] 守卫表测试在，且新路由没有用「先合并再补登记」的方式送进去
- [ ] 登录页、成员页、用户管理页、只读节点页、版本页、影响清单在白天和夜间都看过；375px 宽下登录表单不横滚
- [ ] README 的初始化步骤能在一个空目录的 sqlite 上走通：`REFLOW_DB=... python -m app.cli create-admin --username admin`，然后用该账号登录，看见迁移前就存在的板，并且那些板所在的 BOM 版本负责人是他
- [ ] 阶段 2 到阶段 4 的 README 写明网页还不能改初始 BOM。阶段 5 起改成指向版本页。阶段 6 起删掉「单板编辑会后写覆盖」

## 不在本计划里的事

设计文档「非目标」里的项。定稿之后不要在实现里另选一套角色、文案或事务边界。
