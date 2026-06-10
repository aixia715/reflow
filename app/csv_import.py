import csv
import io
from typing import NamedTuple


class CsvEntry(NamedTuple):
    reference: str
    part: str


class CsvProblem(NamedTuple):
    kind: str        # "duplicate" | "empty_part" | "empty_reference"
    reference: str
    detail: str


def parse_bom_csv(text: str) -> tuple[list[CsvEntry], list[CsvProblem]]:
    """解析 CSV，只取 Reference/Part 两列，拆分逗号合并位号，并产出校验问题清单。

    健壮性：UTF-8 BOM 头、CRLF、带引号含逗号字段、位号首尾空格。
    """
    # 去掉 UTF-8 BOM 头（U+FEFF）；csv 模块按 \n/\r\n 都能正确分行
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))
    # 容忍列名首尾空格
    fieldmap = {(name or "").strip(): name for name in (reader.fieldnames or [])}
    ref_col = fieldmap.get("Reference")
    part_col = fieldmap.get("Part")
    if ref_col is None or part_col is None:
        raise ValueError("CSV 必须包含 Reference 和 Part 两列")

    entries: list[CsvEntry] = []
    problems: list[CsvProblem] = []
    seen: dict[str, str] = {}

    for row in reader:
        raw_refs = (row.get(ref_col) or "")
        part = (row.get(part_col) or "").strip()
        for ref in raw_refs.split(","):
            ref = ref.strip()
            if ref == "":
                if raw_refs.strip() != "":
                    continue  # 合并格内的空段（如尾随逗号）忽略
                problems.append(CsvProblem("empty_reference", "", "位号为空"))
                continue
            if ref in seen:
                problems.append(
                    CsvProblem("duplicate", ref, f"位号重复（已有 Part={seen[ref]}）")
                )
                continue
            if part == "":
                problems.append(CsvProblem("empty_part", ref, "Part 为空"))
            seen[ref] = part
            entries.append(CsvEntry(ref, part))

    return entries, problems
