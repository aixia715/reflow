"""桌面版启动器：环境变量准备与本地 socket 绑定。"""
import ast
import os
import socket
import sys
from pathlib import Path

from app.desktop import bind_socket, prepare_env


def test_prepare_env_sets_db_and_upload_under_user_data(monkeypatch, tmp_path):
    """未设置时，DB 与上传目录都指向用户数据目录。"""
    # 整体隔离 os.environ：对一个本就未设置的变量，delenv 不会登记回滚项，
    # prepare_env() 写入的值会在 teardown 后泄漏到后续测试；改用 setattr
    # 替换整个字典，使测试内的任何改动都能随 teardown 一并还原。
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("REFLOW_DB", raising=False)
    monkeypatch.delenv("REFLOW_UPLOAD_DIR", raising=False)

    prepare_env()

    data = tmp_path / ".local" / "share" / "reflow"
    assert os.environ["REFLOW_DB"] == str(data / "reflow.sqlite")
    assert os.environ["REFLOW_UPLOAD_DIR"] == str(data / "uploads")


def test_prepare_env_does_not_override_existing(monkeypatch, tmp_path):
    """已设置的环境变量不被覆盖，保留调试与多份数据的能力。"""
    # 同上：整体隔离 os.environ，避免本测试写入的值泄漏到后续测试。
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setenv("REFLOW_DB", "/custom/my.sqlite")
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", "/custom/up")

    prepare_env()

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


def test_desktop_module_does_not_import_app_main_at_top_level():
    """守护关键契约：app.main 不得在 app/desktop.py 模块顶层被 import。

    一旦顶层 import app.main，该模块顶层的 create_app() 会在 prepare_env()
    设置 REFLOW_UPLOAD_DIR 之前执行，导致 uploads 目录建到错误位置。
    正确写法是把 `from app.main import app` 放在 main() 函数体内、
    prepare_env() 调用之后。

    用 AST 解析源码而非 sys.modules 判断——其它测试模块在 collection 阶段
    就会 import app.main，用 sys.modules 检测会对本测试造成误报。
    """
    source = Path("app/desktop.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="app/desktop.py")

    for node in tree.body:  # 只看模块级语句，不递归进函数体
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            assert "app.main" not in names, "app.main 不得在模块顶层 import"
        elif isinstance(node, ast.ImportFrom):
            module = f"{'.' * node.level}{node.module or ''}"
            assert module != "app.main", "app.main 不得在模块顶层 import"
