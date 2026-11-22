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

CREATE TABLE IF NOT EXISTS node_attachments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id      INTEGER NOT NULL REFERENCES nodes(id),
    filename     TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,          -- 小写后的登录名
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,    -- 1 = 系统管理员
    disabled_at   TEXT,                          -- NULL = 可登录
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    token_hash   TEXT NOT NULL UNIQUE,           -- sha256(cookie 原文)
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_members (
    board_id   INTEGER NOT NULL REFERENCES boards_hierarchy(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    role       TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (board_id, user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS board_one_owner
    ON board_members(board_id) WHERE role = 'owner';

CREATE TABLE IF NOT EXISTS bom_versions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    board_name    TEXT NOT NULL,
    pcb_version   TEXT NOT NULL,
    bom_version   TEXT NOT NULL,
    owner_user_id INTEGER NOT NULL REFERENCES users(id),
    revision      INTEGER NOT NULL DEFAULT 0,
    revised_by    INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL,
    UNIQUE(board_name, pcb_version, bom_version)
);
"""


def connect(path: str = "reflow.sqlite") -> sqlite3.Connection:
    # timeout=5 即 busy timeout：别人持有写锁时最多等 5 秒，而不是立刻 database is locked
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 内存库不支持 WAL，SQLite 会保持 memory，这里不因此失败
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection, *, assign_version_owners: bool = True) -> None:
    """建表并做幂等迁移。

    assign_version_owners 为假时不把尚无负责人的 BOM 版本归给最早的管理员，
    留给 `assign-orphans` 按命令行指定的人来补。
    """
    conn.executescript(SCHEMA)
    _migrate(conn)
    # 延迟导入：models 不依赖 db，但 db 在迁移末尾要认领无主数据
    from app.models import backfill_bom_version_owners, backfill_owners_if_unassigned
    backfill_owners_if_unassigned(conn)
    if assign_version_owners:
        backfill_bom_version_owners(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """对已有库做幂等增量迁移。不重建 edit_log，旧行原样保留。"""
    # nodes.description：老库无此列时补上（CREATE TABLE IF NOT EXISTS 不会改已存在的表）
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    if "description" not in cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    # 老库没有这三张表时补上。与 SCHEMA 同一份语句，重复执行无副作用。
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            disabled_at   TEXT,
            created_at    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id),
            token_hash   TEXT NOT NULL UNIQUE,
            created_at   TEXT NOT NULL,
            expires_at   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS board_members (
            board_id   INTEGER NOT NULL REFERENCES boards_hierarchy(id) ON DELETE CASCADE,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            role       TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (board_id, user_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS board_one_owner
            ON board_members(board_id) WHERE role = 'owner';
        CREATE TABLE IF NOT EXISTS bom_versions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            board_name    TEXT NOT NULL,
            pcb_version   TEXT NOT NULL,
            bom_version   TEXT NOT NULL,
            owner_user_id INTEGER NOT NULL REFERENCES users(id),
            revision      INTEGER NOT NULL DEFAULT 0,
            revised_by    INTEGER REFERENCES users(id),
            created_at    TEXT NOT NULL,
            UNIQUE(board_name, pcb_version, bom_version)
        );
        """
    )
