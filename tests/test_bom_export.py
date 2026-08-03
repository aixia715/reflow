from app.bom_export import bom_to_csv, changes_to_csv, natural_sort_key
from app.csv_import import parse_bom_csv, parse_change_csv


def test_empty_bom_only_header():
    assert bom_to_csv({}) == "Reference,Part\r\n"


def test_header_and_rows():
    csv_text = bom_to_csv({"R1": "10k", "C1": "100nF"})
    lines = csv_text.splitlines()
    assert lines[0] == "Reference,Part"
    assert "R1,10k" in lines
    assert "C1,100nF" in lines


def test_natural_sort_order():
    bom = {"R10": "a", "R2": "a", "R1": "a", "R100": "a"}
    lines = bom_to_csv(bom).splitlines()[1:]
    refs = [line.split(",")[0] for line in lines]
    assert refs == ["R1", "R2", "R10", "R100"]


def test_dnp_not_present():
    # 不贴位号天然不在 dict 中，导出里不应出现
    csv_text = bom_to_csv({"R1": "10k"})
    assert "C1" not in csv_text


def test_quotes_commas_newlines_escaped():
    bom = {"R1": 'has,comma', "R2": 'has"quote', "R3": "has\nnewline"}
    csv_text = bom_to_csv(bom)
    entries, problems = parse_bom_csv(csv_text)
    assert problems == []
    got = {e.reference: e.part for e in entries}
    assert got == bom


def test_chinese_content():
    bom = {"电阻1": "贴片电阻 10k", "R2": "电容"}
    csv_text = bom_to_csv(bom)
    entries, _ = parse_bom_csv(csv_text)
    got = {e.reference: e.part for e in entries}
    assert got == bom


def test_roundtrip_through_csv_import():
    bom = {"R1": "10k", "C1": "100nF", "U1": "ATmega328"}
    csv_text = bom_to_csv(bom)
    entries, problems = parse_bom_csv(csv_text)
    assert problems == []
    assert {e.reference: e.part for e in entries} == bom


def test_natural_sort_key_mixed():
    assert natural_sort_key("R10") > natural_sort_key("R2")
    assert natural_sort_key("R2") > natural_sort_key("R1")


# ---- 修改项导出（changes_to_csv）----

def test_changes_empty_only_header():
    # 表头与 csv_import.change_csv_template() 一致，可被反向导入为「无条目」
    assert changes_to_csv([]) == "Reference,Part,OP\r\n"


def test_changes_three_ops():
    lines = changes_to_csv([
        {"reference": "U1", "op": "add", "part": "MCU"},
        {"reference": "R1", "op": "modify", "part": "47k"},
        {"reference": "C1", "op": "remove", "part": None},
    ]).splitlines()
    assert lines[0] == "Reference,Part,OP"
    assert "U1,MCU,add" in lines
    assert "R1,47k,modify" in lines
    # remove 行 Part 留空
    assert "C1,,remove" in lines


def test_changes_remove_part_blank_even_if_stored():
    # 历史数据里 remove 行可能残留 part，导出一律留空
    lines = changes_to_csv([{"reference": "C1", "op": "remove", "part": "100nF"}]).splitlines()
    assert lines[1] == "C1,,remove"


def test_changes_natural_sort_order():
    refs = [line.split(",")[0] for line in changes_to_csv([
        {"reference": "R100", "op": "add", "part": "a"},
        {"reference": "R2", "op": "add", "part": "a"},
        {"reference": "R10", "op": "add", "part": "a"},
        {"reference": "R1", "op": "add", "part": "a"},
    ]).splitlines()[1:]]
    assert refs == ["R1", "R2", "R10", "R100"]


def test_changes_quotes_commas_escaped():
    changes = [
        {"reference": "R1", "op": "modify", "part": "has,comma"},
        {"reference": "R2", "op": "add", "part": 'has"quote'},
    ]
    entries, problems = parse_change_csv(changes_to_csv(changes))
    assert problems == []
    assert {e.reference: e.part for e in entries} == {"R1": "has,comma", "R2": 'has"quote'}


def test_changes_roundtrip_through_change_import():
    # 导出的修改项 CSV 能被「从 CSV 导入修改」原样读回（在别处重放同一批修改）
    changes = [
        {"reference": "U1", "op": "add", "part": "MCU"},
        {"reference": "R1", "op": "modify", "part": "47k"},
        {"reference": "C1", "op": "remove", "part": None},
    ]
    entries, problems = parse_change_csv(changes_to_csv(changes))
    assert problems == []
    assert [(e.reference, e.op, e.part) for e in entries] == [
        ("C1", "remove", ""), ("R1", "modify", "47k"), ("U1", "add", "MCU"),
    ]
