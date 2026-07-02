import os
from app import attachments


def test_make_stored_name_unique_with_ext():
    a = attachments.make_stored_name("原理图.sch")
    b = attachments.make_stored_name("原理图.sch")
    assert a != b
    assert a.endswith(".sch")


def test_make_stored_name_no_ext():
    name = attachments.make_stored_name("README")
    assert "." not in name
    assert len(name) == 32   # uuid4.hex


def test_make_stored_name_empty_filename():
    name = attachments.make_stored_name("")
    assert "." not in name


def test_make_stored_name_trailing_dot_treated_as_no_ext():
    """文件名以裸点结尾（如「notes.」）时 os.path.splitext 返回的 ext=='.' 是真值，
    早期实现直接判断 `if ext` 会误入「有扩展名」分支，拼出末尾多一个点的畸形存盘名。"""
    name = attachments.make_stored_name("notes.")
    assert "." not in name
    assert len(name) == 32


def test_make_stored_name_lowercases_extension():
    a = attachments.make_stored_name("原理图.SCH")
    assert a.endswith(".sch")


def test_rel_path_joins_board_node_name():
    p = attachments.rel_path(7, 12, "abc.sch")
    assert p == os.path.join("7", "12", "abc.sch")
    assert p == "7/12/abc.sch"


def test_safe_download_filename_strips_path_separators():
    assert attachments.safe_filename("a/b\\c?.sch") == "abc.sch"
    assert attachments.safe_filename("  示例.sch ") == "示例.sch"
    assert attachments.safe_filename("") == "附件"