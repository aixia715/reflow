from app.bom_export import bom_to_csv, natural_sort_key
from app.csv_import import parse_bom_csv


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
