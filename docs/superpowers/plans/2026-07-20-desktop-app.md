# Reflow 桌面单机版（Windows）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让同事在 Windows 上解压 zip、双击 exe 即可使用 Reflow，不装 Python、不装 Docker、断网可用。

**Architecture:** 新增两个模块 —— `app/paths.py` 解析资源目录与用户数据目录（兼容 PyInstaller 的 `sys._MEIPASS`），`app/desktop.py` 作为打包入口负责设环境变量、绑本地 socket、开浏览器、起 uvicorn。`app/main.py` 改为基于 `resource_dir()` 解析模板与静态目录。PyInstaller onedir 打包，GitHub Actions 的 windows-latest runner 在打 tag 时构建并挂到 Release。

**Tech Stack:** Python 3.12、FastAPI、uvicorn、PyInstaller（onedir）、GitHub Actions。

设计文档：`docs/superpowers/specs/2026-07-20-desktop-app-design.md`

## Global Constraints

- Python `>=3.11`，CI 用 `3.12`（对齐生产镜像 `python:3.12-slim`）。
- 代码注释、docstring、UI 文案、错误消息**一律中文**。
- 遵循 TDD：先写失败测试，再写实现。纯逻辑模块是测试重点。
- 现有测试基线必须全部保持通过（`pytest -q`）。
- **不改动 Docker 分发路径**：`Dockerfile`、`deploy.sh`、`deploy.white-studio.sh`、
  `.github/workflows/publish-image.yml` 一行不动。
- 桌面版监听地址固定 `127.0.0.1`（不是 `0.0.0.0`）。
- 用户数据目录：Windows 为 `%LOCALAPPDATA%\Reflow`，其他平台为 `~/.local/share/reflow`。
- PyInstaller 打包为 **onedir**（非 onefile），产物名 `Reflow`。

---

### Task 1: `app/paths.py` —— 资源目录与用户数据目录解析

纯逻辑模块，无 Web / DB 依赖，是本次的测试重点。

**Files:**
- Create: `app/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: 无（本任务是起点）
- Produces:
  - `resource_dir() -> pathlib.Path` —— 模板/静态文件所在根目录
  - `user_data_dir() -> pathlib.Path` —— 用户数据目录，调用时确保已创建

- [ ] **Step 1: 写失败测试**

创建 `tests/test_paths.py`：

```python
"""资源目录与用户数据目录解析（含 PyInstaller frozen 模式）。"""
import sys
from pathlib import Path

from app.paths import resource_dir, user_data_dir


def test_resource_dir_dev_mode_returns_app_package_dir():
    """开发模式下返回 app/ 目录本身，其下应有 templates 与 static。"""
    d = resource_dir()
    assert d.name == "app"
    assert (d / "templates").is_dir()
    assert (d / "static").is_dir()


def test_resource_dir_frozen_returns_meipass(monkeypatch, tmp_path):
    """打包模式下返回 sys._MEIPASS。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_dir() == Path(tmp_path)


def test_user_data_dir_windows(monkeypatch, tmp_path):
    """Windows 下取 %LOCALAPPDATA%\\Reflow。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert user_data_dir() == Path(tmp_path) / "Reflow"


def test_user_data_dir_posix(monkeypatch, tmp_path):
    """非 Windows 下取 ~/.local/share/reflow。"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert user_data_dir() == tmp_path / ".local" / "share" / "reflow"


def test_user_data_dir_creates_directory(monkeypatch, tmp_path):
    """目录不存在时自动创建。"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    d = user_data_dir()
    assert d.is_dir()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_paths.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.paths'`

- [ ] **Step 3: 写实现**

创建 `app/paths.py`：

```python
"""资源目录与用户数据目录解析（兼容 PyInstaller 打包）。

打包后代码运行在 PyInstaller 解包出的临时目录里，当前工作目录不再是仓库根目录，
因此模板/静态文件不能再用相对路径定位；数据库与上传文件也不能写在程序目录
（onefile 模式下退出即被清理），必须落到用户数据目录。
"""
import os
import sys
from pathlib import Path


def resource_dir() -> Path:
    """模板与静态文件的根目录。

    PyInstaller 打包后返回解包临时目录 `sys._MEIPASS`；开发模式返回 app/ 目录本身。
    两种情况下其直接子目录都应为 templates/ 与 static/。
    """
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def user_data_dir() -> Path:
    """用户数据目录（数据库与上传文件），不存在则创建。

    重装/升级程序不会影响此目录，同事的数据不会因为覆盖解压而丢失。
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        d = Path(base) / "Reflow"
    else:
        d = Path.home() / ".local" / "share" / "reflow"
    d.mkdir(parents=True, exist_ok=True)
    return d
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_paths.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add app/paths.py tests/test_paths.py
git commit -m "feat：新增 paths 模块解析资源目录与用户数据目录"
```

---

### Task 2: `app/main.py` 改用 `resource_dir()` 定位模板与静态目录

`main.py:12` 与 `main.py:30` 目前写死相对路径 `app/templates` / `app/static`，
相对于**当前工作目录**解析。打包后 cwd 不是仓库根目录，会直接找不到模板。

**Files:**
- Modify: `app/main.py:12`（`templates = ...`）、`app/main.py:30`（`app.mount("/static", ...)`）
- Test: `tests/test_paths.py`（追加）

**Interfaces:**
- Consumes: `app.paths.resource_dir() -> Path`（Task 1）
- Produces: 无新接口；`app.main.templates` 与 `/static` 挂载改为绝对路径

- [ ] **Step 1: 写失败测试**

在 `tests/test_paths.py` 末尾追加：

```python
def test_main_uses_absolute_template_dir():
    """模板目录必须是绝对路径，否则打包后随 cwd 变化而失效。"""
    from app.main import templates

    loader_dirs = templates.env.loader.searchpath
    assert all(Path(p).is_absolute() for p in loader_dirs), loader_dirs
    assert any((Path(p) / "base.html").is_file() for p in loader_dirs), loader_dirs
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_paths.py::test_main_uses_absolute_template_dir -v`
Expected: FAIL —— searchpath 为相对路径 `app/templates`，`is_absolute()` 断言不通过

- [ ] **Step 3: 写实现**

在 `app/main.py` 的 import 区加入（放在 `from app.db import connect, init_db` 之后）：

```python
from app.paths import resource_dir
```

把 `app/main.py:12` 一行：

```python
templates = Jinja2Templates(directory="app/templates")
```

改为：

```python
_RES = resource_dir()
templates = Jinja2Templates(directory=str(_RES / "templates"))
```

把 `app/main.py:30` 一行：

```python
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
```

改为：

```python
    app.mount("/static", StaticFiles(directory=str(_RES / "static")), name="static")
```

`REFLOW_DB` / `REFLOW_UPLOAD_DIR` 的读取逻辑（`main.py:23`、`main.py:31`）**保持不变** ——
默认值仍是相对路径，桌面模式下由启动器设置环境变量，开发与测试行为不受影响。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_paths.py -v`
Expected: 6 passed

- [ ] **Step 5: 跑全量测试确认无回归**

Run: `pytest -q`
Expected: 全部通过（基线数量不减少）。若有 UI 测试因端口竞态偶发失败，单独重跑该文件确认。

- [ ] **Step 6: 提交**

```bash
git add app/main.py tests/test_paths.py
git commit -m "fix：模板与静态目录改用绝对路径，为打包做准备"
```

---

### Task 3: `app/desktop.py` —— 桌面版启动器

**Files:**
- Create: `app/desktop.py`
- Test: `tests/test_desktop.py`

**Interfaces:**
- Consumes: `app.paths.user_data_dir() -> Path`（Task 1）
- Produces:
  - `prepare_env() -> None` —— 设置 `REFLOW_DB` / `REFLOW_UPLOAD_DIR`（已设置则不覆盖）
  - `bind_socket() -> tuple[socket.socket, int]` —— 返回已 listen 的 socket 与实际端口
  - `main() -> None` —— 打包入口

**关键顺序陷阱（务必遵守）**：`app/main.py` 末尾是 `app = create_app()`，
**import 该模块就会执行 `create_app()`**，其中会读 `REFLOW_UPLOAD_DIR` 并 `os.makedirs`。
因此 `prepare_env()` 必须在 `from app.main import app` **之前**调用 ——
所以该 import 写在 `main()` 函数体内部，不能放在模块顶部。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_desktop.py`：

```python
"""桌面版启动器：环境变量准备与本地 socket 绑定。"""
import socket
import sys
from pathlib import Path

from app.desktop import bind_socket, prepare_env


def test_prepare_env_sets_db_and_upload_under_user_data(monkeypatch, tmp_path):
    """未设置时，DB 与上传目录都指向用户数据目录。"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("REFLOW_DB", raising=False)
    monkeypatch.delenv("REFLOW_UPLOAD_DIR", raising=False)

    prepare_env()

    import os
    data = tmp_path / ".local" / "share" / "reflow"
    assert os.environ["REFLOW_DB"] == str(data / "reflow.sqlite")
    assert os.environ["REFLOW_UPLOAD_DIR"] == str(data / "uploads")


def test_prepare_env_does_not_override_existing(monkeypatch, tmp_path):
    """已设置的环境变量不被覆盖，保留调试与多份数据的能力。"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("REFLOW_DB", "/custom/my.sqlite")
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", "/custom/up")

    prepare_env()

    import os
    assert os.environ["REFLOW_DB"] == "/custom/my.sqlite"
    assert os.environ["REFLOW_UPLOAD_DIR"] == "/custom/up"


def test_bind_socket_binds_loopback_only(monkeypatch):
    """必须绑 127.0.0.1，不能暴露到局域网。"""
    monkeypatch.delenv("REFLOW_PORT", raising=False)
    sock, port = bind_socket()
    try:
        assert sock.getsockname()[0] == "127.0.0.1"
        assert port > 0
    finally:
        sock.close()


def test_bind_socket_is_listening_before_return(monkeypatch):
    """返回前必须已 listen —— 否则浏览器首个请求可能 connection refused。"""
    monkeypatch.delenv("REFLOW_PORT", raising=False)
    sock, port = bind_socket()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.close()
    finally:
        sock.close()


def test_bind_socket_honours_reflow_port(monkeypatch):
    """REFLOW_PORT 指定时使用该端口（供 CI 冒烟测试固定端口）。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    monkeypatch.setenv("REFLOW_PORT", str(free_port))
    sock, port = bind_socket()
    try:
        assert port == free_port
    finally:
        sock.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_desktop.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.desktop'`

- [ ] **Step 3: 写实现**

创建 `app/desktop.py`：

```python
"""桌面单机版入口：起本地 uvicorn 并打开系统默认浏览器。

与容器部署的区别：
- 监听 127.0.0.1 而非 0.0.0.0 —— 桌面应用不应把服务暴露到局域网。
- 数据库与上传文件落在用户数据目录，重装程序不丢数据。
- 端口默认由系统分配，避免固定端口被占用导致启动失败。
"""
import os
import socket
import webbrowser

from app.paths import user_data_dir


def prepare_env() -> None:
    """把数据库与上传目录指向用户数据目录（已设置则不覆盖）。

    必须在 import app.main 之前调用 —— 该模块顶层会执行 create_app()，
    其中读取 REFLOW_UPLOAD_DIR 并创建目录。
    """
    data = user_data_dir()
    os.environ.setdefault("REFLOW_DB", str(data / "reflow.sqlite"))
    os.environ.setdefault("REFLOW_UPLOAD_DIR", str(data / "uploads"))


def bind_socket() -> tuple[socket.socket, int]:
    """绑定 127.0.0.1 并 listen，返回 (socket, 实际端口)。

    端口取 REFLOW_PORT；未设置时用 0 由系统分配空闲端口。
    返回前完成 listen：浏览器发出首个请求时 uvicorn 的 accept 循环可能尚未启动，
    但只要 socket 已 listen，内核就会把连接排队，不会 connection refused。
    """
    port = int(os.environ.get("REFLOW_PORT", "0"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    actual_port = sock.getsockname()[1]
    sock.listen(128)
    return sock, actual_port


def main() -> None:
    """打包入口：准备环境 → 绑端口 → 开浏览器 → 起服务（阻塞）。"""
    prepare_env()

    # 必须在 prepare_env() 之后再 import：app.main 顶层会执行 create_app()
    import uvicorn
    from app.main import app

    sock, port = bind_socket()
    url = f"http://127.0.0.1:{port}/"

    print(f"Reflow 已启动：{url}")
    print("用完直接关掉这个窗口即可退出。")

    if not os.environ.get("REFLOW_NO_BROWSER"):
        webbrowser.open(url)

    # 不能用 uvicorn.run()：它内部自建 socket，无法接受已绑定的 socket，
    # 也就拿不到启动前的实际端口号。
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_desktop.py -v`
Expected: 5 passed

- [ ] **Step 5: 手工验证真能跑起来**

Run:
```bash
REFLOW_NO_BROWSER=1 REFLOW_PORT=8123 REFLOW_DB=/tmp/desktop-check.sqlite \
  REFLOW_UPLOAD_DIR=/tmp/desktop-check-uploads python -m app.desktop &
sleep 3 && curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8123/
kill %1
```
Expected: 打印 `200`

- [ ] **Step 6: 提交**

```bash
git add app/desktop.py tests/test_desktop.py
git commit -m "feat：新增桌面版启动器，绑本地端口并自动开浏览器"
```

---

### Task 4: PyInstaller 打包配置

**Files:**
- Create: `reflow.spec`
- Modify: `pyproject.toml`（dev 可选依赖加 `pyinstaller`）
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `app/desktop.py` 的 `main()`（Task 3）、`app.paths.resource_dir()`（Task 1）
- Produces: `dist/Reflow/Reflow`（Linux）/ `dist/Reflow/Reflow.exe`（Windows）

**静默陷阱**：`datas` 的**目标路径必须剥掉 `app/` 前缀**，落成 `_MEIPASS/templates`
与 `_MEIPASS/static`。因为 `resource_dir()` 在 frozen 时返回 `_MEIPASS` 本身，
若打成 `_MEIPASS/app/templates` 则路径对不上 —— 开发模式一切正常，打包产物每个模板 404。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_packaging.py`：

```python
"""打包配置契约：datas 目标路径必须与 resource_dir() 的 frozen 根对齐。

resource_dir() 在 frozen 时返回 sys._MEIPASS 本身，其直接子目录须为
templates/ 与 static/。若 .spec 把它们打到 _MEIPASS/app/ 下，开发模式无异常，
但打包产物每个模板都 404。本测试守护这条契约。
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spec_text() -> str:
    with open(os.path.join(REPO_ROOT, "reflow.spec"), encoding="utf-8") as f:
        return f.read()


def _datas_pairs() -> list[tuple[str, str]]:
    """解析 .spec 里 datas 列表中的 (源, 目标) 二元组。"""
    text = _spec_text()
    m = re.search(r"datas\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "reflow.spec 中未找到 datas 列表"
    return re.findall(r"\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", m.group(1))


def test_templates_and_static_target_meipass_root():
    """模板与静态文件必须落在打包根目录下，目标路径不含 app/ 前缀。"""
    pairs = dict(_datas_pairs())
    assert pairs.get("app/templates") == "templates", pairs
    assert pairs.get("app/static") == "static", pairs


def test_entry_point_is_desktop_module():
    """打包入口必须是桌面启动器，不是 uvicorn 命令行。"""
    assert "app/desktop.py" in _spec_text()


def test_onedir_mode():
    """必须是 onedir：未签名的单文件 exe 杀软误报率明显更高。"""
    text = _spec_text()
    assert "COLLECT(" in text, "缺少 COLLECT，说明不是 onedir 模式"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_packaging.py -v`
Expected: FAIL —— `FileNotFoundError: reflow.spec`

- [ ] **Step 3: 写实现**

创建 `reflow.spec`：

```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

datas 的目标路径必须剥掉 app/ 前缀，落成 <bundle>/templates 与 <bundle>/static，
与 app.paths.resource_dir() 在 frozen 模式下返回 sys._MEIPASS 的约定对齐。
"""

a = Analysis(
    ['app/desktop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app/templates', 'templates'),
        ('app/static', 'static'),
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Reflow',
    debug=False,
    strip=False,
    upx=False,
    console=True,          # 保留控制台窗口：既是退出方式，也是出错时唯一线索
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='Reflow',
)
```

在 `pyproject.toml` 把 dev 依赖那行：

```toml
dev = ["pytest>=8.0", "httpx>=0.27", "pytest-playwright>=0.5"]
```

改为：

```toml
dev = ["pytest>=8.0", "httpx>=0.27", "pytest-playwright>=0.5", "pyinstaller>=6.0"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_packaging.py -v`
Expected: 3 passed

- [ ] **Step 5: 本地实际构建并冒烟验证**

在 Linux 上构建同样能验证 spec 语法、datas 路径与 hiddenimports 是否齐全
（Windows 产物由 Task 5 的 CI 验证）。

Run:
```bash
pip install -e ".[dev]"
pyinstaller --noconfirm --clean reflow.spec
REFLOW_NO_BROWSER=1 REFLOW_PORT=8124 REFLOW_DB=/tmp/pkg-check.sqlite \
  REFLOW_UPLOAD_DIR=/tmp/pkg-check-uploads ./dist/Reflow/Reflow &
sleep 5 && curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8124/
kill %1
```
Expected: 打印 `200`。若报模板 404 或 `TemplateNotFound`，检查 datas 目标路径；
若报 `ModuleNotFoundError`，把缺的模块补进 `hiddenimports`。

- [ ] **Step 6: 忽略构建产物**

把 `build/`、`dist/` 加入 `.gitignore`（若尚未包含）：

```bash
grep -qxF 'build/' .gitignore || echo 'build/' >> .gitignore
grep -qxF 'dist/' .gitignore || echo 'dist/' >> .gitignore
```

- [ ] **Step 7: 提交**

```bash
git add reflow.spec pyproject.toml tests/test_packaging.py .gitignore
git commit -m "feat：新增 PyInstaller onedir 打包配置"
```

---

### Task 5: CI 构建 Windows 产物并挂到 Release

**Files:**
- Create: `.github/workflows/publish-desktop.yml`

**Interfaces:**
- Consumes: `reflow.spec`（Task 4）、`app/desktop.py` 的 `REFLOW_PORT` / `REFLOW_NO_BROWSER` 支持（Task 3）
- Produces: Release 附件 `Reflow-<tag>-windows.zip`

沿用 `publish-image.yml` 的 tag 触发与 `verify-tag-on-master` 校验模式，
并复用 `_checks.yml` 跑既有测试。

- [ ] **Step 1: 写 workflow**

创建 `.github/workflows/publish-desktop.yml`：

```yaml
name: publish-desktop

on:
  push:
    tags: ["v*.*.*"]

concurrency:
  group: publish-desktop
  cancel-in-progress: false

jobs:
  verify-tag-on-master:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0
      - name: 校验 tag 是否已合入 master
        run: |
          git fetch origin master
          if ! git merge-base --is-ancestor "${{ github.sha }}" origin/master; then
            echo "::error::tag ${GITHUB_REF#refs/tags/} 指向的提交（${{ github.sha }}）不在 master 分支历史中，请先合并到 master 再打 tag"
            exit 1
          fi

  checks:
    needs: verify-tag-on-master
    uses: ./.github/workflows/_checks.yml

  build-windows:
    needs: [verify-tag-on-master, checks]
    runs-on: windows-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: 安装依赖
        run: pip install -e ".[dev]"

      - name: 构建桌面版
        run: pyinstaller --noconfirm --clean reflow.spec

      - name: 冒烟测试（打包产物真能起来）
        shell: pwsh
        run: |
          $env:REFLOW_NO_BROWSER = "1"
          $env:REFLOW_PORT = "8125"
          $env:REFLOW_DB = "$env:TEMP\smoke.sqlite"
          $env:REFLOW_UPLOAD_DIR = "$env:TEMP\smoke-uploads"
          $p = Start-Process -FilePath ".\dist\Reflow\Reflow.exe" -PassThru
          try {
            $ok = $false
            foreach ($i in 1..30) {
              try {
                Invoke-WebRequest -Uri "http://127.0.0.1:8125/" -UseBasicParsing -TimeoutSec 2 | Out-Null
                $ok = $true; break
              } catch { Start-Sleep -Seconds 1 }
            }
            if (-not $ok) { throw "打包产物 30 秒内未就绪" }
            Write-Host "首页 200，打包冒烟通过"
          } finally {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
          }

      - name: 打包成 zip
        shell: pwsh
        run: |
          $tag = "${{ github.ref_name }}"
          Compress-Archive -Path ".\dist\Reflow\*" -DestinationPath ".\Reflow-$tag-windows.zip"

      - name: 挂到 Release
        uses: softprops/action-gh-release@v2
        with:
          files: Reflow-${{ github.ref_name }}-windows.zip
```

- [ ] **Step 2: 校验 YAML 语法**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/publish-desktop.yml')); print('YAML OK')"`
Expected: 打印 `YAML OK`

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/publish-desktop.yml
git commit -m "ci：打 tag 时构建 Windows 桌面版并挂到 Release"
```

---

### Task 6: README 补充桌面版分发与使用说明

给同事看的说明必须是零术语的 —— 他们不需要知道 Python、Docker 或端口是什么。

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 5 产出的 Release 附件命名 `Reflow-<tag>-windows.zip`
- Produces: 无代码接口

- [ ] **Step 1: 写实现**

在 `README.md` 的「## 运行」小节**之前**插入新小节：

```markdown
## 桌面版（Windows，给不熟悉容器的同事）

同事无需安装 Python 或 Docker，也不需要联网：

1. 到本仓库 [Releases](https://github.com/aixia715/reflow/releases) 页面下载
   `Reflow-vX.Y.Z-windows.zip`。
2. 解压到任意目录（**整个文件夹一起解压**，不要只取出 `Reflow.exe`）。
3. 双击文件夹里的 `Reflow.exe`，浏览器会自动打开 Reflow。
4. 用完直接关掉那个黑色命令行窗口即退出。

数据存放在 `%LOCALAPPDATA%\Reflow`（数据库 `reflow.sqlite` + 上传图片 `uploads/`），
**不在程序目录里** —— 升级时覆盖解压新版本不会丢数据。

几点须知：

- 每台电脑的数据完全独立，同事之间互相看不到对方的单板，
  节点链接（指向 `127.0.0.1`）发给别人也打不开。需要共享起始 BOM 时手动传 CSV 让对方导入。
- 服务只监听 `127.0.0.1`，不会暴露到局域网。
- 程序未做代码签名，个别企业环境的杀毒软件可能拦截，需要手动放行。

桌面版由 `publish-desktop.yml` 在推 `v*.*.*` tag 时用 windows-latest runner 自动构建
（与 Docker 镜像发版同一个 tag 触发）。本地构建：`pyinstaller --noconfirm --clean reflow.spec`。
```

- [ ] **Step 2: 确认链接与命名与 CI 产物一致**

Run: `grep -n 'Reflow-' README.md .github/workflows/publish-desktop.yml`
Expected: README 里的 `Reflow-vX.Y.Z-windows.zip` 与 workflow 里的
`Reflow-${{ github.ref_name }}-windows.zip` 命名格式一致

- [ ] **Step 3: 跑全量测试确认无回归**

Run: `pytest -q`
Expected: 全部通过

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs：README 补充 Windows 桌面版下载与使用说明"
```

---

## 完成标准

- `pytest -q` 全绿，新增 `tests/test_paths.py`、`tests/test_desktop.py`、`tests/test_packaging.py` 全通过。
- 本地 `pyinstaller --noconfirm --clean reflow.spec` 构建出的产物能起服务并返回首页 200。
- Docker 分发路径未受影响：`Dockerfile`、`deploy*.sh`、`publish-image.yml` 无改动。
- 推 tag 后 GitHub Release 上出现 `Reflow-<tag>-windows.zip`。
