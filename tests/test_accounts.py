import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.db import connect, init_db
from app.models import (
    assign_boards_without_owner,
    backfill_owners_if_unassigned,
    create_board,
    create_session,
    create_user,
    delete_board,
    user_by_session_token,
)


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def _board(conn, uid="1"):
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v','bom',?)",
        (uid,),
    )
    conn.commit()


def test_auth_tables_exist(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "sessions", "board_members", "bom_versions"} <= names


def test_only_one_owner_per_board(conn):
    conn.execute(
        "INSERT INTO users(username,password_hash,is_admin,created_at)"
        " VALUES('a','x',1,'t'),('b','x',0,'t')")
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v','bom','1')")
    conn.execute(
        "INSERT INTO board_members(board_id,user_id,role,created_at) VALUES(1,1,'owner','t')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO board_members(board_id,user_id,role,created_at) VALUES(1,2,'owner','t')")


def test_connect_enables_wal(tmp_path):
    path = tmp_path / "t.sqlite"
    c = connect(str(path))
    init_db(c)
    mode = c.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_migrate_old_db_keeps_rows(tmp_path):
    """老库没有用户表、nodes 也没有 description。升级后旧行还在，新表补上，再跑一次不丢。"""
    path = tmp_path / "old.sqlite"
    c = connect(str(path))
    c.executescript(
        """
        CREATE TABLE boards_hierarchy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_name TEXT NOT NULL,
            pcb_version TEXT NOT NULL,
            bom_version TEXT NOT NULL,
            board_uid TEXT NOT NULL
        );
        CREATE TABLE initial_bom (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_name TEXT NOT NULL,
            pcb_version TEXT NOT NULL,
            bom_version TEXT NOT NULL,
            reference TEXT NOT NULL,
            part TEXT NOT NULL
        );
        CREATE TABLE nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            board_id INTEGER NOT NULL,
            parent_id INTEGER,
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            is_committed INTEGER NOT NULL DEFAULT 0,
            committed_at TEXT
        );
        """
    )
    c.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('Old','v','b','1')"
    )
    c.execute(
        "INSERT INTO initial_bom(board_name,pcb_version,bom_version,reference,part)"
        " VALUES('Old','v','b','R1','10k')"
    )
    c.execute(
        "INSERT INTO nodes(board_id,message,created_at,is_committed) VALUES(1,'旧节点','t',1)"
    )
    c.commit()
    init_db(c)
    assert c.execute("SELECT board_uid FROM boards_hierarchy").fetchone()["board_uid"] == "1"
    assert c.execute("SELECT part FROM initial_bom").fetchone()["part"] == "10k"
    node = c.execute("SELECT message, description FROM nodes").fetchone()
    assert node["message"] == "旧节点"
    assert node["description"] == ""
    names = {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "sessions", "board_members", "bom_versions"} <= names
    init_db(c)
    assert c.execute("SELECT COUNT(*) AS n FROM boards_hierarchy").fetchone()["n"] == 1
    assert c.execute("SELECT COUNT(*) AS n FROM initial_bom").fetchone()["n"] == 1
    assert c.execute("SELECT message FROM nodes").fetchone()["message"] == "旧节点"


def test_first_admin_claims_boards_second_does_not(conn):
    _board(conn, "1")
    _board(conn, "2")
    first = create_user(conn, "Admin", "password1", is_admin=True)
    rows = conn.execute(
        "SELECT board_id, user_id, role FROM board_members ORDER BY board_id"
    ).fetchall()
    assert [(r["user_id"], r["role"]) for r in rows] == [(first, "owner"), (first, "owner")]
    create_user(conn, "Other", "password2", is_admin=True)
    owners = {r["user_id"] for r in conn.execute("SELECT user_id FROM board_members")}
    assert owners == {first}


def test_first_user_must_be_admin(conn):
    with pytest.raises(ValueError, match="第一个账号必须是系统管理员"):
        create_user(conn, "bob", "password1", is_admin=False)


def test_password_must_be_8(conn):
    with pytest.raises(ValueError, match="密码至少 8 位"):
        create_user(conn, "admin", "short", is_admin=True)


def test_session_expiry_and_disabled(conn):
    uid = create_user(conn, "Admin", "password1", is_admin=True)
    token = create_session(conn, uid, days=14)
    assert user_by_session_token(conn, token)["username"] == "admin"
    digest = hashlib.sha256(token.encode()).hexdigest()
    conn.execute(
        "UPDATE sessions SET expires_at=? WHERE token_hash=?",
        ("2000-01-01T00:00:00+00:00", digest),
    )
    conn.commit()
    assert user_by_session_token(conn, token) is None

    token2 = create_session(conn, uid, days=14)
    conn.execute(
        "UPDATE users SET disabled_at=? WHERE id=?",
        ("2026-01-01T00:00:00+00:00", uid),
    )
    conn.commit()
    assert user_by_session_token(conn, token2) is None
    # 停用只是让令牌查不回来；会话行还在，真正删除留给停用接口
    assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 2


def test_session_slides_when_half_elapsed(conn):
    uid = create_user(conn, "admin", "password1", is_admin=True)
    token = create_session(conn, uid, days=14)
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute(
        "UPDATE sessions SET expires_at=? WHERE token_hash=?",
        (soon, hashlib.sha256(token.encode()).hexdigest()),
    )
    conn.commit()
    assert user_by_session_token(conn, token) is not None
    exp = conn.execute("SELECT expires_at FROM sessions").fetchone()["expires_at"]
    assert exp > soon


def test_backfill_noop_when_any_owner_exists(conn):
    uid = create_user(conn, "admin", "password1", is_admin=True)
    create_board(conn, "B", "v", "bom", "1", owner_user_id=uid)
    _board(conn, "2")
    assert backfill_owners_if_unassigned(conn) == 0
    orphan = conn.execute(
        "SELECT role FROM board_members WHERE board_id="
        "(SELECT id FROM boards_hierarchy WHERE board_uid='2')"
    ).fetchone()
    assert orphan is None
    claimed = assign_boards_without_owner(conn, uid)
    assert claimed == 1


def test_first_admin_owns_existing_bom_version_and_backfill_is_idempotent(conn):
    conn.execute(
        "INSERT INTO initial_bom(board_name,pcb_version,bom_version,reference,part)"
        " VALUES('B','v','bom','R1','10k')"
    )
    conn.commit()
    uid = create_user(conn, "Admin", "password1", is_admin=True)
    row = conn.execute(
        "SELECT owner_user_id FROM bom_versions WHERE board_name='B'"
    ).fetchone()
    assert row["owner_user_id"] == uid
    init_db(conn)
    again = conn.execute(
        "SELECT owner_user_id FROM bom_versions WHERE board_name='B'"
    ).fetchone()
    assert again["owner_user_id"] == uid
    assert conn.execute("SELECT COUNT(*) AS n FROM bom_versions").fetchone()["n"] == 1


def test_ensure_bom_version_does_not_replace_owner(conn):
    from app.models import ensure_bom_version
    uid = create_user(conn, "admin", "password1", is_admin=True)
    other = create_user(conn, "other", "password2", is_admin=True)
    first = ensure_bom_version(conn, "B", "v", "bom", uid)
    conn.commit()
    second = ensure_bom_version(conn, "B", "v", "bom", other)
    conn.commit()
    assert first == second
    owner = conn.execute(
        "SELECT owner_user_id FROM bom_versions WHERE id=?", (first,)
    ).fetchone()["owner_user_id"]
    assert owner == uid


def test_create_board_writes_owner_and_delete_cascades(conn):
    uid = create_user(conn, "admin", "password1", is_admin=True)
    bid = create_board(conn, "B", "v", "bom", "9", owner_user_id=uid)
    row = conn.execute(
        "SELECT user_id, role FROM board_members WHERE board_id=?", (bid,)
    ).fetchone()
    assert row["user_id"] == uid and row["role"] == "owner"
    delete_board(conn, bid)
    assert conn.execute("SELECT COUNT(*) AS n FROM board_members").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM boards_hierarchy").fetchone()["n"] == 0
