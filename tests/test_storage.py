import os
from app import storage


def test_delete_files_missing_file_no_error(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path))
    storage.delete_files(["not/here.sch"])  # 不抛异常


def test_delete_files_other_oserror_does_not_abort_remaining(tmp_path, monkeypatch):
    """一个文件删除失败（如目录而非文件导致 IsADirectoryError）不应中断其余文件的删除，
    也不应把异常抛给调用方——DB 侧才是数据来源，磁盘清理只是尽力而为。"""
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path))
    bad_dir = tmp_path / "a_dir"
    bad_dir.mkdir()
    good_file = tmp_path / "b.sch"
    good_file.write_bytes(b"x")
    storage.delete_files(["a_dir", "b.sch"])  # os.remove 对目录抛 IsADirectoryError
    assert bad_dir.exists()          # 目录删不掉，但不影响别的
    assert not good_file.exists()    # 正常文件仍被删除
