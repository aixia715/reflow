"""元器件值 Lint（纯逻辑，零 Web/DB 依赖）。"""

from app.component_lint import LintIssue, lint_part


def test_spec_already_standard_no_issue():
    assert lint_part("10pF") == ("10pF", [])


def test_spec_magnitude_normalized():
    value, issues = lint_part("1000pF")
    assert value == "1nF"
    assert issues == [LintIssue("fix", "修正: 1000pF → 1nF")]


def test_spec_magnitude_normalized_down():
    value, issues = lint_part("0.1uF")
    assert value == "100nF"
    assert issues == [LintIssue("fix", "修正: 0.1uF → 100nF")]


def test_spec_chained_fixes():
    value, issues = lint_part("1000 KR")
    assert value == "1MR"
    assert [i.level for i in issues] == ["fix", "fix", "fix"]


def test_unit_alias_ohm():
    value, issues = lint_part("10ohm")
    assert value == "10R"
    assert issues == [LintIssue("fix", "修正: 10ohm → 10R")]


def test_unit_alias_omega():
    value, issues = lint_part("10Ω")
    assert value == "10R"
    assert issues == [LintIssue("fix", "修正: 10Ω → 10R")]


def test_unit_case_fix():
    value, issues = lint_part("100mh")
    assert value == "100mH"
    assert issues == [LintIssue("fix", "修正: 100mh → 100mH")]


def test_missing_unit_warns():
    value, issues = lint_part("10")
    assert value == "10"
    assert issues == [LintIssue("warning", "警告: 无法识别的规格格式（10）")]


def test_unsupported_prefix_warns():
    value, issues = lint_part("10XR")
    assert value == "10XR"
    assert issues == [LintIssue("warning", "警告: 无法识别的规格格式（10XR）")]


def test_zero_value_untouched():
    assert lint_part("0R") == ("0R", [])


def test_model_untouched():
    assert lint_part("OP27") == ("OP27", [])


def test_model_trims_whitespace():
    value, issues = lint_part(" OP27 ")
    assert value == "OP27"
    assert issues == [LintIssue("fix", "修正: 去除前后空白 → OP27")]


def test_model_ordinary_trailing_char_untouched():
    assert lint_part("OP27~") == ("OP27~", [])


def test_no_suffix_handling_trailing_star_is_just_bad_format():
    # 后缀概念（*, %, !, #）不在本项目范围内，不做任何特殊剥离/保留处理，
    # 结尾的这类字符只是普通字符——落在 SPEC 里就直接判无法识别格式。
    value, issues = lint_part("10pF*")
    assert value == "10pF*"
    assert issues == [LintIssue("warning", "警告: 无法识别的规格格式（10pF*）")]


def test_e192_exact_match_no_issue():
    assert lint_part("150R") == ("150R", [])


def test_e24_resistor_no_issue():
    # 220 不是 E192 成员，但它是常见的 E24（5%容差）标准值，不应报警。
    assert lint_part("220R") == ("220R", [])


def test_off_grid_resistor_warns_with_nearest_suggestion():
    # 230 既不在 E192 也不在 E24 里，相邻的 229 / 232 里 229 更近。
    value, issues = lint_part("230R")
    assert value == "230R"
    assert issues == [
        LintIssue("warning", "警告: 阻值不是标准阻值（230R），最接近的标准值：229R")
    ]


def test_excess_precision_resistor_warns():
    # 456.7 超出标准阻值的三位有效数字精度，453 / 459 里 459 更近。
    value, issues = lint_part("4.567kR")
    assert value == "4.567kR"
    assert issues == [
        LintIssue(
            "warning",
            "警告: 阻值不是标准阻值（4.567kR），最接近的标准值：4.59kR",
        )
    ]


def test_e192_only_applies_to_resistors_not_capacitors():
    # 2.2 不是 E192 成员，但 E192 只管电阻；电容/电感不做该项检查。
    assert lint_part("2.2uF") == ("2.2uF", [])


def test_e192_only_applies_to_resistors_not_inductors():
    assert lint_part("4.7uH") == ("4.7uH", [])


def test_e192_skips_after_earlier_warning():
    # 已产生格式警告，后续规则（含 E192 校验）不再执行。
    value, issues = lint_part("10XR")
    assert len(issues) == 1
