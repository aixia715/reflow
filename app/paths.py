"""资源目录解析。

模板与静态文件相对 app 包定位，避免依赖进程的当前工作目录
（本地 uvicorn 与 Docker 镜像都从这一路径取 templates/ 与 static/）。
"""
from pathlib import Path


def resource_dir() -> Path:
    """模板与静态文件的根目录（app/ 本身，其直接子目录为 templates/ 与 static/）。"""
    return Path(__file__).resolve().parent
