"""硬更改图片的文件系统读写（上传目录由 REFLOW_UPLOAD_DIR 配置）。"""
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
