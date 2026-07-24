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


def test_main_uses_absolute_template_dir():
    """模板目录必须是绝对路径，否则打包后随 cwd 变化而失效。"""
    from app.main import templates

    loader_dirs = templates.env.loader.searchpath
    assert all(Path(p).is_absolute() for p in loader_dirs), loader_dirs
    assert any((Path(p) / "base.html").is_file() for p in loader_dirs), loader_dirs
