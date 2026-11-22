
from app.db import connect, init_db
from app.cli import bootstrap_admin_from_env, main


def _prepare(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    monkeypatch.setenv("REFLOW_DB", str(db))
    monkeypatch.delenv("REFLOW_BOOTSTRAP_USERNAME", raising=False)
    monkeypatch.delenv("REFLOW_BOOTSTRAP_PASSWORD", raising=False)
    return db


def test_cli_create_admin_claims_existing_board(tmp_path, monkeypatch):
    db = _prepare(tmp_path, monkeypatch)
    conn = connect(str(db))
    init_db(conn)
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v','bom','1')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "correct-horse")
    assert main(["create-admin", "--username", "Admin"]) == 0
    conn = connect(str(db))
    init_db(conn)
    user = conn.execute("SELECT username, is_admin FROM users").fetchone()
    assert user["username"] == "admin" and user["is_admin"] == 1
    member = conn.execute("SELECT role, user_id FROM board_members").fetchone()
    assert member["role"] == "owner"


def test_cli_password_mismatch_inserts_nothing(tmp_path, monkeypatch, capsys):
    db = _prepare(tmp_path, monkeypatch)
    answers = iter(["correct-horse", "other-horse-xx"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert main(["create-admin", "--username", "admin"]) == 2
    assert "不一致" in capsys.readouterr().err
    conn = connect(str(db))
    init_db(conn)
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0


def test_cli_create_admin_refuses_when_users_exist(tmp_path, monkeypatch, capsys):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "correct-horse")
    assert main(["create-admin", "--username", "admin"]) == 0
    assert main(["create-admin", "--username", "other"]) == 1
    assert "已经有用户" in capsys.readouterr().err


def test_cli_assign_orphans_fills_only_gaps(tmp_path, monkeypatch):
    """已有 owner 时，启动 backfill 不会把后插入的无主板领走；命令行只补空缺。"""
    db = _prepare(tmp_path, monkeypatch)
    conn = connect(str(db))
    init_db(conn)
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v','bom','early')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "correct-horse")
    assert main(["create-admin", "--username", "admin"]) == 0
    conn = connect(str(db))
    init_db(conn)
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v','bom','late')"
    )
    conn.commit()
    init_db(conn)
    late = conn.execute(
        "SELECT m.role FROM boards_hierarchy b"
        " LEFT JOIN board_members m ON m.board_id=b.id AND m.role='owner'"
        " WHERE b.board_uid='late'"
    ).fetchone()
    assert late["role"] is None
    conn.close()
    assert main(["assign-orphans", "--username", "admin"]) == 0
    conn = connect(str(db))
    init_db(conn)
    rows = conn.execute(
        "SELECT b.board_uid, u.username FROM board_members m"
        " JOIN users u ON u.id=m.user_id"
        " JOIN boards_hierarchy b ON b.id=m.board_id"
        " ORDER BY b.board_uid"
    ).fetchall()
    assert [(r["board_uid"], r["username"]) for r in rows] == [
        ("early", "admin"), ("late", "admin"),
    ]


def test_bootstrap_creates_admin_and_does_not_reset(tmp_path, monkeypatch):
    db = _prepare(tmp_path, monkeypatch)
    conn = connect(str(db))
    init_db(conn)
    conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES('B','v','bom','1')"
    )
    conn.commit()
    monkeypatch.setenv("REFLOW_BOOTSTRAP_USERNAME", "Admin")
    monkeypatch.setenv("REFLOW_BOOTSTRAP_PASSWORD", "password1")
    assert bootstrap_admin_from_env(conn) is True
    user = conn.execute("SELECT username, is_admin, password_hash FROM users").fetchone()
    assert user["username"] == "admin" and user["is_admin"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM board_members").fetchone()["n"] == 1
    assert bootstrap_admin_from_env(conn) is False
    again = conn.execute("SELECT password_hash FROM users").fetchone()["password_hash"]
    assert again == user["password_hash"]


def test_bootstrap_missing_password_creates_nothing(tmp_path, monkeypatch, caplog):
    db = _prepare(tmp_path, monkeypatch)
    conn = connect(str(db))
    init_db(conn)
    monkeypatch.setenv("REFLOW_BOOTSTRAP_USERNAME", "admin")
    monkeypatch.delenv("REFLOW_BOOTSTRAP_PASSWORD", raising=False)
    with caplog.at_level("WARNING"):
        assert bootstrap_admin_from_env(conn) is False
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
    assert "不完整" in caplog.text


def test_bootstrap_short_password(tmp_path, monkeypatch, caplog):
    db = _prepare(tmp_path, monkeypatch)
    conn = connect(str(db))
    init_db(conn)
    monkeypatch.setenv("REFLOW_BOOTSTRAP_USERNAME", "admin")
    monkeypatch.setenv("REFLOW_BOOTSTRAP_PASSWORD", "short")
    with caplog.at_level("WARNING"):
        assert bootstrap_admin_from_env(conn) is False
    assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
