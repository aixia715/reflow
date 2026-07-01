"""节点附件纯逻辑：存盘名生成、相对路径拼接、下载文件名清理（零 Web/DB 依赖）。"""
import os
import re
import uuid


def make_stored_name(original: str) -> str:
    """生成唯一存盘名（uuid4.hex + 原扩展名）；无扩展名则只有 hex。"""
    _, ext = os.path.splitext(original or "")
    return f"{uuid.uuid4().hex}.{ext[1:]}" if ext else uuid.uuid4().hex


def rel_path(board_id, node_id, stored_name: str) -> str:
    """附件相对上传根目录的路径：board_id/node_id/stored_name。"""
    return os.path.join(str(board_id), str(node_id), stored_name)


_SAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str) -> str:
    """下载文件名清理：去掉路径分隔符与控制字符，折叠首尾空白。
    空名兜底「附件」，避免 Content-Disposition 出现空名。"""
    cleaned = _SAFE_RE.sub("", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "附件"