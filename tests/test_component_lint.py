"""元器件值 Lint（纯逻辑，零 Web/DB 依赖）。"""

from app.component_lint import (
    _E24_MANTISSAS,
    _E96_MANTISSAS,
    _E192_MANTISSAS,
    LintIssue,
    lint_part,
)

# IEC 60063 权威数值（用于校验硬编码表没有手抄错误，来源与 E192/E24/E96 相同）。
_E48_REFERENCE = (
    1.00, 1.05, 1.10, 1.15, 1.21, 1.27, 1.33, 1.40, 1.47, 1.54, 1.62, 1.69,
    1.78, 1.87, 1.96, 2.05, 2.15, 2.26, 2.37, 2.49, 2.61, 2.74, 2.87, 3.01,
    3.16, 3.32, 3.48, 3.65, 3.83, 4.02, 4.22, 4.42, 4.64, 4.87, 5.11, 5.36,
    5.62, 5.90, 6.19, 6.49, 6.81, 7.15, 7.50, 7.87, 8.25, 8.66, 9.09, 9.53,
)
_E12_REFERENCE = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)
_E6_REFERENCE = (1.0, 1.5, 2.2, 3.3, 4.7, 6.8)


def test_e192_table_size_and_bounds():
    assert len(_E192_MANTISSAS) == 192
    assert _E192_MANTISSAS[0] == 100
    assert _E192_MANTISSAS[-1] == 988


def test_e96_table_size_and_bounds():
    assert len(_E96_MANTISSAS) == 96
    assert _E96_MANTISSAS[0] == 100
    assert _E96_MANTISSAS[-1] == 976


def test_e24_table_size_and_bounds():
    assert len(_E24_MANTISSAS) == 24
    assert _E24_MANTISSAS[0] == 100
    assert _E24_MANTISSAS[-1] == 910


def test_e48_is_subset_of_e192():
    # E48/E96 用同一套三位有效数字舍入规则，E48 必然也落在 E192 表里。
    assert {round(v * 100) for v in _E48_REFERENCE} <= set(_E192_MANTISSAS)


def test_e96_is_subset_of_e192():
    assert set(_E96_MANTISSAS) <= set(_E192_MANTISSAS)


def test_e12_is_subset_of_e24():
    # E12/E6 用同一套两位有效数字舍入规则，E12 必然也落在 E24 表里。
    assert {round(v * 100) for v in _E12_REFERENCE} <= set(_E24_MANTISSAS)


def test_e6_is_subset_of_e24():
    assert {round(v * 100) for v in _E6_REFERENCE} <= set(_E24_MANTISSAS)


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


def test_unit_alias_with_si_prefix():
    # §10：别名必须位于字符串末尾，前面的 SI 前缀要保留。
    value, issues = lint_part("10kOHM")
    assert value == "10kR"
    assert issues == [LintIssue("fix", "修正: 10kOHM → 10kR")]


def test_unit_alias_henry_abbreviation():
    value, issues = lint_part("4.7Hy")
    assert value == "4.7H"
    assert issues == [LintIssue("fix", "修正: 4.7Hy → 4.7H")]


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


def test_near_top_of_decade_suggests_next_decade_boundary():
    # 9.99 离下一档的 10.0（差 0.01）比本档最大值 9.88（差 0.11）近得多，
    # 推荐值要跨十进制档才对。
    value, issues = lint_part("9.99R")
    assert value == "9.99R"
    assert issues == [
        LintIssue("warning", "警告: 阻值不是标准阻值（9.99R），最接近的标准值：10R")
    ]


def test_near_top_of_decade_with_larger_magnitude_still_correct():
    value, issues = lint_part("999R")
    assert value == "999R"
    assert issues == [
        LintIssue("warning", "警告: 阻值不是标准阻值（999R），最接近的标准值：1kR")
    ]


def test_near_top_of_decade_but_still_closer_to_table_max():
    # 9.9 离本档最大值 9.88（差 0.02）比下一档的 10.0（差 0.1）更近，
    # 不应该被跨档修复逻辑带偏。
    value, issues = lint_part("9.9R")
    assert value == "9.9R"
    assert issues == [
        LintIssue("warning", "警告: 阻值不是标准阻值（9.9R），最接近的标准值：9.88R")
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


def test_resistor_uses_e192_not_capped_to_e96():
    # 459 是 E192 成员但不是 E96 成员——电阻用更精细的 E192，不封顶到 E96。
    assert lint_part("459R") == ("459R", [])


def test_capacitor_e96_member_no_issue():
    assert lint_part("150nF") == ("150nF", [])


def test_capacitor_e24_member_no_issue():
    # 220 不是 E96 成员，但电容/电感的标准值封顶到 E96 时仍并入 E24（220 是 E24 成员）。
    assert lint_part("2.2uF") == ("2.2uF", [])


def test_inductor_e24_member_no_issue():
    # 470 不是 E96 成员，但是 E24 成员。
    assert lint_part("4.7uH") == ("4.7uH", [])


def test_capacitor_off_grid_warns_with_nearest_suggestion():
    value, issues = lint_part("230nF")
    assert value == "230nF"
    assert issues == [
        LintIssue("warning", "警告: 容值不是标准容值（230nF），最接近的标准值：232nF")
    ]


def test_inductor_off_grid_warns_with_nearest_suggestion():
    value, issues = lint_part("230mH")
    assert value == "230mH"
    assert issues == [
        LintIssue("warning", "警告: 感值不是标准感值（230mH），最接近的标准值：232mH")
    ]


def test_capacitor_excess_precision_capped_to_e96():
    # 电容封顶到 E96：456.7 附近 E96 只有 453（E192 独有的 459 不算数），取 453。
    value, issues = lint_part("4.567uF")
    assert value == "4.567uF"
    assert issues == [
        LintIssue(
            "warning",
            "警告: 容值不是标准容值（4.567uF），最接近的标准值：4.53uF",
        )
    ]


def test_e192_skips_after_earlier_warning():
    # 已产生格式警告，后续规则（含 E192 校验）不再执行。
    value, issues = lint_part("10XR")
    assert len(issues) == 1
