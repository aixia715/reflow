from app import hard_change as hc


def test_split_ext_lowercases_and_strips_dot():
    assert hc.split_ext("Photo.JPG") == "jpg"
    assert hc.split_ext("noext") == ""


def test_make_stored_name_unique_with_ext():
    a = hc.make_stored_name("x.png")
    b = hc.make_stored_name("x.png")
    assert a != b and a.endswith(".png")


def test_validate_upload_rejects_empty_title():
    assert hc.validate_upload("  ", []) is not None


def test_validate_upload_rejects_bad_ext():
    assert hc.validate_upload("标题", [("a.svg", 100)]) is not None


def test_validate_upload_rejects_too_big():
    assert hc.validate_upload("标题", [("a.png", hc.MAX_IMAGE_BYTES + 1)]) is not None


def test_validate_upload_rejects_too_many():
    imgs = [("a.png", 10)] * (hc.MAX_IMAGES + 1)
    assert hc.validate_upload("标题", imgs) is not None


def test_validate_upload_ok():
    assert hc.validate_upload("标题", [("a.png", 10), ("b.jpg", 20)]) is None


def test_merge_timeline_orders_newest_first_draft_pinned_top():
    nodes = [
        {"id": 1, "is_committed": 1, "committed_at": "2026-01-01T00:00", "created_at": "x"},
        {"id": 2, "is_committed": 1, "committed_at": "2026-03-01T00:00", "created_at": "x"},
        {"id": 3, "is_committed": 0, "committed_at": None, "created_at": "2026-09-09T00:00"},
    ]
    hards = [{"id": 9, "occurred_at": "2026-02-01T00:00"}]
    out = hc.merge_timeline(nodes, hards)
    kinds = [(it["kind"], it["obj"]["id"]) for it in out]
    assert kinds == [("node", 3), ("node", 2), ("hard", 9), ("node", 1)]


def test_storage_save_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path / "up"))
    from app import storage
    storage.save_image("a.png", b"hello")
    p = tmp_path / "up" / "a.png"
    assert p.read_bytes() == b"hello"
    storage.delete_images(["a.png", "missing.png"])  # 缺文件不报错
    assert not p.exists()


def test_validate_content_types_rejects_non_image():
    assert hc.validate_content_types(["image/png", "text/html"]) is not None


def test_validate_content_types_ok():
    assert hc.validate_content_types(["image/png", "image/jpeg"]) is None


def test_validate_content_types_empty_ok():
    assert hc.validate_content_types([]) is None
