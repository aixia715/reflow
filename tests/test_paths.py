"""资源目录解析：模板与静态文件相对 app 包定位。"""
from pathlib import Path

from app.paths import resource_dir


def test_resource_dir_returns_app_package_dir():
    """返回 app/ 目录本身，其下应有 templates 与 static。"""
    d = resource_dir()
    assert d.name == "app"
    assert (d / "templates").is_dir()
    assert (d / "static").is_dir()


def test_main_uses_absolute_template_dir():
    """模板目录必须是绝对路径，不随进程当前工作目录变化。"""
    from app.main import templates

    loader_dirs = templates.env.loader.searchpath
    assert all(Path(p).is_absolute() for p in loader_dirs), loader_dirs
    assert any((Path(p) / "base.html").is_file() for p in loader_dirs), loader_dirs
