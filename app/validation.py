"""位号编辑校验（纯逻辑，零 Web/DB 依赖）。"""


def validate_edit(
    full_bom: dict[str, str], reference: str, op: str, part: str | None
) -> str | None:
    """校验对折叠后 BOM 的一次位号编辑。

    full_bom: 被编辑节点折叠后的完整 BOM（根节点即初始 BOM）。
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
