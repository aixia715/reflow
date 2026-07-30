"""统一预览行表 build_preview_rows 的纯逻辑测试。

导入预览界面把「变更 / 问题 / lint 提示」合成一张表来渲染，这里守住合成规则。
"""

from app.csv_import import (
    CsvProblem, LintNote, PlannedChange, build_preview_rows,
)


def test_change_without_notes_is_a_plain_row():
    rows = build_preview_rows([PlannedChange("R1", "modify", "47k")], [], [])
    assert len(rows) == 1
    assert rows[0]._asdict() == {"reference": "R1", "op": "modify", "part": "47k",
                                 "fix": "", "warning": "", "error": ""}


def test_fix_note_attaches_to_its_change_row():
    notes = [LintNote("fix", "R1", "修正: 1000pF → 1nF")]
    rows = build_preview_rows([PlannedChange("R1", "add", "1nF")], [], notes)
    assert rows[0].fix == "修正: 1000pF → 1nF"
    assert rows[0].warning == ""


def test_fix_and_warning_can_coexist_on_one_row():
    notes = [LintNote("fix", "R2", "修正: 去除前后空白 → 230R"),
             LintNote("warning", "R2", "警告: 阻值不是标准阻值（230R）")]
    rows = build_preview_rows([PlannedChange("R2", "modify", "230R")], [], notes)
    assert rows[0].fix.startswith("修正:")
    assert rows[0].warning.startswith("警告:")


def test_only_first_note_of_each_level_is_kept():
    notes = [LintNote("warning", "R1", "第一条"), LintNote("warning", "R1", "第二条")]
    rows = build_preview_rows([PlannedChange("R1", "modify", "1k")], [], notes)
    assert rows[0].warning == "第一条"


def test_problem_rows_come_first_and_carry_the_error():
    problems = [CsvProblem("invalid", "R9", "位号 R9 已存在，请用「修改」")]
    rows = build_preview_rows([PlannedChange("R1", "modify", "47k")], problems, [])
    assert [r.reference for r in rows] == ["R9", "R1"]
    assert rows[0].error == "位号 R9 已存在，请用「修改」"
    assert rows[0].op is None and rows[0].part is None
    assert rows[1].error == ""


def test_problem_row_also_gets_its_lint_notes():
    """全量模式下问题位号被两端剔除，lint 提示只能挂回它的问题行。"""
    problems = [CsvProblem("empty_part", "C1", "Part 为空")]
    notes = [LintNote("warning", "C1", "警告: 无法识别的规格格式（xx）")]
    rows = build_preview_rows([], problems, notes)
    assert rows[0].error == "Part 为空"
    assert rows[0].warning.startswith("警告:")


def test_orphan_notes_are_dropped():
    """全量模式里「修正后与现值相同」不产生变更行，其 lint 提示随之丢弃。"""
    notes = [LintNote("fix", "R7", "修正: 1K → 1k")]
    rows = build_preview_rows([PlannedChange("R1", "modify", "47k")], [], notes)
    assert len(rows) == 1
    assert rows[0].reference == "R1"
    assert rows[0].fix == ""


def test_empty_input_yields_no_rows():
    assert build_preview_rows([], [], []) == []
