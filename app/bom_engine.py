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
