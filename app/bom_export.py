import csv
import io
import re


def natural_sort_key(reference: str) -> list:
    """位号自然排序键：把连续数字段当整数比较，使 R2 < R10 < R100。"""
    return [
        int(tok) if tok.isdigit() else tok.lower()
        for tok in re.split(r"(\d+)", reference)
    ]


def bom_to_csv(bom: dict[str, str]) -> str:
    """把折叠后的 BOM（{位号: 值}）渲染为 CSV 文本。

    - 表头固定 `Reference,Part`（与 csv_import 对称，可反向导入）
    - 一位号一行，相同 Part 不合并
    - 行按位号自然排序
    - 不贴位号天然不在 dict 中，故不会出现
    - 含逗号/引号/换行的字段由 csv 模块正确转义
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Reference", "Part"])
    for ref in sorted(bom, key=natural_sort_key):
        writer.writerow([ref, bom[ref]])
    return buf.getvalue()


def changes_to_csv(changes: list[dict]) -> str:
    """把一个节点的 changeset（`models.get_changeset` 的形状）渲染为 CSV 文本。

    - 表头固定 `Reference,Part,OP`，与 `csv_import.change_csv_template()` 一致，
      故导出的修改项可被「从 CSV 导入修改」原样读回（在别处重放同一批修改）
    - OP 取 add/modify/remove，与 `csv_import._VALID_OPS` 同源
    - remove 行 Part 一律留空：该 op 的 part 本就不落库，历史数据里若有残留也不导出
    - 行按位号自然排序（changeset 的存储顺序是写入顺序，对读者无意义）
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Reference", "Part", "OP"])
    for c in sorted(changes, key=lambda c: natural_sort_key(c["reference"])):
        part = "" if c["op"] == "remove" else (c["part"] or "")
        writer.writerow([c["reference"], part, c["op"]])
    return buf.getvalue()


def diff_to_csv(rows: list[dict], left_label: str, right_label: str) -> str:
    """把对比页的差异行（`compare.diff_boms` 结果，已过滤掉 kind=="same"）渲染为 CSV 文本。

    - 表头 `Reference,{left_label},{right_label},Change`：左右两列头直接用调用方
      算好的、人可读的节点描述（如 `#12 xxx`），比固定写 Left/Right 更有用
    - Change 取 add/modify/remove，与 changes_to_csv 的 OP 同源
    - 左/右值为 None（未populated/不贴）时该格留空——CSV 是数据不是页面，不写「不贴」这类展示文案
    - 行按位号自然排序（diff_boms 内部是字典序，不是自然序）
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Reference", left_label, right_label, "Change"])
    for r in sorted(rows, key=lambda r: natural_sort_key(r["reference"])):
        writer.writerow([r["reference"], r["left"] or "", r["right"] or "", r["kind"]])
    return buf.getvalue()
