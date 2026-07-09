"""硬更改图片与节点附件的文件系统读写（上传目录由 REFLOW_UPLOAD_DIR 配置）。"""
import os


def upload_dir() -> str:
    d = os.environ.get("REFLOW_UPLOAD_DIR", "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def save_image(stored_name: str, data: bytes) -> None:
    with open(os.path.join(upload_dir(), stored_name), "wb") as f:
        f.write(data)


def _remove_best_effort(rel_paths) -> None:
    """按相对上传根目录的路径尽力删除多个文件：单个文件删除失败（不存在、
    权限不足、磁盘故障等）不中断其余文件的删除，也不向调用方抛出——DB 侧
    才是数据来源，磁盘文件清理失败只留下残留文件，不应让调用方的请求失败。"""
    d = upload_dir()
    for p in rel_paths:
        try:
            os.remove(os.path.join(d, p))
        except OSError:
            pass


def delete_images(filenames) -> None:
    _remove_best_effort(filenames)


def save_attachment(rel_path: str, data: bytes) -> None:
    """把附件写到 uploads/<rel_path>，自动建子目录。rel_path 相对上传根目录。"""
    full = os.path.join(upload_dir(), rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)


def delete_files(rel_paths) -> None:
    """按相对上传根目录的路径尽力删除多个文件；见 _remove_best_effort。"""
    _remove_best_effort(rel_paths)
