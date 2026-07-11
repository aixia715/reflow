"""issue #108：工作区从 CSV 导入修改项 —— 纯逻辑层。"""
import pytest

from app.csv_import import (
    ChangeEntry, PlannedChange, change_csv_template, parse_change_csv, plan_changes,
)


def test_headers_are_case_insensitive():
    """issue 原文的表头就是大写 PART，必须能认。"""
    csv = "reference,PART\nR1,10k\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k")]


def test_header_whitespace_tolerated():
    csv = " Reference , Part \nR1,10k\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k")]


def test_missing_required_column_raises():
    with pytest.raises(ValueError):
        parse_change_csv("Reference,Value\nR1,10k\n")


def test_strips_utf8_bom_and_handles_crlf():
    csv = "﻿Reference,Part\r\nR1,10k\r\nC1,100nF\r\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k"),
                       ChangeEntry("C1", None, "100nF")]


def test_op_column_read_case_insensitively():
    csv = "Reference,Part,OP\nR1,10k,ADD\nC1,,Remove\nR2,22k,modify\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", "add", "10k"),
                       ChangeEntry("C1", "remove", ""),
                       ChangeEntry("R2", "modify", "22k")]


def test_blank_op_cell_falls_back_to_inference():
    """有 OP 列但某行留空 → 该行 op=None，交给 plan_changes 推断。"""
    csv = "Reference,Part,Op\nR1,10k,\nC1,,remove\n"
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries[0] == ChangeEntry("R1", None, "10k")
    assert entries[1] == ChangeEntry("C1", "remove", "")


def test_invalid_op_value_is_a_problem():
    csv = "Reference,Part,OP\nR1,10k,delete\n"
    entries, problems = parse_change_csv(csv)
    assert entries == []
    assert len(problems) == 1
    assert problems[0].kind == "bad_op"
    assert problems[0].reference == "R1"
    assert "delete" in problems[0].detail


def test_splits_comma_merged_references_sharing_one_row():
    csv = 'Reference,Part,OP\n"R67, R24",1kR,modify\n'
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R67", "modify", "1kR"),
                       ChangeEntry("R24", "modify", "1kR")]


def test_trailing_comma_in_merged_cell_ignored():
    csv = 'Reference,Part\n"R1,",10k\n'
    entries, problems = parse_change_csv(csv)
    assert problems == []
    assert entries == [ChangeEntry("R1", None, "10k")]


def test_empty_reference_is_a_problem():
    csv = "Reference,Part\n,10k\n"
    entries, problems = parse_change_csv(csv)
    assert entries == []
    assert [p.kind for p in problems] == ["empty_reference"]


def test_duplicate_reference_within_csv_is_a_problem_first_wins():
    csv = "Reference,Part\nR1,10k\nR1,22k\n"
    entries, problems = parse_change_csv(csv)
    assert entries == [ChangeEntry("R1", None, "10k")]
    assert [p.kind for p in problems] == ["duplicate"]
    assert problems[0].reference == "R1"


def test_duplicate_detected_across_merged_cells():
    csv = 'Reference,Part\n"R1,R2",10k\nR2,22k\n'
    entries, problems = parse_change_csv(csv)
    assert len(entries) == 2
    assert [p.kind for p in problems] == ["duplicate"]


BOM = {"R1": "10k", "C1": "100nF"}


def test_infers_modify_for_existing_reference():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", None, "47k")])
    assert problems == []
    assert changes == [PlannedChange("R1", "modify", "47k")]


def test_infers_add_for_new_reference():
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", None, "1uF")])
    assert problems == []
    assert changes == [PlannedChange("R9", "add", "1uF")]


def test_explicit_op_wins_over_inference():
    """位号不存在但显式写 modify → 走 modify，于是校验失败。"""
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", "modify", "1uF")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]
    assert "不存在" in problems[0].detail


def test_remove_drops_part_value():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", "remove", "随便写的")])
    assert problems == []
    assert changes == [PlannedChange("R1", "remove", None)]


def test_remove_allows_empty_part():
    changes, problems = plan_changes(BOM, [ChangeEntry("C1", "remove", "")])
    assert problems == []
    assert changes == [PlannedChange("C1", "remove", None)]


def test_remove_of_unplaced_reference_is_a_problem():
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", "remove", "")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]


def test_add_of_existing_reference_is_a_problem():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", "add", "22k")])
    assert changes == []
    assert problems[0].reference == "R1"
    assert "已存在" in problems[0].detail


def test_empty_part_is_a_problem_for_add():
    changes, problems = plan_changes(BOM, [ChangeEntry("R9", "add", "")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]


def test_empty_part_is_a_problem_for_inferred_modify():
    """无 OP 列时空 Part 推断不出「不贴」，必须报错。"""
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", None, "")])
    assert changes == []
    assert [p.kind for p in problems] == ["invalid"]


def test_modify_to_same_value_is_a_harmless_noop_not_a_problem():
    changes, problems = plan_changes(BOM, [ChangeEntry("R1", None, "10k")])
    assert problems == []
    assert changes == [PlannedChange("R1", "modify", "10k")]


def test_good_rows_and_bad_rows_both_reported():
    """问题行不阻止其余行进入 changes；是否应用由调用方按「全对才应用」决定。"""
    changes, problems = plan_changes(BOM, [
        ChangeEntry("R1", None, "47k"),
        ChangeEntry("R1x", "modify", "1k"),
        ChangeEntry("C1", "remove", ""),
    ])
    assert changes == [PlannedChange("R1", "modify", "47k"),
                       PlannedChange("C1", "remove", None)]
    assert [p.reference for p in problems] == ["R1x"]


def test_change_csv_template_has_three_headers_and_no_rows():
    """issue #112：修改清单 CSV 模板含 Reference/Part/OP 三列表头，无数据行。"""
    tmpl = change_csv_template()
    assert tmpl == "Reference,Part,OP\n"
    # 模板应能被 parse_change_csv 反向解析：无条目、无问题
    entries, problems = parse_change_csv(tmpl)
    assert entries == []
    assert problems == []


def test_full_bom_is_not_mutated():
    bom = dict(BOM)
    plan_changes(bom, [ChangeEntry("R1", "remove", ""), ChangeEntry("R9", "add", "1k")])
    assert bom == BOM
