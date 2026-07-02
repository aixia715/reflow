import pytest
from app.db import connect, init_db
from app import models


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def _mk_board(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    return models.create_board(conn, "B", "v1", "bomA", "SN1")


def test_node_attachments_table_exists(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "node_attachments" in names


def test_add_list_get_attachment(conn):
    bid = _mk_board(conn)
    ws = models.workspace_node(conn, bid)
    aid = models.add_node_attachment(conn, ws["id"], "原理图.sch", "7/12/abc.sch")
    assert aid > 0
    rows = models.list_node_attachments(conn, ws["id"])
    assert len(rows) == 1
    assert rows[0]["filename"] == "原理图.sch"
    assert rows[0]["storage_path"] == "7/12/abc.sch"
    got = models.get_node_attachment(conn, aid)
    assert got["node_id"] == ws["id"]


def test_delete_attachment_returns_storage_path(conn):
    bid = _mk_board(conn)
    ws = models.workspace_node(conn, bid)
    aid = models.add_node_attachment(conn, ws["id"], "a.sch", "1/2/a.sch")
    path = models.delete_node_attachment(conn, aid)
    assert path == "1/2/a.sch"
    assert models.list_node_attachments(conn, ws["id"]) == []
    # 二次删除返回 None
    assert models.delete_node_attachment(conn, aid) is None


def test_delete_node_cleans_attachments(conn):
    bid = _mk_board(conn)
    ws = models.workspace_node(conn, bid)
    nid = models.commit_workspace(conn, bid, "第一次")
    models.add_node_attachment(conn, nid, "a.sch", "x/y/a.sch")
    paths = models.delete_node(conn, nid)
    assert paths == ["x/y/a.sch"]
    assert conn.execute("SELECT COUNT(*) FROM node_attachments").fetchone()[0] == 0


def test_board_attachment_paths_collects_all(conn):
    bid = _mk_board(conn)
    ws = models.workspace_node(conn, bid)
    nid = models.commit_workspace(conn, bid, "第一次")
    models.add_node_attachment(conn, ws["id"], "a.sch", "x/y/a.sch")
    models.add_node_attachment(conn, nid, "b.pdf", "x/z/b.pdf")
    paths = models.board_attachment_paths(conn, bid)
    assert sorted(paths) == ["x/y/a.sch", "x/z/b.pdf"]


def test_delete_board_cleans_attachment_rows(conn):
    bid = _mk_board(conn)
    ws = models.workspace_node(conn, bid)
    models.add_node_attachment(conn, ws["id"], "a.sch", "1/2/a.sch")
    models.delete_board(conn, bid)
    assert conn.execute("SELECT COUNT(*) FROM node_attachments").fetchone()[0] == 0


def test_board_attachment_paths_by_name_collects_across_versions(conn):
    from app.csv_import import CsvEntry
    bid1 = _mk_board(conn)  # board_name="B", pcb="v1", bom="bomA"
    models.create_bom_version(conn, "B", "v2", "bomA", [CsvEntry("R1", "10k")])
    bid2 = models.create_board(conn, "B", "v2", "bomA", "SN2")
    ws1 = models.workspace_node(conn, bid1)
    ws2 = models.workspace_node(conn, bid2)
    models.add_node_attachment(conn, ws1["id"], "a.sch", "x/1/a.sch")
    models.add_node_attachment(conn, ws2["id"], "b.sch", "x/2/b.sch")
    paths = models.board_attachment_paths_by_name(conn, "B")
    assert sorted(paths) == ["x/1/a.sch", "x/2/b.sch"]
    # 不同单板名称的附件不应被收进来
    assert models.board_attachment_paths_by_name(conn, "其它单板") == []