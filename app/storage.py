"""硬更改图片与节点附件的文件系统读写（上传目录由 REFLOW_UPLOAD_DIR 配置）。"""
import os


def upload_dir() -> str:
    d = os.environ.get("REFLOW_UPLOAD_DIR", "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def save_image(stored_name: str, data: bytes) -> None:
    with open(os.path.join(upload_dir(), stored_name), "wb") as f:
        f.write(data)


def delete_images(filenames) -> None:
    d = upload_dir()
    for name in filenames:
        try:
            os.remove(os.path.join(d, name))
        except FileNotFoundError:
            pass


def save_attachment(rel_path: str, data: bytes) -> None:
    """把附件写到 uploads/<rel_path>，自动建子目录。rel_path 相对上传根目录。"""
    full = os.path.join(upload_dir(), rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)


def delete_files(rel_paths) -> None:
    """按相对上传根目录的路径删除多个文件；缺文件不报错。"""
    d = upload_dir()
    for p in rel_paths:
        try:
            os.remove(os.path.join(d, p))
        except FileNotFoundError:
            pass
