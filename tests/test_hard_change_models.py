import pytest
from app.db import connect, init_db
from app import models


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def test_hard_change_tables_exist(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hard_changes" in names
    assert "hard_change_images" in names


def _mk_board(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    return models.create_board(conn, "B", "v1", "bomA", "SN1")


def test_create_and_get_hard_change_with_images(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(
        conn, bid, "飞线 R1-R2", "说明", "2026-06-01T10:30",
        [("aaa.png", "原图1.png"), ("bbb.jpg", "原图2.jpg")])
    hc = models.get_hard_change(conn, hc_id)
    assert hc["title"] == "飞线 R1-R2" and hc["board_id"] == bid
    imgs = models.list_hard_change_images(conn, hc_id)
    assert [i["filename"] for i in imgs] == ["aaa.png", "bbb.jpg"]
    assert [i["sort_order"] for i in imgs] == [0, 1]


def test_list_hard_changes_by_board(conn):
    bid = _mk_board(conn)
    models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00", [])
    models.create_hard_change(conn, bid, "B", "", "2026-02-01T00:00", [])
    assert [h["title"] for h in models.list_hard_changes(conn, bid)] == ["A", "B"]


def test_update_hard_change(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(conn, bid, "旧", "x", "2026-01-01T00:00", [])
    models.update_hard_change(conn, hc_id, "新", "y", "2026-05-05T05:05")
    hc = models.get_hard_change(conn, hc_id)
    assert (hc["title"], hc["description"], hc["occurred_at"]) == ("新", "y", "2026-05-05T05:05")


def test_add_and_delete_hard_change_images(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00",
                                      [("a.png", "a")])
    models.add_hard_change_images(conn, hc_id, [("b.png", "b")])
    imgs = models.list_hard_change_images(conn, hc_id)
    assert [i["sort_order"] for i in imgs] == [0, 1]
    fns = models.delete_hard_change_images(conn, [imgs[0]["id"]])
    assert fns == ["a.png"]
    assert [i["filename"] for i in models.list_hard_change_images(conn, hc_id)] == ["b.png"]


def test_delete_hard_change_returns_filenames(conn):
    bid = _mk_board(conn)
    hc_id = models.create_hard_change(conn, bid, "A", "", "2026-01-01T00:00",
                                      [("a.png", "a"), ("b.png", "b")])
    fns = models.delete_hard_change(conn, hc_id)
    assert sorted(fns) == ["a.png", "b.png"]
    assert models.get_hard_change(conn, hc_id) is None
    assert models.list_hard_change_images(conn, hc_id) == []
