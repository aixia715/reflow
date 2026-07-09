"""位号编辑校验（纯逻辑，零 Web/DB 依赖）。"""

import json
from datetime import datetime, timezone


def validate_changes_payload(changes: str) -> tuple[list[dict], str | None]:
    """校验前端提交的 changes JSON：语法、形状、字段类型、位号唯一性。

    返回 (payload, error)；error 非 None 时 payload 为空。空数组不算错误，
    「空修改」的文案各路由不同，由调用方判断。

    op / part 允许缺省或为 None——CSV 的 OP 列留空即表示交给 op 推断。

    正常界面走 |tojson / JSON.stringify 生成，形状必然正确；本函数防的是手工
    拼接的畸形请求体：validate_edit 会对 part 调 .strip()，非字符串真值会打穿；
    RecursionError 是 RuntimeError 的子类，深嵌套 JSON 能绕过对 ValueError /
    TypeError 的捕获；位号重复则会让 set_change 的 upsert 后者覆盖前者，审计
    日志留下一条从未生效的记录。
    """
    try:
        payload = json.loads(changes)
    except (ValueError, TypeError, RecursionError):
        return [], "数据格式不正确"
    if not isinstance(payload, list) or not all(isinstance(c, dict) for c in payload):
        return [], "数据格式不正确"
    for c in payload:
        if not isinstance(c.get("reference"), str):
            return [], "数据格式不正确"
        for field in ("part", "op"):
            if not (c.get(field) is None or isinstance(c.get(field), str)):
                return [], "数据格式不正确"
    refs = [c["reference"].strip() for c in payload]
    if len(set(refs)) != len(refs):
        return [], "位号重复"
    return payload, None


def validate_insert_time(prev_ts: str, next_ts: str, chosen_ts: str | None) -> str | None:
    """校验插入节点所选时间：须严格落在「上一节点之后、下一节点之前」（开区间）。

    三个参数均为 ISO 8601 时间字符串；按时刻比较，不做字符串字面量比较。
    无时区的时间一律按 UTC 处理（避免 aware/naive 混比抛 TypeError）。
    合法返回 None，否则返回中文错误消息。
    """
    if not (chosen_ts or "").strip():
        return "时间不能为空"

    def _parse(s):
        d = datetime.fromisoformat(s)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d

    try:
        prev = _parse(prev_ts)
        nxt = _parse(next_ts)
        chosen = _parse(chosen_ts)
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
