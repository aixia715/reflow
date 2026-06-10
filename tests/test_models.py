import pytest
from app.db import connect, init_db
from app import models


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def test_create_bom_version_with_initial_entries(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(
        conn, "MainBoard", "v1", "bomA",
        [CsvEntry("R1", "10k"), CsvEntry("C9", "100nF")],
    )
    bom = models.get_initial_bom(conn, "MainBoard", "v1", "bomA")
    assert bom == {"R1": "10k", "C9": "100nF"}


def test_create_board_makes_root_and_empty_workspace(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    board_id = models.create_board(conn, "B", "v1", "bomA", "3")
    nodes = models.list_nodes(conn, board_id)
    assert len(nodes) == 2
    assert nodes[0]["parent_id"] is None
    assert nodes[0]["is_committed"] == 1
    assert nodes[1]["is_committed"] == 0
    assert nodes[1]["parent_id"] == nodes[0]["id"]


def test_board_uid_exists(conn):
    models.create_board(conn, "B", "v1", "bomA", "3")
    assert models.board_uid_exists(conn, "B", "v1", "bomA", "3") is True
    assert models.board_uid_exists(conn, "B", "v1", "bomA", "4") is False
    # 不同 BOM 版本下同名 ID 不算冲突
    assert models.board_uid_exists(conn, "B", "v1", "bomB", "3") is False


def test_changeset_upsert_and_chain_for_node(conn):
    from app.csv_import import CsvEntry
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    board_id = models.create_board(conn, "B", "v1", "bomA", "3")
    root_id = models.list_nodes(conn, board_id)[0]["id"]
    ws_id = models.list_nodes(conn, board_id)[1]["id"]

    models.set_change(conn, ws_id, "R1", "modify", "47k")
    models.set_change(conn, ws_id, "R1", "modify", "22k")  # upsert 覆盖
    cs = models.get_changeset(conn, ws_id)
    assert cs == [{"reference": "R1", "op": "modify", "part": "22k"}]

    initial, chain = models.get_chain(conn, ws_id)
    assert initial == {"R1": "10k"}
    assert chain == [[], cs]

    models.delete_change(conn, ws_id, "R1")
    assert models.get_changeset(conn, ws_id) == []


def test_list_board_log_filters_and_orders(tmp_path):
    from app.db import connect, init_db
    from app.csv_import import CsvEntry
    from app import models, audit
    conn = connect(str(tmp_path / "t.sqlite")); init_db(conn)
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(conn, "B", "v1", "bomA", "1")
    ws = models.workspace_node(conn, bid)
    audit.record_edit(conn, ws["id"], "R1", "10k", "22k", "modify", "direct")
    audit.record_edit(conn, ws["id"], "C1", None, "1uF", "add", "direct")
    rows = models.list_board_log(conn, bid)
    assert [r["reference"] for r in rows] == ["C1", "R1"]          # 倒序
    assert rows[0]["node_message"] is not None                      # join 到节点
    only_r1 = models.list_board_log(conn, bid, reference="R1")
    assert [r["reference"] for r in only_r1] == ["R1"]
    only_node = models.list_board_log(conn, bid, node_id=ws["id"])
    assert len(only_node) == 2


def test_node_summaries(tmp_path):
    from app.db import connect, init_db
    from app.csv_import import CsvEntry
    from app import models
    conn = connect(str(tmp_path / "t.sqlite")); init_db(conn)
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(conn, "B", "v1", "bomA", "1")
    ws = models.workspace_node(conn, bid)
    models.set_change(conn, ws["id"], "R1", "modify", "22k")
    s = models.node_summaries(conn, bid)
    root = models.list_nodes(conn, bid)[0]
    assert s[root["id"]] == []
    assert s[ws["id"]] == [{"reference": "R1", "op": "modify"}]
