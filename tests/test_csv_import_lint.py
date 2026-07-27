"""CSV 导入条目的元器件值 lint 集成（纯逻辑，零 Web/DB 依赖）。"""

from app.csv_import import ChangeEntry, CsvEntry, LintNote, lint_entries


def test_lint_entries_applies_fix_and_reports_it():
    linted, notes = lint_entries([CsvEntry("R1", "1000pF")])
    assert linted == [CsvEntry("R1", "1nF")]
    assert notes == [LintNote("fix", "R1", "修正: 1000pF → 1nF")]


def test_lint_entries_warns_without_changing_value():
    linted, notes = lint_entries([CsvEntry("R1", "230R")])
    assert linted == [CsvEntry("R1", "230R")]
    assert notes == [
        LintNote("warning", "R1", "警告: 阻值不是标准阻值（230R），最接近的标准值：229R")
    ]


def test_lint_entries_skips_non_rcl_reference():
    linted, notes = lint_entries([CsvEntry("U1", "555")])
    assert linted == [CsvEntry("U1", "555")]
    assert notes == []


def test_lint_entries_no_issue_stays_silent():
    linted, notes = lint_entries([CsvEntry("R1", "10pF")])
    assert linted == [CsvEntry("R1", "10pF")]
    assert notes == []


def test_lint_entries_preserves_order_for_multiple_rows():
    linted, notes = lint_entries([
        CsvEntry("R1", "1000pF"),
        CsvEntry("R2", "230R"),
        CsvEntry("U1", "555"),
    ])
    assert linted == [
        CsvEntry("R1", "1nF"),
        CsvEntry("R2", "230R"),
        CsvEntry("U1", "555"),
    ]
    assert [n.reference for n in notes] == ["R1", "R2"]


def test_lint_entries_works_with_change_entries_too():
    # ChangeEntry 比 CsvEntry 多一个 op 字段，lint_entries 靠 _replace 通用处理两者。
    linted, notes = lint_entries([ChangeEntry("R1", "add", "1000pF")])
    assert linted == [ChangeEntry("R1", "add", "1nF")]
    assert notes == [LintNote("fix", "R1", "修正: 1000pF → 1nF")]
