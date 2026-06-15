import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards_hierarchy (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    board_name  TEXT NOT NULL,
    pcb_version TEXT NOT NULL,
    bom_version TEXT NOT NULL,
    board_uid   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS initial_bom (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    board_name  TEXT NOT NULL,
    pcb_version TEXT NOT NULL,
    bom_version TEXT NOT NULL,
    reference   TEXT NOT NULL,
    part        TEXT NOT NULL,
    UNIQUE(board_name, pcb_version, bom_version, reference)
);

CREATE TABLE IF NOT EXISTS nodes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id     INTEGER NOT NULL REFERENCES boards_hierarchy(id),
    parent_id    INTEGER REFERENCES nodes(id),
    message      TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    is_committed INTEGER NOT NULL DEFAULT 0,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS node_changes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id   INTEGER NOT NULL REFERENCES nodes(id),
    reference TEXT NOT NULL,
    op        TEXT NOT NULL CHECK(op IN ('add','modify','remove')),
    part      TEXT,
    UNIQUE(node_id, reference)
);

CREATE TABLE IF NOT EXISTS edit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id    INTEGER NOT NULL REFERENCES nodes(id),
    reference  TEXT NOT NULL,
    old_part   TEXT,
    new_part   TEXT,
    op         TEXT NOT NULL,
    source     TEXT NOT NULL CHECK(source IN ('direct','propagated')),
    created_at TEXT NOT NULL,
    note       TEXT
);

CREATE TABLE IF NOT EXISTS hard_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    board_id    INTEGER NOT NULL REFERENCES boards_hierarchy(id),
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hard_change_images (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    hard_change_id INTEGER NOT NULL REFERENCES hard_changes(id),
    filename       TEXT NOT NULL,
    original_name  TEXT,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
"""


def connect(path: str = "reflow.sqlite") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """对已有库做幂等增量迁移（无版本表，单人 MVP 够用）。"""
    # nodes.description：老库无此列时补上（CREATE TABLE IF NOT EXISTS 不会改已存在的表）
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    if "description" not in cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN description TEXT NOT NULL DEFAULT ''")
