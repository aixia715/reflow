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
