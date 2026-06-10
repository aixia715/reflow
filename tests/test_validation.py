from app.validation import validate_edit

BOM = {"R1": "10k", "C1": "100nF"}


def test_modify_unknown_reference_rejected():
    assert "不存在" in validate_edit(BOM, "R9", "modify", "22k")


def test_add_existing_reference_rejected():
    assert "已存在" in validate_edit(BOM, "R1", "add", "22k")


def test_remove_unknown_reference_rejected():
    assert "不存在" in validate_edit(BOM, "R9", "remove", None)


def test_modify_requires_part():
    assert validate_edit(BOM, "R1", "modify", "  ") is not None


def test_add_requires_part():
    assert validate_edit(BOM, "R9", "add", "") is not None


def test_empty_reference_rejected():
    assert validate_edit(BOM, "  ", "modify", "1k") is not None


def test_unknown_op_rejected():
    assert validate_edit(BOM, "R1", "frob", "1k") is not None


def test_valid_modify_passes():
    assert validate_edit(BOM, "R1", "modify", "22k") is None


def test_valid_add_passes():
    assert validate_edit(BOM, "R9", "add", "1k") is None


def test_valid_remove_passes():
    assert validate_edit(BOM, "C1", "remove", None) is None
