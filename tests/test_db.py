from app.db import connect, init_db


def test_init_creates_all_tables():
    conn = connect(":memory:")
    init_db(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"boards_hierarchy", "initial_bom", "nodes", "node_changes", "edit_log"} <= names


def test_row_factory_allows_name_access():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v1','bomA','3')"
    )
    row = conn.execute("SELECT board_name FROM boards_hierarchy").fetchone()
    assert row["board_name"] == "B"
