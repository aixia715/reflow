"""元器件值 Lint：校验并标准化 BOM 中的元器件值（纯逻辑，零 Web/DB 依赖）。

不处理装配属性后缀（*, %, !, # 等）——本项目不会遇到这类记法，
落在 Part 字符串里的这些字符一律当普通字符处理。
"""

import re
from bisect import bisect_left
from decimal import Decimal
from typing import NamedTuple

_SPEC_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)([fpnumkMGT]?)([RFH])$")

_UNIT_ALIASES = (
    ("ohm", "R"),
    ("Ω", "R"),
    ("farad", "F"),
    ("henry", "H"),
    ("Hy", "H"),
)

_UNIT_CASE_FIX = {"r": "R", "f": "F", "h": "H"}

# 这里的 "F" 键是 SI 前缀 femto 误写成大写（§12），跟单位字母 F（法拉）撞写法；
# 只有 body[-2]（倒数第二位，前缀槽位）落在这张表里才会生效，body[-1]（最后一位，
# 单位槽位）另外校验，两者不会混淆——但改这张表时要留意这个撞字母。
_PREFIX_CASE_FIX = {"K": "k", "P": "p", "N": "n", "U": "u", "F": "f", "g": "G", "t": "T"}

_PREFIX_EXPONENT = {"f": -15, "p": -12, "n": -9, "u": -6, "m": -3, "": 0, "k": 3, "M": 6, "G": 9, "T": 12}
_PREFIX_ORDER = ["f", "p", "n", "u", "m", "", "k", "M", "G", "T"]

# IEC 60063 E192（0.5% 精度电阻）标准值，每个十进制段内的三位有效数字尾数。
# 实际 BOM 里各种容差的电阻都会用到，E192 只是最精确的一档，校验时要与
# E24（覆盖 E12/E6）合并成完整的标准阻值集合，而不是只认 E192。
_E192_MANTISSAS = (
    100, 101, 102, 104, 105, 106, 107, 109, 110, 111, 113, 114, 115, 117, 118,
    120, 121, 123, 124, 126, 127, 129, 130, 132, 133, 135, 137, 138, 140, 142,
    143, 145, 147, 149, 150, 152, 154, 156, 158, 160, 162, 164, 165, 167, 169,
    172, 174, 176, 178, 180, 182, 184, 187, 189, 191, 193, 196, 198, 200, 203,
    205, 208, 210, 213, 215, 218, 221, 223, 226, 229, 232, 234, 237, 240, 243,
    246, 249, 252, 255, 258, 261, 264, 267, 271, 274, 277, 280, 284, 287, 291,
    294, 298, 301, 305, 309, 312, 316, 320, 324, 328, 332, 336, 340, 344, 348,
    352, 357, 361, 365, 370, 374, 379, 383, 388, 392, 397, 402, 407, 412, 417,
    422, 427, 432, 437, 442, 448, 453, 459, 464, 470, 475, 481, 487, 493, 499,
    505, 511, 517, 523, 530, 536, 542, 549, 556, 562, 569, 576, 583, 590, 597,
    604, 612, 619, 626, 634, 642, 649, 657, 665, 673, 681, 690, 698, 706, 715,
    723, 732, 741, 750, 759, 768, 777, 787, 796, 806, 816, 825, 835, 845, 856,
    866, 876, 887, 898, 909, 920, 931, 942, 953, 965, 976, 988,
)

# E48/E96 是 E192 的严格子集（同一套三位有效数字舍入规则，间隔取样），
# E12/E6 是 E24 的严格子集；两族舍入规则不同（E24 只取两位有效数字），
# 互不包含，因此覆盖全部常见容差只需 E192 + E24 这两张表。
_E24_MANTISSAS = (
    100, 110, 120, 130, 150, 160, 180, 200, 220, 240, 270, 300,
    330, 360, 390, 430, 470, 510, 560, 620, 680, 750, 820, 910,
)

# E96（1% 精度）——电容/电感商用件很少突破这个精度，标准值封顶到这里。
_E96_MANTISSAS = (
    100, 102, 105, 107, 110, 113, 115, 118, 121, 124, 127, 130, 133, 137, 140,
    143, 147, 150, 154, 158, 162, 165, 169, 174, 178, 182, 187, 191, 196, 200,
    205, 210, 215, 221, 226, 232, 237, 243, 249, 255, 261, 267, 274, 280, 287,
    294, 301, 309, 316, 324, 332, 340, 348, 357, 365, 374, 383, 392, 402, 412,
    422, 432, 442, 453, 464, 475, 487, 499, 511, 523, 536, 549, 562, 576, 590,
    604, 619, 634, 649, 665, 681, 698, 715, 732, 750, 768, 787, 806, 825, 845,
    866, 887, 909, 931, 953, 976,
)

# 电阻用最精细的 E192，电容/电感封顶到 E96——两者都要并上 E24（覆盖 E12/E6）。
_STANDARD_MANTISSAS_R = tuple(sorted(set(_E192_MANTISSAS) | set(_E24_MANTISSAS)))
_STANDARD_MANTISSAS_CF = tuple(sorted(set(_E96_MANTISSAS) | set(_E24_MANTISSAS)))

_STANDARD_MANTISSAS_BY_UNIT = {
    "R": _STANDARD_MANTISSAS_R,
    "F": _STANDARD_MANTISSAS_CF,
    "H": _STANDARD_MANTISSAS_CF,
}
_STANDARD_SETS_BY_UNIT = {unit: frozenset(m) for unit, m in _STANDARD_MANTISSAS_BY_UNIT.items()}
_UNIT_VALUE_LABEL = {"R": "阻值", "F": "容值", "H": "感值"}

# 只有电阻/电容/电感位号才套用 R/F/H 这套规则；芯片、分立器件等型号常以数字开头
# （555、741、74HC04、2N3904……），套用同一套规则会把型号误判成"格式不对的规格值"。
# 只认标准单字母前缀，不识别 RT/RV 这类变体（先做最简单版本）。
_LINTED_REFERENCE_PREFIXES = frozenset({"R", "C", "L"})
_REFERENCE_PREFIX_PATTERN = re.compile(r"^[A-Za-z]+")


def _is_linted_reference(reference: str) -> bool:
    match = _REFERENCE_PREFIX_PATTERN.match(reference)
    return match is not None and match.group(0) in _LINTED_REFERENCE_PREFIXES


class LintIssue(NamedTuple):
    level: str  # "fix" | "warning"
    message: str


def lint_part(reference: str, raw_part: str) -> tuple[str, list[LintIssue]]:
    """校验并标准化一个元器件值，返回 (标准化结果, 问题列表)。

    只有位号前缀是 R/C/L（电阻/电容/电感）才执行完整的单位/前缀标准化与量级
    归一；其余位号（芯片、分立器件……）只做首尾空白清理，不套用 R/F/H 规则，
    因为它们的型号经常以数字开头，容易被误判成格式不对的规格值。
    型号类值（不以数字开头）同样只做首尾空白清理。任意一条规则产生 warning
    后立即停止后续规则。

    归一化是一条多规则流水线，但对外只报**结论**：值经过多步改写时，中间态
    不作为提示暴露（见 `_consolidate_fixes`）。
    """
    value, issues = _lint_part_steps(reference, raw_part)
    return value, _consolidate_fixes(raw_part, value, issues)


def _consolidate_fixes(raw_part: str, value: str, issues: list[LintIssue]) -> list[LintIssue]:
    """多条 fix 合并成一条「原值 → 最终值」；单条保持原措辞。

    逐规则记录会把流水线的中间态当成结论讲给用户，而中间态可能被下一条规则
    立刻推翻——例如 3.9Nf 先经单位大小写变成 3.9NF，再经前缀大小写变成 3.9nF，
    逐条记录的第一条「修正: 3.9Nf → 3.9NF」单独读就是错的建议。用户只关心
    最终改成了什么，顺带也让「已自动修正 N 处」按值计数而不是按步。
    单条 fix 无中间态可言，保留原措辞（「去除前后空白 → X」这类说法没有等价
    的原值→最终值写法）。原值取 strip 后的，避免提示里出现看不见的首尾空白。
    """
    fixes = [i for i in issues if i.level == "fix"]
    if len(fixes) < 2:
        return issues
    rest = [i for i in issues if i.level != "fix"]
    return [LintIssue("fix", f"修正: {raw_part.strip()} → {value}")] + rest


def _lint_part_steps(reference: str, raw_part: str) -> tuple[str, list[LintIssue]]:
    """跑完整条归一化流水线，逐规则记录（含中间态）。仅供 `lint_part` 收敛后对外。"""
    issues: list[LintIssue] = []

    stripped = raw_part.strip()
    if stripped != raw_part:
        issues.append(LintIssue("fix", f"修正: 去除前后空白 → {stripped}"))

    if not _is_linted_reference(reference):
        return stripped, issues

    if not stripped or not stripped[0].isdigit():
        return stripped, issues

    body = stripped
    body = _normalize_internal_space(body, issues)
    body = _normalize_unit_alias(body, issues)
    body = _normalize_unit_case(body, issues)
    body = _normalize_prefix_case(body, issues)

    match = _SPEC_PATTERN.fullmatch(body)
    if not match:
        issues.append(LintIssue("warning", f"警告: 无法识别的规格格式（{body}）"))
        return body, issues

    number_str, prefix, unit = match.groups()
    mantissa = Decimal(number_str)
    exponent = _PREFIX_EXPONENT[prefix]

    body, mantissa, prefix = _normalize_magnitude(mantissa, exponent, unit, body, issues)

    if mantissa != 0:
        warning = _check_standard_value(mantissa, prefix, unit, body)
        if warning is not None:
            issues.append(warning)

    return body, issues


def lint_warning_for(reference: str, part: str | None) -> str | None:
    """取某个**已落库**的元器件值的警告文案，没问题返回 None。

    落库前 `_lint_or_none` 已经把 fix 级问题（空白、单位大小写、量级）消化掉了，
    存的就是标准化后的值，所以这里重跑 lint 只会剩下 warning 级——即「格式认不出」
    或「不是标准值」这类需要人来判断、工具不该擅自改的问题。
    渲染时实时算而不是落库时存，是因为 lint 规则会演进，值才是唯一事实来源。
    每个值至多触发一条 warning（格式警告会提前返回），故取第一条即可。
    """
    if part is None:
        return None
    for issue in lint_part(reference, part)[1]:
        if issue.level == "warning":
            return issue.message
    return None


def _normalize_internal_space(body: str, issues: list[LintIssue]) -> str:
    normalized = re.sub(r"(\d)\s+(?=[a-zA-ZΩ])", r"\1", body)
    if normalized != body:
        issues.append(LintIssue("fix", f"修正: {body} → {normalized}"))
    return normalized


def _normalize_unit_alias(body: str, issues: list[LintIssue]) -> str:
    # 只做尾部匹配，不检查别名前面是不是数字——调用方（lint_part）已经先用
    # stripped[0].isdigit() 把关，只有数字开头的 SPEC 候选才会走到这里。
    for alias, unit in _UNIT_ALIASES:
        if body.lower().endswith(alias.lower()):
            normalized = body[: -len(alias)] + unit
            issues.append(LintIssue("fix", f"修正: {body} → {normalized}"))
            return normalized
    return body


def _normalize_unit_case(body: str, issues: list[LintIssue]) -> str:
    if body and body[-1] in _UNIT_CASE_FIX:
        normalized = body[:-1] + _UNIT_CASE_FIX[body[-1]]
        issues.append(LintIssue("fix", f"修正: {body} → {normalized}"))
        return normalized
    return body


def _normalize_prefix_case(body: str, issues: list[LintIssue]) -> str:
    if len(body) >= 2 and body[-1] in ("R", "F", "H") and body[-2] in _PREFIX_CASE_FIX:
        normalized = body[:-2] + _PREFIX_CASE_FIX[body[-2]] + body[-1]
        issues.append(LintIssue("fix", f"修正: {body} → {normalized}"))
        return normalized
    return body


def _format_mantissa(mantissa: Decimal) -> str:
    text = format(mantissa.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _select_prefix_and_format(actual: Decimal, unit: str) -> str:
    """把一个物理值（电阻用欧姆、电容用法拉、电感用亨利为底）格式化成带 SI 前缀的显示形式。"""
    for prefix in _PREFIX_ORDER:
        candidate = actual / (Decimal(10) ** _PREFIX_EXPONENT[prefix])
        if 1 <= candidate < 1000:
            return f"{_format_mantissa(candidate)}{prefix}{unit}"
    return f"{_format_mantissa(actual)}{unit}"


def _normalize_magnitude(
    mantissa: Decimal, exponent: int, unit: str, body: str, issues: list[LintIssue]
) -> tuple[str, Decimal, str]:
    if mantissa == 0:
        return body, mantissa, ""

    actual = mantissa * (Decimal(10) ** exponent)
    for prefix in _PREFIX_ORDER:
        candidate = actual / (Decimal(10) ** _PREFIX_EXPONENT[prefix])
        if 1 <= candidate < 1000:
            normalized = f"{_format_mantissa(candidate)}{prefix}{unit}"
            if normalized != body:
                issues.append(LintIssue("fix", f"修正: {body} → {normalized}"))
            return normalized, candidate, prefix
    return body, mantissa, ""


def _check_standard_value(mantissa: Decimal, prefix: str, unit: str, body: str) -> LintIssue | None:
    """校验数值是否落在该单位对应的标准容差序列上（电阻 E192∪E24，电容/电感封顶 E96∪E24）。"""
    standard_mantissas = _STANDARD_MANTISSAS_BY_UNIT[unit]

    decade = 0
    leading = mantissa
    while leading >= 10:
        leading /= 10
        decade += 1
    while leading < 1:
        leading *= 10
        decade -= 1

    scaled = leading * 100
    if scaled == scaled.to_integral_value() and int(scaled) in _STANDARD_SETS_BY_UNIT[unit]:
        return None

    nearest_mantissa, decade_shift = _nearest_standard(scaled, standard_mantissas)
    suggested_actual = (
        (Decimal(nearest_mantissa) / 100)
        * (Decimal(10) ** (decade + decade_shift))
        * (Decimal(10) ** _PREFIX_EXPONENT[prefix])
    )
    suggestion = _select_prefix_and_format(suggested_actual, unit)
    label = _UNIT_VALUE_LABEL[unit]
    return LintIssue(
        "warning",
        f"警告: {label}不是标准{label}（{body}），最接近的标准值：{suggestion}",
    )


def _nearest_standard(scaled: Decimal, standard_mantissas: tuple[int, ...]) -> tuple[int, int]:
    """在标准值表里找离 scaled 最近的一项，返回 (尾数, 十进制档偏移)。

    表只覆盖一个十进制档（尾数 1.00~9.88/9.76 之间），越靠近档位上沿，
    离下一档起点（下一档的 1.00，等价于本档的 10.00）反而可能更近，
    所以要把这个跨档候选也纳入比较，否则会把 9.99 推荐成本档最大值而不是 10。
    """
    pos = bisect_left(standard_mantissas, scaled)
    candidates: list[tuple[int, int]] = []
    if pos > 0:
        candidates.append((standard_mantissas[pos - 1], 0))
    if pos < len(standard_mantissas):
        candidates.append((standard_mantissas[pos], 0))
    candidates.append((standard_mantissas[0], 1))  # 下一档的最小值，本档尺度下等价于 ×10

    def distance(candidate: tuple[int, int]) -> Decimal:
        value, shift = candidate
        return abs(scaled - Decimal(value) * (Decimal(10) ** shift))

    return min(candidates, key=distance)
