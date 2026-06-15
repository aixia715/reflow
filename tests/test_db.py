from app.db import connect, init_db


def test_init_creates_all_tables():
    conn = connect(":memory:")
    init_db(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"boards_hierarchy", "initial_bom", "nodes", "node_changes", "edit_log"} <= names


def test_nodes_has_description_column():
    conn = connect(":memory:")
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    assert "description" in cols


def test_init_db_migrates_old_nodes_table():
    """老库（nodes 无 description 列）跑 init_db 后自动补列，旧数据 description 为空串。"""
    conn = connect(":memory:")
    # 模拟旧 schema：手建一个没有 description 列的 nodes 表 + 一行数据
    conn.execute(
        "CREATE TABLE nodes ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " board_id INTEGER NOT NULL, parent_id INTEGER,"
        " message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,"
        " is_committed INTEGER NOT NULL DEFAULT 0, committed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO nodes(board_id,message,created_at,is_committed) VALUES(1,'旧节点','t',1)"
    )
    conn.commit()
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    assert "description" in cols
    row = conn.execute("SELECT message, description FROM nodes").fetchone()
    assert row["message"] == "旧节点"
    assert row["description"] == ""


def test_row_factory_allows_name_access():
    conn = connect(":memory:")
    init_db(conn)
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v1','bomA','3')"
    )
    row = conn.execute("SELECT board_name FROM boards_hierarchy").fetchone()
    assert row["board_name"] == "B"
