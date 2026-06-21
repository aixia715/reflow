"""节点对比纯逻辑：BOM 差异、时间区间内硬更改（零 Web/DB 依赖）。"""


def diff_boms(left: dict[str, str], right: dict[str, str]) -> list[dict]:
    """对比两个折叠后的完整 BOM，返回按位号升序排列的差异行。

    kind 判定：仅右有→add；两边都有且值不同→modify；都有且相同→same；仅左有→remove。
    """
    rows = []
    for ref in sorted(set(left) | set(right)):
        lv = left.get(ref)
        rv = right.get(ref)
        if lv is None:
            kind = "add"
        elif rv is None:
            kind = "remove"
        elif lv == rv:
            kind = "same"
        else:
            kind = "modify"
        rows.append({"reference": ref, "left": lv, "right": rv, "kind": kind})
    return rows
