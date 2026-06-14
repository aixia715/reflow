"""硬更改纯逻辑：文件名、上传校验、时间线混排（零 Web/DB 依赖）。"""
import os
import uuid

ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 单图 10 MB
MAX_IMAGES = 12                       # 每条硬更改最多 12 张


def split_ext(filename: str) -> str:
    """返回小写扩展名（不含点）；无扩展名返回空串。"""
    _, ext = os.path.splitext(filename or "")
    return ext[1:].lower()


def make_stored_name(original: str) -> str:
    """生成唯一存盘名 uuid4.hex(+.ext)；扩展名取自原名。"""
    ext = split_ext(original)
    return f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex


def validate_upload(title: str, uploads: list[tuple[str, int]]) -> str | None:
    """校验标题与待上传图片 [(文件名, 字节数), ...]，返回中文错误消息或 None。"""
    if not (title or "").strip():
        return "标题不能为空"
    if len(uploads) > MAX_IMAGES:
        return f"附图最多 {MAX_IMAGES} 张，当前 {len(uploads)} 张"
    for name, size in uploads:
        if split_ext(name) not in ALLOWED_EXTS:
            return f"不支持的图片格式：{name}（仅支持 {', '.join(sorted(ALLOWED_EXTS))}）"
        if size > MAX_IMAGE_BYTES:
            return f"图片过大：{name}（单图上限 10 MB）"
    return None


def validate_content_types(content_types) -> str | None:
    """二次校验：上传文件的 Content-Type 必须是 image/*。返回中文错误或 None。"""
    for ct in content_types:
        if not (ct or "").startswith("image/"):
            return f"不支持的文件类型：{ct or '未知'}（仅接受图片）"
    return None


def merge_timeline(nodes, hard_changes) -> list[dict]:
    """合并 BOM 节点与硬更改为按时间排序的时间线项。

    - 已提交节点用 committed_at、硬更改用 occurred_at 排序，最新在上；
    - 工作区草稿（未提交节点）恒钉在最顶（它是「当前正在做的」）。
    返回 [{"kind": "node"|"hard", "ts": str, "is_draft": bool, "obj": 原对象}]。
    """
    items: list[dict] = []
    for n in nodes:
        committed = bool(n["is_committed"])
        items.append({
            "kind": "node",
            "ts": n["committed_at"] if committed else n["created_at"],
            "is_draft": not committed,
            "obj": n,
        })
    for h in hard_changes:
        items.append({"kind": "hard", "ts": h["occurred_at"],
                      "is_draft": False, "obj": h})
    items.sort(key=lambda it: (it["is_draft"], it["ts"] or ""), reverse=True)
    return items
