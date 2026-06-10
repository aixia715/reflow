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
