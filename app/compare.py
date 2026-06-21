"""节点对比纯逻辑：BOM 差异、时间区间内硬更改（零 Web/DB 依赖）。"""

from datetime import datetime


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


def hard_changes_between(hcs: list[dict], lo_ts: str, hi_ts: str) -> list[dict]:
    """取 occurred_at 落在 [lo, hi]（含两端）的硬更改，按时间升序。

    lo/hi 顺序无关（内部归一）；时间用 fromisoformat 解析比较，避免字符串格式脆弱。
    """
    lo, hi = sorted([datetime.fromisoformat(lo_ts), datetime.fromisoformat(hi_ts)])
    picked = [h for h in hcs if lo <= datetime.fromisoformat(h["occurred_at"]) <= hi]
    return sorted(picked, key=lambda h: datetime.fromisoformat(h["occurred_at"]))
