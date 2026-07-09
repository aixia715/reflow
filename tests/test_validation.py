from app.validation import validate_edit

BOM = {"R1": "10k", "C1": "100nF"}


def test_modify_unknown_reference_rejected():
    assert "不存在" in validate_edit(BOM, "R9", "modify", "22k")


def test_add_existing_reference_rejected():
    assert "已存在" in validate_edit(BOM, "R1", "add", "22k")


def test_remove_unknown_reference_rejected():
    assert "不存在" in validate_edit(BOM, "R9", "remove", None)


def test_modify_requires_part():
    assert "Part" in validate_edit(BOM, "R1", "modify", "  ")


def test_add_requires_part():
    assert "Part" in validate_edit(BOM, "R9", "add", "")


def test_empty_reference_rejected():
    assert "位号" in validate_edit(BOM, "  ", "modify", "1k")


def test_unknown_op_rejected():
    assert "未知" in validate_edit(BOM, "R1", "frob", "1k")


def test_valid_modify_passes():
    assert validate_edit(BOM, "R1", "modify", "22k") is None


def test_valid_add_passes():
    assert validate_edit(BOM, "R9", "add", "1k") is None


def test_valid_remove_passes():
    assert validate_edit(BOM, "C1", "remove", None) is None


def test_part_with_surrounding_whitespace_passes():
    # 契约：part 只判断非空，不在此裁剪；存储前的清理由调用方负责
    assert validate_edit(BOM, "R9", "add", "  10k  ") is None


def test_remove_ignores_spurious_part():
    # 契约：remove 不要求 part，传了多余值也合法（路由层会忽略）
    assert validate_edit(BOM, "C1", "remove", "spurious") is None


from app.validation import validate_insert_time

PREV = "2026-06-01T00:00:00+00:00"
NEXT = "2026-06-10T00:00:00+00:00"


def test_insert_time_between_passes():
    assert validate_insert_time(PREV, NEXT, "2026-06-05T00:00:00+00:00") is None


def test_insert_time_equal_prev_rejected():
    # 开区间：等于上一节点时间不允许
    assert "上一个" in validate_insert_time(PREV, NEXT, PREV)


def test_insert_time_equal_next_rejected():
    assert "下一个" in validate_insert_time(PREV, NEXT, NEXT)


def test_insert_time_before_prev_rejected():
    assert "上一个" in validate_insert_time(PREV, NEXT, "2026-05-30T00:00:00+00:00")


def test_insert_time_after_next_rejected():
    assert "下一个" in validate_insert_time(PREV, NEXT, "2026-06-20T00:00:00+00:00")


def test_insert_time_empty_rejected():
    assert "不能为空" in validate_insert_time(PREV, NEXT, "")


def test_insert_time_invalid_format_rejected():
    assert "格式" in validate_insert_time(PREV, NEXT, "not-a-time")


def test_insert_time_naive_chosen_treated_as_utc():
    # 无时区的 chosen（直接构造请求时可能出现）不应抛异常，按 UTC 处理
    assert validate_insert_time(PREV, NEXT, "2026-06-05T00:00:00") is None
    assert "上一个" in validate_insert_time(PREV, NEXT, "2026-05-01T00:00:00")


def test_insert_time_different_offset_compared_correctly():
    # 选择值带 +08:00 偏移，应按时刻比较而非字符串字面量
    # 2026-06-05T08:00:00+08:00 == 2026-06-05T00:00:00Z，落在区间内
    assert validate_insert_time(PREV, NEXT, "2026-06-05T08:00:00+08:00") is None


from app.validation import validate_new_name


def test_new_name_empty_rejected():
    assert "不能为空" in validate_new_name("")


def test_new_name_whitespace_rejected():
    assert "不能为空" in validate_new_name("   ")


def test_new_name_none_rejected():
    assert "不能为空" in validate_new_name(None)


def test_valid_new_name_passes():
    assert validate_new_name("v2") is None


def test_new_name_not_stripped_in_return():
    # 契约：只判断 trim 后非空，不负责裁剪；裁剪由调用方（路由）负责
    assert validate_new_name("  v2  ") is None


# ── changes 载荷校验（issue #110：手搓的畸形 payload 不该打成 500）────────

import json
import pytest
from app.validation import validate_changes_payload


def test_changes_payload_accepts_well_formed():
    payload = [{"reference": "R1", "op": "modify", "part": "47k"},
               {"reference": "C1", "op": "remove", "part": None}]
    got, err = validate_changes_payload(json.dumps(payload))
    assert err is None
    assert got == payload


def test_changes_payload_accepts_empty_array():
    """空数组不是格式错误——「空修改」的文案各路由不同，由调用方判断。"""
    assert validate_changes_payload("[]") == ([], None)


def test_changes_payload_allows_omitted_op_and_part():
    """op / part 允许缺省或为 None：CSV 里 OP 列留空即表示交给 op 推断。"""
    got, err = validate_changes_payload('[{"reference":"R1"}]')
    assert err is None and got == [{"reference": "R1"}]


@pytest.mark.parametrize("bad", [
    "not json",                                      # 语法错误
    "42",                                            # 顶层是标量
    '{"a":1}',                                       # 顶层是对象
    '["R1"]',                                        # 元素不是对象
    "[123]",                                         # 元素是数字
    '[{"op":"add","part":"1k"}]',                    # 缺 reference
    '[{"reference":123,"op":"add","part":"1k"}]',    # reference 不是字符串
    '[{"reference":"R9","op":"add","part":123}]',    # part 不是字符串
    '[{"reference":"R9","op":"add","part":true}]',   # part 是布尔真值
    '[{"reference":"R9","op":123,"part":"1k"}]',     # op 不是字符串
])
def test_changes_payload_rejects_malformed(bad):
    got, err = validate_changes_payload(bad)
    assert got == []
    assert err == "数据格式不正确"


def test_changes_payload_rejects_deeply_nested_json():
    """RecursionError 是 RuntimeError 的子类，不被 ValueError/TypeError 捕获。"""
    got, err = validate_changes_payload("[" * 10000 + "]" * 10000)
    assert got == []
    assert err == "数据格式不正确"


def test_changes_payload_rejects_duplicate_reference_after_strip():
    got, err = validate_changes_payload(
        '[{"reference":"R9","op":"add","part":"1u"},'
        ' {"reference":" R9 ","op":"add","part":"2u"}]')
    assert got == []
    assert err == "位号重复"
