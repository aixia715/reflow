def fold_bom(initial: dict[str, str], chain: list[list[dict]]) -> dict[str, str]:
    """初始 BOM + 沿链按顺序叠加每个节点的 changeset，得到完整 BOM。

    chain: 从根到目标节点（含目标），每元素是该节点 changeset 列表。
    """
    result = dict(initial)
    for changeset in chain:
        for ch in changeset:
            if ch["op"] == "remove":
                result.pop(ch["reference"], None)
            else:  # add / modify
                result[ch["reference"]] = ch["part"]
    return result


def stage_change(
    parent_bom: dict[str, str], reference: str, new_value: str | None
) -> tuple[str, str, str | None]:
    """把「该位号的新值」换算成相对父节点的暂存条目：(action, op, part)。

    插入页的改动暂存在浏览器里，保存时整批写成新节点的 changeset，而 changeset
    是**相对父节点**的——所以 op 只能按父节点算，与「相对当前屏幕」无关：本页
    暂存里 add 过的位号再改一次，落库仍是一条 add。

    action='drop' 表示这条等于没改（值回到父节点原样、或本页刚 add 的位号又设为
    不贴），调用方应把它从暂存里删掉：留着只会变成一条改前改后相同的空转记录。
    drop 时仍返回换算后的 op/part，方便调用方展示。

    new_value=None 表示不贴。parent_bom 不被修改。
    """
    parent_value = parent_bom.get(reference)
    if new_value is None:
        op = "remove"
    elif reference in parent_bom:
        op = "modify"
    else:
        op = "add"
    action = "drop" if parent_value == new_value else "set"
    return action, op, new_value


def resolve_reference(
    initial: dict[str, str], chain: list[list[dict]], reference: str
) -> str | None:
    """某位号在目标节点的解析值；None 表示不贴（不在 BOM 中）。"""
    value = initial.get(reference)
    for changeset in chain:
        for ch in changeset:
            if ch["reference"] == reference:
                value = None if ch["op"] == "remove" else ch["part"]
    return value
