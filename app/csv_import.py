import csv
import io
from typing import NamedTuple

from app.validation import validate_edit


class CsvEntry(NamedTuple):
    reference: str
    part: str


class CsvProblem(NamedTuple):
    kind: str        # "duplicate" | "empty_part" | "empty_reference" | "bad_op" | "invalid"
    reference: str
    detail: str


def _is_cell_blank(v: str | list | tuple | None) -> bool:
    # 数据行字段数多于表头时，DictReader 把多余字段以 list 挂在 restkey（None 键）下
    if isinstance(v, (list, tuple)):
        return all(_is_cell_blank(x) for x in v)
    return not (v or "").strip()


def _is_blank_row(row: dict) -> bool:
    """整行所有单元格都是空白/空（Excel 保存 CSV 产生的空行）→ 视作空行跳过。"""
    return all(_is_cell_blank(v) for v in row.values())


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
        if _is_blank_row(row):
            continue  # Excel 空行（一行逗号、无文字）自动跳过
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


class ChangeEntry(NamedTuple):
    """CSV 里的一行修改。op 为 None 表示待按折叠 BOM 推断；part 可能是空串。"""
    reference: str
    op: str | None
    part: str


_VALID_OPS = ("add", "modify", "remove")


def change_csv_template() -> str:
    """工作区「从 CSV 导入修改」用的下载模板：仅 Reference/Part/OP 三列表头。

    与 parse_change_csv 的表头约定一致（大小写不敏感、空格容错），无数据行，
    便于用户填好位号后直接上传。可被 parse_change_csv 反向解析为「无条目」。
    """
    return "Reference,Part,OP\n"


def parse_change_csv(text: str) -> tuple[list[ChangeEntry], list[CsvProblem]]:
    """解析「修改清单 CSV」：Reference / Part 两列必需，OP 列可选。

    表头大小写不敏感（issue 原文的表头是 PART）。逗号合并位号拆成多条。
    CSV 内位号重复视为问题行（首条获胜），不做后者覆盖前者。
    Part 是否可空取决于 op，故不在此判断，留给 plan_changes。
    """
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))
    fieldmap = {(name or "").strip().lower(): name for name in (reader.fieldnames or [])}
    ref_col = fieldmap.get("reference")
    part_col = fieldmap.get("part")
    op_col = fieldmap.get("op")
    if ref_col is None or part_col is None:
        raise ValueError("CSV 必须包含 Reference 和 Part 两列")

    entries: list[ChangeEntry] = []
    problems: list[CsvProblem] = []
    seen: set[str] = set()

    for row in reader:
        if _is_blank_row(row):
            continue  # Excel 空行（一行逗号、无文字）自动跳过
        raw_refs = row.get(ref_col) or ""
        part = (row.get(part_col) or "").strip()
        raw_op = (row.get(op_col) or "").strip() if op_col else ""
        op = raw_op.lower()
        for ref in raw_refs.split(","):
            ref = ref.strip()
            if ref == "":
                if raw_refs.strip() != "":
                    continue  # 合并格内的空段（如尾随逗号）忽略
                problems.append(CsvProblem("empty_reference", "", "位号为空"))
                continue
            if ref in seen:
                problems.append(CsvProblem("duplicate", ref, "位号在 CSV 中重复"))
                continue
            seen.add(ref)
            if op and op not in _VALID_OPS:
                problems.append(
                    CsvProblem("bad_op", ref,
                               f"OP 值无效：{raw_op}（只能是 add / modify / remove）"))
                continue
            entries.append(ChangeEntry(ref, op or None, part))

    return entries, problems


class PlannedChange(NamedTuple):
    """一条已确定 op 且通过校验的修改。remove 时 part 为 None。"""
    reference: str
    op: str
    part: str | None


def plan_changes(
    full_bom: dict[str, str], entries: list[ChangeEntry]
) -> tuple[list[PlannedChange], list[CsvProblem]]:
    """把 CSV 条目落到某节点折叠后的 BOM 上：推断 op、逐条校验。

    op 为 None 时按位号是否已在 full_bom 中推断为 modify / add——推断永远
    得不出 remove，批量设不贴必须显式写 OP=remove。

    CSV 内位号不重复（parse_change_csv 已保证），故每条都对**静态的**
    full_bom 独立校验，不需要维护逐行模拟态。full_bom 不被修改。
    """
    changes: list[PlannedChange] = []
    problems: list[CsvProblem] = []
    for e in entries:
        op = e.op or ("modify" if e.reference in full_bom else "add")
        part = None if op == "remove" else e.part
        err = validate_edit(full_bom, e.reference, op, part)
        if err:
            problems.append(CsvProblem("invalid", e.reference, err))
            continue
        changes.append(PlannedChange(e.reference, op, part))
    return changes, problems
