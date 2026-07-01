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


def test_rel_path_joins_board_node_name():
    p = attachments.rel_path(7, 12, "abc.sch")
    assert p == os.path.join("7", "12", "abc.sch")
    assert p == "7/12/abc.sch"


def test_safe_download_filename_strips_path_separators():
    assert attachments.safe_filename("a/b\\c?.sch") == "abc.sch"
    assert attachments.safe_filename("  示例.sch ") == "示例.sch"
    assert attachments.safe_filename("") == "附件"