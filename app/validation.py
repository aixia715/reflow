"""位号编辑校验（纯逻辑，零 Web/DB 依赖）。"""

from datetime import datetime


def validate_insert_time(prev_ts: str, next_ts: str, chosen_ts: str | None) -> str | None:
    """校验插入节点所选时间：须严格落在「上一节点之后、下一节点之前」（开区间）。

    三个参数均为 ISO 8601 时间字符串（带时区偏移）；按时刻比较，不做字符串字面量比较。
    合法返回 None，否则返回中文错误消息。
    """
    if not (chosen_ts or "").strip():
        return "时间不能为空"
    try:
        prev = datetime.fromisoformat(prev_ts)
        nxt = datetime.fromisoformat(next_ts)
        chosen = datetime.fromisoformat(chosen_ts)
    except ValueError:
        return "时间格式无效"
    if chosen <= prev:
        return "时间必须晚于上一个节点"
    if chosen >= nxt:
        return "时间必须早于下一个节点"
    return None


def validate_edit(
    full_bom: dict[str, str], reference: str, op: str, part: str | None
) -> str | None:
    """校验对折叠后 BOM 的一次位号编辑。

    full_bom: 被编辑节点折叠后的完整 BOM（根节点即初始 BOM）。
    reference: 位号；允许未裁剪，函数内部会 strip。
    op: 'add' | 'modify' | 'remove'。
    part: 新 Part 值；只判断非空、不裁剪（存储前的清理由调用方负责）；remove 时忽略。
    合法返回 None，否则返回中文错误消息。
    """
    reference = (reference or "").strip()
    if not reference:
        return "位号不能为空"
    has_part = bool((part or "").strip())
    if op == "add":
        if reference in full_bom:
            return f"位号 {reference} 已存在，请用「修改」"
        if not has_part:
            return "新增位号必须填写 Part"
    elif op == "modify":
        if reference not in full_bom:
            return f"位号 {reference} 不存在，无法修改"
        if not has_part:
            return "修改必须填写新 Part 值"
    elif op == "remove":
        if reference not in full_bom:
            return f"位号 {reference} 不存在或已是不贴状态"
    else:
        return f"未知操作类型：{op}"
    return None


def validate_new_name(new: str | None) -> str | None:
    """重命名时的新名校验：trim 后非空。返回中文错误消息或 None。

    只判断非空，不裁剪（裁剪由调用方负责，与 validate_edit 的 part 契约一致）。
    """
    if not (new or "").strip():
        return "名称不能为空"
    return None
