import json

import pytest
from fastapi.testclient import TestClient

from app.auth import DUMMY_PASSWORD_HASH
from app.db import connect, init_db
from app.main import create_app
from app.models import create_user


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "auth.sqlite"
    monkeypatch.setenv("REFLOW_DB", str(path))
    monkeypatch.delenv("REFLOW_BOOTSTRAP_USERNAME", raising=False)
    monkeypatch.delenv("REFLOW_BOOTSTRAP_PASSWORD", raising=False)
    return path


def _client():
    return TestClient(create_app())


def _csrf(client) -> str:
    client.get("/login")
    return client.cookies.get("reflow_csrf")


def test_anonymous_home_redirects_health_stays_open(db_path):
    client = _client()
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 303
    assert home.headers["location"].startswith("/login")
    assert client.get("/healthz").status_code == 200
    assert client.get("/version").status_code == 200
    # 未登录连 /docs 也进登录页，不先暴露「没有这个路由」
    assert client.get("/docs", follow_redirects=False).status_code == 303


def test_zero_users_login_page_has_no_password_form(db_path):
    client = _client()
    r = client.get("/login")
    assert r.status_code == 200
    assert "create-admin" in r.text
    assert 'type="password"' not in r.text


def test_wrong_password_does_not_set_session(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    client = _client()
    token = _csrf(client)
    r = client.post("/login", data={
        "username": "admin", "password": "nope-nope", "csrf_token": token,
    })
    assert r.status_code == 200
    assert "用户名或密码错误" in r.text
    assert "reflow_session" not in r.cookies


def test_disabled_account_message(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.execute("UPDATE users SET disabled_at='2026-01-01T00:00:00+00:00'")
    conn.commit()
    conn.close()
    client = _client()
    token = _csrf(client)
    r = client.post("/login", data={
        "username": "admin", "password": "password1", "csrf_token": token,
    })
    assert r.status_code == 200
    assert "账号已停用，请联系管理员" in r.text
    assert "reflow_session" not in r.cookies


def test_unknown_user_still_hashes(db_path, monkeypatch):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    import app.auth as auth
    calls = []
    real = auth.verify_password

    def wrapped(password, stored):
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr("app.auth.verify_password", wrapped)
    client = _client()
    token = _csrf(client)
    r = client.post("/login", data={
        "username": "nobody", "password": "password1", "csrf_token": token,
    })
    assert "用户名或密码错误" in r.text
    assert calls == [DUMMY_PASSWORD_HASH]


def test_login_next_rejects_open_redirect(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    client = _client()
    token = _csrf(client)
    r = client.post(
        "/login",
        data={
            "username": "admin", "password": "password1", "csrf_token": token,
            "next": "https://evil.example",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "reflow_session" in r.cookies


def test_login_next_keeps_internal_path(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    client = _client()
    token = _csrf(client)
    r = client.post(
        "/login",
        data={
            "username": "admin", "password": "password1", "csrf_token": token,
            "next": "/board/1/node/2",
        },
        follow_redirects=False,
    )
    assert r.headers["location"] == "/board/1/node/2"


def test_login_then_home_ok(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    client = _client()
    token = _csrf(client)
    client.post("/login", data={
        "username": "admin", "password": "password1", "csrf_token": token,
    })
    assert client.get("/").status_code == 200
    assert "admin" in client.get("/").text
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_htmx_anonymous_post_redirects_without_login_html(db_path):
    client = _client()
    r = client.post(
        "/board/1/commit",
        data={"message": "x"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert r.headers["HX-Redirect"].startswith("/login?next=")
    assert "<html" not in r.text.lower()


def test_post_without_csrf_is_rejected(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    client = _client()
    token = _csrf(client)
    client.post("/login", data={
        "username": "admin", "password": "password1", "csrf_token": token,
    })
    r = client.post(
        "/board/1/commit",
        data={"message": "x"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "页面已过期" in json.loads(r.headers["HX-Trigger"])["showToast"]


def test_logout_returns_to_login(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    client = _client()
    token = _csrf(client)
    client.post("/login", data={
        "username": "admin", "password": "password1", "csrf_token": token,
    })
    token = client.cookies.get("reflow_csrf")
    r = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
    again = client.get("/", follow_redirects=False)
    assert again.status_code == 303


def test_change_password_keeps_current_session(db_path):
    conn = connect(str(db_path))
    init_db(conn)
    create_user(conn, "admin", "password1", is_admin=True)
    conn.close()
    client = _client()
    token = _csrf(client)
    client.post("/login", data={
        "username": "admin", "password": "password1", "csrf_token": token,
    })
    token = client.cookies.get("reflow_csrf")
    bad = client.post("/account/password", data={
        "csrf_token": token,
        "current_password": "nope-nope",
        "new_password": "password2",
        "new_password2": "password2",
    })
    assert "当前密码不正确" in bad.text
    ok = client.post("/account/password", data={
        "csrf_token": token,
        "current_password": "password1",
        "new_password": "password2",
        "new_password2": "password2",
    }, follow_redirects=False)
    assert ok.status_code == 303
    assert client.get("/").status_code == 200
    # 旧密码不能再登录。另开一个客户端，避免把当前会话清掉。
    other = _client()
    other.cookies.set("reflow_csrf", token)
    failed = other.post("/login", data={
        "username": "admin", "password": "password1", "csrf_token": token,
    })
    assert "用户名或密码错误" in failed.text
