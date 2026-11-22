"""已登录的测试客户端。阶段 1 起业务路由要求登录，旧测试代表「已授权的管理员在操作」。"""

import httpx
from fastapi.testclient import TestClient

_SESSIONS: dict[tuple[str, str], dict[str, str]] = {}


class AuthTestClient(TestClient):
    """POST / DELETE 自动带上 CSRF 头，调用点不用手写令牌。"""

    def request(self, method, url, **kwargs):
        if str(method).upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers = dict(kwargs.pop("headers", None) or {})
            if not any(k.lower() == "x-csrf-token" for k in headers):
                token = self.cookies.get("reflow_csrf")
                if token:
                    headers["X-CSRF-Token"] = token
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


def auth_client(
    tmp_path, monkeypatch, *,
    username="admin", password="test-password", is_admin=True, upload_dir=None,
):
    """建库、确保有这个用户、登录，返回带 CSRF 的 TestClient。"""
    db = tmp_path / "t.sqlite"
    monkeypatch.setenv("REFLOW_DB", str(db))
    monkeypatch.delenv("REFLOW_BOOTSTRAP_USERNAME", raising=False)
    monkeypatch.delenv("REFLOW_BOOTSTRAP_PASSWORD", raising=False)
    if upload_dir is not None:
        monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(upload_dir))
    from app.db import connect, init_db
    from app.models import create_user, get_user_by_username
    conn = connect(str(db))
    init_db(conn)
    if get_user_by_username(conn, username) is None:
        create_user(conn, username, password, is_admin=is_admin)
    conn.close()
    from app.main import create_app
    client = AuthTestClient(create_app())
    client.get("/login")
    response = client.post("/login", data={
        "username": username, "password": password, "next": "/",
    })
    assert response.status_code == 200, response.text
    assert client.cookies.get("reflow_session"), "登录后没有会话 cookie"
    return client


def login_cookies(base_url: str, username="admin", password="test-password") -> dict[str, str]:
    """对已用引导变量造出管理员的 live server 登录，返回 cookie。同一地址复用。"""
    key = (base_url, username)
    cached = _SESSIONS.get(key)
    if cached:
        return dict(cached)
    with httpx.Client(base_url=base_url, follow_redirects=False) as client:
        client.get("/login")
        token = client.cookies.get("reflow_csrf")
        response = client.post("/login", data={
            "username": username, "password": password, "csrf_token": token,
        })
        assert response.status_code == 303, response.text
        jar = {
            "reflow_csrf": client.cookies.get("reflow_csrf"),
            "reflow_session": client.cookies.get("reflow_session"),
        }
    assert jar["reflow_session"], "登录失败"
    _SESSIONS[key] = jar
    return dict(jar)


def playwright_cookies(base_url: str, jar: dict[str, str]) -> list[dict]:
    return [
        {"name": name, "value": value, "url": base_url}
        for name, value in jar.items() if value
    ]


def auth_httpx(base_url: str, **kwargs) -> httpx.Client:
    """带管理员会话和 CSRF 头的 httpx 客户端。follow_redirects 等参数原样传下去。"""
    jar = login_cookies(base_url)
    client = httpx.Client(base_url=base_url, cookies=jar, **kwargs)

    def _csrf(request: httpx.Request) -> None:
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            request.headers["X-CSRF-Token"] = jar["reflow_csrf"]

    client.event_hooks["request"].append(_csrf)
    return client
