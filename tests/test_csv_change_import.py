"""issue #108：工作区从 CSV 导入修改项 —— 纯逻辑层。"""
import pytest

from app.csv_import import ChangeEntry, parse_change_csv


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
    entries, _ = parse_change_csv(csv)
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
