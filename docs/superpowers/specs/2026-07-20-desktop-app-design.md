# Reflow 桌面单机版（Windows）设计文档

日期：2026-07-20
状态：已确认

## 背景与目标

需要把 Reflow 分享给同事（普通硬件工程师，不熟悉容器部署）。现有分发方式只有 Docker
（`Dockerfile` + `deploy.*.sh` 部署到 NAS），要求对方懂容器或至少能访问局域网服务。

先后否掉的方案：

- **共享同一实例**：应用无任何鉴权代码（`app/` 下 grep `auth|login|password|session` 零命中），
  发链接等于给所有人写权限；且 CLAUDE.md 明确 MVP 边界不做多用户/并发/权限。
- **NAS 上一人一个容器**（各自独立卷 → 各自独立 DB）：可行且零代码改动，但同事仍依赖局域网可达，
  且不符合「不依赖容器」的要求。

**最终目标**：产出一个 Windows 上双击即用的单机应用。同事解压 zip、双击 exe、浏览器自动打开，
不装 Python、不装 Docker、断网可用。

**非目标**：原生窗口（Electron / Tauri / pywebview）、代码签名、自动更新、macOS / Linux 包。

## 方案（已选定）

**PyInstaller onedir 打包 + 启动器起本地 uvicorn + 打开系统默认浏览器。**

选 onedir 而非 onefile：未签名的单文件 exe 在企业/实验室环境杀软误报率明显更高，且每次启动要解压到
临时目录。onedir 解压后是一个文件夹，启动快、误报少，代价是同事看到一堆文件（README 提示别乱删）。

**前置确认（已验证，无需处理）**：CLAUDE.md 称 Alpine.js / HTMX 走 CDN —— 该描述已过时。
`base.html:10-11` 实际引用 `/static/vendor/htmx.min.js` 与 `/static/vendor/alpine.min.js`，
两个文件已在仓库中。离线运行无隐患，无需额外 vendoring。

**Docker 路径保留不动**：NAS 上的 `reflow-1` 容器、`deploy.sh` / `deploy.white-studio.sh`
/ `publish-image.yml` 一行不改。桌面版是新增的分发渠道，不是替换。

## 细节设计

### 1. `app/paths.py`（新增，纯逻辑）

两个函数，不依赖 Web / DB，是本次的测试重点：

- `resource_dir() -> Path` —— 模板与静态文件的根目录。
  PyInstaller 打包时返回 `sys._MEIPASS`；否则返回 `app/` 所在目录（`Path(__file__).parent`）。
  判定依据为 `getattr(sys, "frozen", False)`。
- `user_data_dir() -> Path` —— 用户数据目录。Windows 取 `%LOCALAPPDATA%\Reflow`，
  其他平台取 `~/.local/share/reflow`。目录不存在则创建（`mkdir(parents=True, exist_ok=True)`）。

### 2. `app/main.py` 改造

`main.py:12` 与 `main.py:30` 目前把 `app/templates`、`app/static` 写成**相对当前工作目录**的路径。
打包后 cwd 不再是仓库根目录，会直接找不到模板。改为基于 `resource_dir()` 解析：

```python
_RES = resource_dir()
templates = Jinja2Templates(directory=str(_RES / "templates"))
...
app.mount("/static", StaticFiles(directory=str(_RES / "static")), name="static")
```

`REFLOW_DB` / `REFLOW_UPLOAD_DIR` 的读取逻辑（`main.py:23`、`main.py:31`、`app/storage.py:6`）
保持不变 —— 默认值仍是相对路径，由启动器负责在桌面模式下设成用户数据目录。
开发与测试行为因此完全不受影响。

### 3. `app/desktop.py`（新增，打包入口）

启动顺序：

1. **仅在未设置时**写入 `REFLOW_DB` = `user_data_dir()/reflow.sqlite`、
   `REFLOW_UPLOAD_DIR` = `user_data_dir()/uploads`。用 `os.environ.setdefault`，
   保留环境变量覆盖能力（便于调试与多份数据）。
2. 自建 socket 绑 `127.0.0.1`，端口取 `REFLOW_PORT` 环境变量；未设置时用**端口 0**，
   由系统分配空闲端口后 `getsockname()[1]` 读回实际端口号，避免固定端口被占用导致启动失败。
   （`REFLOW_PORT` 供 CI 冒烟测试固定端口用，见第 5 节。）
3. **先 `sock.listen()`**，再 `webbrowser.open(f"http://127.0.0.1:{port}/")`。
   顺序不能反：浏览器发出第一个 GET 时 uvicorn 的 accept 循环可能尚未启动，
   但只要 socket 已 listen，内核就会把连接排队，不会 connection refused。
4. `uvicorn.Server(uvicorn.Config(app, ...)).run(sockets=[sock])` 阻塞运行。

**不能用 `uvicorn.run()`** —— 它内部自建 socket，不接受外部传入的已绑定 socket，
因而无法在启动前得知实际端口。必须走 `Server` + `Config` + `run(sockets=[...])` 这条路径。

**绑定 `127.0.0.1` 而非 `0.0.0.0`** —— `Dockerfile` 用 `0.0.0.0` 是容器场景的正确选择，
桌面应用不应把服务暴露到局域网。

保留控制台窗口（不加 `--noconsole`）：它同时是退出方式（关掉窗口即退出）和出错时的唯一线索。
README 中说明「用完关掉这个黑窗口」。

### 4. `reflow.spec`（新增，PyInstaller 配置）

- 入口 `app/desktop.py`，onedir 模式，输出名 `Reflow`。
- `datas` 带上 `app/templates` 与 `app/static`（含 `vendor/` 下的 htmx / alpine）。
  **目标路径必须剥掉 `app/` 前缀**，落成 `_MEIPASS/templates` 和 `_MEIPASS/static`，
  即 `datas=[("app/templates", "templates"), ("app/static", "static")]`。
  因为 `resource_dir()` 在 frozen 时返回 `_MEIPASS` 本身，若打成 `_MEIPASS/app/templates`
  则路径对不上。这是个静默陷阱：开发模式完全正常，打包产物每个模板都 404，
  只有第 5 节的冒烟测试能拦住。
- 按需补 `hiddenimports`（uvicorn 的 worker / lifecycle 模块常需显式声明）。

### 5. `.github/workflows/publish-desktop.yml`（新增）

复用现有 tag 触发模式（与 `publish-image.yml` 一致，含 `verify-tag-on-master` 校验）：

推 `v*.*.*` → `windows-latest` runner → `pip install -e .` + PyInstaller 构建
→ 打包成 `Reflow-vX.Y.Z-windows.zip` → 作为附件挂到 GitHub Release。

**冒烟测试（必须）**：CI 里启动构建产物中的 `Reflow.exe`，轮询 `/healthz` 直到返回 200
（超时即失败），然后结束进程。打包类问题（缺模板、缺 hiddenimports、路径错）只在真机运行时暴露，
不加这步等于没验证。因端口是动态分配的，冒烟测试通过设置 `REFLOW_PORT` 环境变量固定端口
—— 故 `desktop.py` 需支持该变量：设置了就用它，否则用端口 0。

同事从 Release 页面下载 zip，分发只需给一个链接。

## 测试策略

- `app/paths.py` 单元测试：`resource_dir()` 在 frozen / 非 frozen 两种情况下的返回值
  （用 monkeypatch 设 `sys.frozen` 与 `sys._MEIPASS`）；`user_data_dir()` 在
  Windows / 非 Windows 下的路径与自动建目录行为。
- 现有 222+ 测试须全部保持通过 —— `main.py` 改造不得影响开发模式下的路径解析。
- 打包产物由 CI 冒烟测试覆盖（见 5）。
- 遵循 TDD：先写失败测试再实现。

## 已接受的代价

- **数据在各人电脑上**：无法集中备份，无法统一升级。升级 = 重新发一次 zip，同事重新下载解压。
  因数据存在 `%LOCALAPPDATA%` 而非程序目录，重装**不会**覆盖旧数据 —— 这是第 1 节的核心理由。
- **数据孤岛**：同事之间互相看不到对方的单板，没有共享的初始 BOM 库，每份实例从空开始。
  节点 URL 指向 `127.0.0.1`，发给别人打不开。需要共享起始 BOM 时手动传 CSV 让对方导入。
- **杀软误报**：onedir 已大幅降低概率，但企业环境仍可能拦截。退路是购买代码签名证书（本次不做）。

## 后续可能（本次不做）

macOS / Linux 包、代码签名、原生窗口外壳、自动更新、应用内只读模式。
