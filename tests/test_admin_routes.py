"""网页用户管理：仅系统管理员可创建、停用、重置密码，并移交所有者已停用的板。"""
import json
import logging
import os

from app.csv_import import CsvEntry
from app.db import connect
from tests.helpers import AuthTestClient, auth_client


def _conn():
    return connect(os.environ["REFLOW_DB"])


def _user_id(username: str) -> int:
    conn = _conn()
    try:
        return conn.execute(
            "SELECT id FROM users WHERE username=?", (username,)
        ).fetchone()["id"]
    finally:
        conn.close()


def _toast(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["showToast"]


def test_non_admin_cannot_open_user_admin(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    plain = auth_client(
        tmp_path, monkeypatch, username="plain", password="test-password", is_admin=False,
    )
    assert "用户管理" in admin.get("/").text
    assert "用户管理" not in plain.get("/").text
    denied = plain.get("/admin/users")
    assert denied.status_code == 403
    posted = plain.post("/admin/users", data={
        "username": "sneaky", "password": "password-1", "password2": "password-1",
    })
    assert posted.status_code == 403
    conn = _conn()
    try:
        assert conn.execute(
            "SELECT 1 FROM users WHERE username='sneaky'"
        ).fetchone() is None
    finally:
        conn.close()


def test_admin_creates_user_who_can_log_in(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    created = admin.post("/admin/users", data={
        "username": "HuaQing",
        "password": "password-1",
        "password2": "password-1",
    })
    assert created.status_code == 200, created.text
    assert "huaqing" in created.text
    assert "password-1" not in created.text
    assert "✓" in _toast(created)
    # 大小写折叠后的账号能登录，且不是管理员。
    person = auth_client(
        tmp_path, monkeypatch, username="huaqing", password="password-1", is_admin=False,
    )
    assert person.get("/admin/users").status_code == 403
    assert person.get("/", follow_redirects=False).status_code == 200


def test_admin_can_create_another_admin(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    created = admin.post("/admin/users", data={
        "username": "boss",
        "password": "password-1",
        "password2": "password-1",
        "is_admin": "1",
    })
    assert created.status_code == 200, created.text
    assert "管理员" in created.text
    boss = auth_client(
        tmp_path, monkeypatch, username="boss", password="password-1", is_admin=False,
    )
    assert boss.get("/admin/users").status_code == 200


def test_disable_revokes_session_and_blocks_login(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    admin.post("/admin/users", data={
        "username": "shelf", "password": "password-1", "password2": "password-1",
    })
    shelf = auth_client(
        tmp_path, monkeypatch, username="shelf", password="password-1", is_admin=False,
    )
    shelf_id = _user_id("shelf")
    disabled = admin.post(f"/admin/users/{shelf_id}/disable")
    assert disabled.status_code == 200, disabled.text
    conn = _conn()
    try:
        row = conn.execute("SELECT disabled_at FROM users WHERE id=?", (shelf_id,)).fetchone()
        assert row["disabled_at"]
        sessions = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (shelf_id,)
        ).fetchone()["n"]
        assert sessions == 0
    finally:
        conn.close()
    home = shelf.get("/", follow_redirects=False)
    assert home.status_code == 303
    assert home.headers["location"].startswith("/login")
    again = shelf.post("/login", data={"username": "shelf", "password": "password-1"})
    assert again.status_code == 200
    assert "账号已停用，请联系管理员" in again.text


def test_cannot_disable_self_or_last_admin(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    admin.post("/admin/users", data={
        "username": "boss", "password": "password-1", "password2": "password-1",
        "is_admin": "1",
    })
    admin_id = _user_id("admin")
    boss_id = _user_id("boss")
    page = admin.get("/admin/users").text
    assert f"/admin/users/{admin_id}/disable" not in page
    assert f"/admin/users/{boss_id}/disable" in page

    refused = admin.post(f"/admin/users/{admin_id}/disable")
    assert refused.status_code == 200
    assert "不能停用自己" in _toast(refused)
    assert admin.get("/", follow_redirects=False).status_code == 200
    conn = _conn()
    try:
        assert conn.execute(
            "SELECT disabled_at FROM users WHERE id=?", (admin_id,)
        ).fetchone()["disabled_at"] is None
    finally:
        conn.close()

    boss = auth_client(
        tmp_path, monkeypatch, username="boss", password="password-1", is_admin=False,
    )
    gone = admin.post(f"/admin/users/{boss_id}/disable")
    assert gone.status_code == 200
    assert boss.get("/", follow_redirects=False).status_code == 303

    last = admin.post(f"/admin/users/{admin_id}/disable")
    assert "不能停用最后一位系统管理员" in _toast(last)
    assert admin.get("/", follow_redirects=False).status_code == 200
    # 停用按钮在只剩一位时不渲染。重新启用后再停用，覆盖「两位时可以停用另一位」。
    enabled = admin.post(f"/admin/users/{boss_id}/enable")
    assert enabled.status_code == 200
    boss_again = auth_client(
        tmp_path, monkeypatch, username="boss", password="password-1", is_admin=False,
    )
    assert boss_again.get("/admin/users").status_code == 200
    admin.post(f"/admin/users/{boss_id}/disable")
    final = admin.get("/admin/users").text
    assert f"/admin/users/{admin_id}/disable" not in final
    assert f"/admin/users/{boss_id}/disable" not in final
    assert f"/admin/users/{boss_id}/enable" in final


def test_reset_password_revokes_old_session(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    admin.post("/admin/users", data={
        "username": "shelf", "password": "password-1", "password2": "password-1",
    })
    shelf = auth_client(
        tmp_path, monkeypatch, username="shelf", password="password-1", is_admin=False,
    )
    shelf_id = _user_id("shelf")
    mismatch = admin.post(f"/admin/users/{shelf_id}/password", data={
        "password": "password-2", "password2": "password-3",
    })
    assert "两次输入的新密码不一致" in mismatch.text
    assert shelf.get("/", follow_redirects=False).status_code == 200

    self_reset = admin.post(f"/admin/users/{_user_id('admin')}/password", data={
        "password": "password-9", "password2": "password-9",
    })
    assert "修改密码" in _toast(self_reset)
    assert admin.get("/", follow_redirects=False).status_code == 200

    reset = admin.post(f"/admin/users/{shelf_id}/password", data={
        "password": "password-2", "password2": "password-2",
    })
    assert reset.status_code == 200
    assert "password-2" not in reset.text
    assert shelf.get("/", follow_redirects=False).status_code == 303
    from app.main import create_app
    fresh = AuthTestClient(create_app())
    fresh.get("/login")
    bad = fresh.post("/login", data={"username": "shelf", "password": "password-1"})
    assert "用户名或密码错误" in bad.text
    ok = fresh.post("/login", data={"username": "shelf", "password": "password-2"},
                    follow_redirects=False)
    assert ok.status_code == 303


def _seed_disabled_owner(admin):
    """造一块所有者不是管理员的板，初始 BOM 里放一个不会出现在管理页的位号。"""
    from app.models import create_board, create_bom_version, create_user

    conn = _conn()
    try:
        owner_id = create_user(conn, "shelf", "password-1", is_admin=False)
        create_bom_version(
            conn, "AlphaBoard", "pcb-z9", "bom-z9",
            [CsvEntry("ZR9", "secret-4k7")],
            owner_user_id=owner_id,
        )
        board_id = create_board(
            conn, "AlphaBoard", "pcb-z9", "bom-z9", "UID-ORPHAN",
            owner_user_id=owner_id,
        )
        version_id = conn.execute(
            "SELECT id FROM bom_versions WHERE board_name=? AND pcb_version=? AND bom_version=?",
            ("AlphaBoard", "pcb-z9", "bom-z9"),
        ).fetchone()["id"]
    finally:
        conn.close()
    disabled = admin.post(f"/admin/users/{owner_id}/disable")
    assert disabled.status_code == 200, disabled.text
    return owner_id, board_id, version_id


def _assert_names_without_bom(html: str, board_id: int):
    assert "UID-ORPHAN" in html
    assert "AlphaBoard" in html
    assert "pcb-z9" in html
    assert "bom-z9" in html
    assert "secret-4k7" not in html
    assert "ZR9" not in html
    assert f"/board/{board_id}" not in html
    assert 'href="/board/' not in html


def test_disabled_owner_list_and_force_transfer_board(tmp_path, monkeypatch, caplog):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    owner_id, board_id, _version_id = _seed_disabled_owner(admin)
    html = admin.get("/admin/users").text
    _assert_names_without_bom(html, board_id)
    assert "所有者已停用的单板" in html
    assert admin.get(f"/board/{board_id}").status_code == 403

    admin.post("/admin/users", data={
        "username": "newbie", "password": "password-2", "password2": "password-2",
    })
    newbie_id = _user_id("newbie")
    conn = _conn()
    try:
        log_before = conn.execute("SELECT COUNT(*) AS n FROM edit_log").fetchone()["n"]
    finally:
        conn.close()

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="reflow.admin"):
        missing = admin.post(
            f"/admin/boards/{board_id}/transfer", data={"username": "nobody"},
        )
    assert "没有这个用户" in missing.text
    assert not [rec for rec in caplog.records if rec.name == "reflow.admin"]

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="reflow.admin"):
        moved = admin.post(
            f"/admin/boards/{board_id}/transfer", data={"username": "newbie"},
        )
    assert moved.status_code == 200, moved.text
    _assert_names_without_bom(moved.text, board_id) if "UID-ORPHAN" in moved.text else None
    assert "secret-4k7" not in moved.text
    assert "ZR9" not in moved.text
    assert f"/board/{board_id}" not in moved.text
    infos = [rec for rec in caplog.records if rec.name == "reflow.admin" and rec.levelno == logging.INFO]
    assert len(infos) == 1
    message = infos[0].getMessage()
    assert f"actor={_user_id('admin')}" in message
    assert "kind=单板" in message
    assert f"object={board_id}" in message
    assert f"new_user={newbie_id}" in message
    assert "secret-4k7" not in message

    conn = _conn()
    try:
        from app.models import load_membership
        assert load_membership(conn, newbie_id, board_id) == "owner"
        assert load_membership(conn, owner_id, board_id) == "editor"
        assert load_membership(conn, _user_id("admin"), board_id) is None
        assert conn.execute("SELECT COUNT(*) AS n FROM edit_log").fetchone()["n"] == log_before
    finally:
        conn.close()
    assert admin.get(f"/board/{board_id}").status_code == 403
    # 转让之后这块板不再出现在「所有者已停用」清单里。
    assert "UID-ORPHAN" not in admin.get("/admin/users").text


def test_force_transfer_bom_version_keeps_members(tmp_path, monkeypatch, caplog):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    _owner_id, board_id, version_id = _seed_disabled_owner(admin)
    html = admin.get("/admin/users").text
    assert "负责人已停用的 BOM 版本" in html
    _assert_names_without_bom(html, board_id)

    admin.post("/admin/users", data={
        "username": "newbie", "password": "password-2", "password2": "password-2",
    })
    newbie_id = _user_id("newbie")
    conn = _conn()
    try:
        members_before = conn.execute(
            "SELECT board_id, user_id, role FROM board_members ORDER BY board_id, user_id"
        ).fetchall()
        members_before = [tuple(row) for row in members_before]
    finally:
        conn.close()

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="reflow.admin"):
        moved = admin.post(
            f"/admin/bom-versions/{version_id}/transfer", data={"username": "newbie"},
        )
    assert moved.status_code == 200, moved.text
    assert "secret-4k7" not in moved.text
    assert "ZR9" not in moved.text
    infos = [rec for rec in caplog.records if rec.name == "reflow.admin" and rec.levelno == logging.INFO]
    assert len(infos) == 1
    message = infos[0].getMessage()
    assert f"actor={_user_id('admin')}" in message
    assert "kind=BOM 版本" in message
    assert f"object={version_id}" in message
    assert f"new_user={newbie_id}" in message

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT owner_user_id FROM bom_versions WHERE id=?", (version_id,)
        ).fetchone()
        assert row["owner_user_id"] == newbie_id
        members_after = [
            tuple(r) for r in conn.execute(
                "SELECT board_id, user_id, role FROM board_members ORDER BY board_id, user_id"
            ).fetchall()
        ]
        assert members_after == members_before
    finally:
        conn.close()
    assert "bom-z9" not in admin.get("/admin/users").text.split("负责人已停用的 BOM 版本", 1)[-1]


def _sessions(user_id: int) -> int:
    conn = _conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (user_id,)
        ).fetchone()["n"]
    finally:
        conn.close()


def _is_admin(username: str) -> int:
    conn = _conn()
    try:
        return conn.execute(
            "SELECT is_admin FROM users WHERE username=?", (username,)
        ).fetchone()["is_admin"]
    finally:
        conn.close()


def test_grant_admin_takes_effect_without_dropping_session(tmp_path, monkeypatch, caplog):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    admin.post("/admin/users", data={
        "username": "clerk", "password": "password-1", "password2": "password-1",
    })
    clerk = auth_client(
        tmp_path, monkeypatch, username="clerk", password="password-1", is_admin=False,
    )
    clerk_id = _user_id("clerk")
    assert clerk.get("/admin/users").status_code == 403
    sessions_before = _sessions(clerk_id)
    conn = _conn()
    try:
        log_before = conn.execute("SELECT COUNT(*) AS n FROM edit_log").fetchone()["n"]
        members_before = [
            tuple(r) for r in conn.execute(
                "SELECT board_id, user_id, role FROM board_members ORDER BY board_id, user_id"
            )
        ]
    finally:
        conn.close()

    page = admin.get("/admin/users").text
    assert f"/admin/users/{_user_id('admin')}/admin" not in page
    assert f"/admin/users/{clerk_id}/admin" in page
    assert "设为管理员" in page

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="reflow.admin"):
        granted = admin.post(f"/admin/users/{clerk_id}/admin", data={"is_admin": "1"})
    assert granted.status_code == 200, granted.text
    assert "管理员" in granted.text
    infos = [rec for rec in caplog.records if rec.name == "reflow.admin" and rec.levelno == logging.INFO]
    assert len(infos) == 1
    message = infos[0].getMessage()
    assert f"actor={_user_id('admin')}" in message
    assert f"target={clerk_id}" in message
    assert "is_admin=1" in message
    # 旧会话还在，下一次请求就按新身份放行。
    assert _sessions(clerk_id) == sessions_before
    assert clerk.get("/admin/users").status_code == 200
    conn = _conn()
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM edit_log").fetchone()["n"] == log_before
        members_after = [
            tuple(r) for r in conn.execute(
                "SELECT board_id, user_id, role FROM board_members ORDER BY board_id, user_id"
            )
        ]
        assert members_after == members_before
    finally:
        conn.close()


def test_cannot_revoke_self_or_last_admin_and_session_stays(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    admin.post("/admin/users", data={
        "username": "boss", "password": "password-1", "password2": "password-1",
        "is_admin": "1",
    })
    boss = auth_client(
        tmp_path, monkeypatch, username="boss", password="password-1", is_admin=False,
    )
    admin_id = _user_id("admin")
    boss_id = _user_id("boss")
    html = admin.get("/admin/users").text
    assert f'hx-post="/admin/users/{admin_id}/admin"' not in html
    assert f'hx-post="/admin/users/{boss_id}/admin"' in html

    refused = admin.post(f"/admin/users/{admin_id}/admin", data={"is_admin": "0"})
    assert "不能取消自己的管理员身份" in _toast(refused)
    assert _is_admin("admin") == 1
    assert admin.get("/admin/users").status_code == 200

    sessions_before = _sessions(boss_id)
    revoked = admin.post(f"/admin/users/{boss_id}/admin", data={"is_admin": "0"})
    assert revoked.status_code == 200
    assert _is_admin("boss") == 0
    assert _sessions(boss_id) == sessions_before
    assert boss.get("/", follow_redirects=False).status_code == 200
    assert boss.get("/admin/users").status_code == 403

    last = admin.post(f"/admin/users/{admin_id}/admin", data={"is_admin": "0"})
    assert "不能取消最后一位系统管理员" in _toast(last)
    assert _is_admin("admin") == 1
    assert admin.get("/admin/users").status_code == 200
    final = admin.get("/admin/users").text
    assert f'hx-post="/admin/users/{admin_id}/admin"' not in final


def test_disabled_account_admin_flag_ignores_loginable_count(tmp_path, monkeypatch):
    """已停用的账号可以改管理员身份。最后一位只数未停用的。"""
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    admin.post("/admin/users", data={
        "username": "boss", "password": "password-1", "password2": "password-1",
        "is_admin": "1",
    })
    boss_id = _user_id("boss")
    admin.post(f"/admin/users/{boss_id}/disable")
    # 只剩 admin 一位可登录管理员，停用的 boss 仍可取消管理员。
    revoked = admin.post(f"/admin/users/{boss_id}/admin", data={"is_admin": "0"})
    assert revoked.status_code == 200, revoked.text
    assert _is_admin("boss") == 0
    conn = _conn()
    try:
        assert conn.execute(
            "SELECT disabled_at FROM users WHERE id=?", (boss_id,)
        ).fetchone()["disabled_at"]
    finally:
        conn.close()

    granted = admin.post(f"/admin/users/{boss_id}/admin", data={"is_admin": "1"})
    assert granted.status_code == 200, granted.text
    assert _is_admin("boss") == 1
    # 仍是停用，旧会话回不来；启用后按管理员登录。
    from app.main import create_app
    fresh = AuthTestClient(create_app())
    fresh.get("/login")
    blocked = fresh.post("/login", data={"username": "boss", "password": "password-1"})
    assert "账号已停用" in blocked.text
    admin.post(f"/admin/users/{boss_id}/enable")
    boss = auth_client(
        tmp_path, monkeypatch, username="boss", password="password-1", is_admin=False,
    )
    assert boss.get("/admin/users").status_code == 200


def test_admin_flag_does_not_change_board_membership(tmp_path, monkeypatch):
    admin = auth_client(tmp_path, monkeypatch, username="admin", password="test-password")
    from app.models import create_board, create_user

    conn = _conn()
    try:
        owner_id = create_user(conn, "owner", "password-1", is_admin=False)
        board_id = create_board(
            conn, "OnlyMine", "v1", "bom", "UID-MINE", owner_user_id=owner_id,
        )
    finally:
        conn.close()
    owner = auth_client(
        tmp_path, monkeypatch, username="owner", password="password-1", is_admin=False,
    )
    assert owner.get(f"/board/{board_id}").status_code == 200
    assert admin.get(f"/board/{board_id}").status_code == 403

    admin.post(f"/admin/users/{owner_id}/admin", data={"is_admin": "1"})
    assert owner.get("/admin/users").status_code == 200
    assert owner.get(f"/board/{board_id}").status_code == 200
    assert admin.get(f"/board/{board_id}").status_code == 403
    conn = _conn()
    try:
        from app.models import load_membership
        assert load_membership(conn, owner_id, board_id) == "owner"
        assert load_membership(conn, _user_id("admin"), board_id) is None
    finally:
        conn.close()

    admin.post(f"/admin/users/{owner_id}/admin", data={"is_admin": "0"})
    assert owner.get("/admin/users").status_code == 403
    assert owner.get(f"/board/{board_id}", follow_redirects=False).status_code == 200
