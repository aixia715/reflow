import pytest

from app.csv_import import parse_bom_csv, CsvEntry, CsvProblem


def test_splits_comma_merged_references_sharing_one_part():
    csv = 'Item,Quantity,Reference,Part,PCB Footprint\n1,2,"R67,R24",1kR,0402\n'
    entries, problems = parse_bom_csv(csv)
    assert problems == []
    assert CsvEntry("R67", "1kR") in entries
    assert CsvEntry("R24", "1kR") in entries
    assert len(entries) == 2


def test_only_reference_and_part_columns_used():
    csv = "Item,Quantity,Reference,Part,Assembly Type\n5,1,R1,10k,SMT\n"
    entries, _ = parse_bom_csv(csv)
    assert entries == [CsvEntry("R1", "10k")]


def test_strips_utf8_bom_and_handles_crlf():
    csv = "﻿Reference,Part\r\nR1,10k\r\nR2,100nF\r\n"
    entries, problems = parse_bom_csv(csv)
    assert problems == []
    assert entries == [CsvEntry("R1", "10k"), CsvEntry("R2", "100nF")]


def test_strips_reference_whitespace_and_keeps_underscore_suffix():
    csv = 'Reference,Part\n" C86_PD1 , R5 ",HE364B-G\n'
    entries, _ = parse_bom_csv(csv)
    assert CsvEntry("C86_PD1", "HE364B-G") in entries
    assert CsvEntry("R5", "HE364B-G") in entries


def test_duplicate_reference_reported_and_first_wins():
    csv = "Reference,Part\nR1,10k\nR1,22k\n"
    entries, problems = parse_bom_csv(csv)
    assert entries == [CsvEntry("R1", "10k")]
    assert any(p.kind == "duplicate" and p.reference == "R1" for p in problems)


def test_empty_part_reported_but_entry_kept():
    csv = "Reference,Part\nR9,\n"
    entries, problems = parse_bom_csv(csv)
    assert entries == [CsvEntry("R9", "")]
    assert any(p.kind == "empty_part" and p.reference == "R9" for p in problems)


def test_missing_required_columns_raises():
    with pytest.raises(ValueError):
        parse_bom_csv("Item,Quantity\n1,2\n")


def test_blank_rows_from_excel_are_skipped():
    # Excel 保存 CSV 时空行在文件里是一行逗号、无文字内容，应自动跳过，不报问题。
    csv = "Reference,Part\nR1,10k\n,,\nR2,100nF\n"
    entries, problems = parse_bom_csv(csv)
    assert problems == []
    assert entries == [CsvEntry("R1", "10k"), CsvEntry("R2", "100nF")]


def test_blank_row_with_extra_columns_skipped():
    # 表头多于两列时空行同样是全空，应跳过。
    csv = "Item,Quantity,Reference,Part,PCB Footprint\n1,2,R1,10k,0402\n,,,,\n2,1,R2,100nF,0402\n"
    entries, problems = parse_bom_csv(csv)
    assert problems == []
    assert entries == [CsvEntry("R1", "10k"), CsvEntry("R2", "100nF")]


def test_duplicate_with_empty_part_does_not_emit_empty_part_problem():
    # 首条 R1=10k 进入 entries；第二条 R1 是重复且 Part 为空，
    # 应只报 duplicate，不应为这个被丢弃的重复行误报 empty_part。
    csv = "Reference,Part\nR1,10k\nR1,\n"
    entries, problems = parse_bom_csv(csv)
    assert entries == [CsvEntry("R1", "10k")]
    assert any(p.kind == "duplicate" and p.reference == "R1" for p in problems)
    assert not any(p.kind == "empty_part" for p in problems)
