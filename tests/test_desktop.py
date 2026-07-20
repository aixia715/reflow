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
