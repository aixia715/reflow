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

_STANDARD_MANTISSAS = tuple(sorted(set(_E192_MANTISSAS) | set(_E24_MANTISSAS)))
_STANDARD_SET = frozenset(_STANDARD_MANTISSAS)


class LintIssue(NamedTuple):
    level: str  # "fix" | "warning"
    message: str


def lint_part(raw_part: str) -> tuple[str, list[LintIssue]]:
    """校验并标准化一个元器件值，返回 (标准化结果, 问题列表)。

    型号类值（不以数字开头）只做首尾空白清理；规格值（数字开头）执行完整的
    单位/前缀标准化与量级归一。任意一条规则产生 warning 后立即停止后续规则。
    """
    issues: list[LintIssue] = []

    stripped = raw_part.strip()
    if stripped != raw_part:
        issues.append(LintIssue("fix", f"修正: 去除前后空白 → {stripped}"))

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

    if unit == "R" and mantissa != 0:
        warning = _check_standard_resistance(mantissa, prefix, body)
        if warning is not None:
            issues.append(warning)

    return body, issues


def _normalize_internal_space(body: str, issues: list[LintIssue]) -> str:
    normalized = re.sub(r"(\d)\s+(?=[a-zA-ZΩ])", r"\1", body)
    if normalized != body:
        issues.append(LintIssue("fix", f"修正: {body} → {normalized}"))
    return normalized


def _normalize_unit_alias(body: str, issues: list[LintIssue]) -> str:
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


def _check_standard_resistance(mantissa: Decimal, prefix: str, body: str) -> LintIssue | None:
    """校验阻值是否落在 E6/E12/E24/E48/E96/E192 任一标准容差序列上。"""
    decade = 0
    leading = mantissa
    while leading >= 10:
        leading /= 10
        decade += 1
    while leading < 1:
        leading *= 10
        decade -= 1

    scaled = leading * 100
    if scaled == scaled.to_integral_value() and int(scaled) in _STANDARD_SET:
        return None

    nearest = _nearest_standard(scaled)
    suggestion = _format_mantissa((Decimal(nearest) / 100) * (Decimal(10) ** decade))
    return LintIssue(
        "warning",
        f"警告: 阻值不是标准阻值（{body}），最接近的标准值：{suggestion}{prefix}R",
    )


def _nearest_standard(scaled: Decimal) -> int:
    pos = bisect_left(_STANDARD_MANTISSAS, scaled)
    if pos == 0:
        return _STANDARD_MANTISSAS[0]
    if pos == len(_STANDARD_MANTISSAS):
        return _STANDARD_MANTISSAS[-1]
    before, after = _STANDARD_MANTISSAS[pos - 1], _STANDARD_MANTISSAS[pos]
    return before if (scaled - before) <= (after - scaled) else after
