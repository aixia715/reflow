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
