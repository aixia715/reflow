"""登录、登出、修改自己的密码。不开放注册。"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import password_ok, safe_next, session_days, verify_password
from app.authz import clear_session_cookie, set_session_cookie
from app.main import get_conn, templates
from app import models

router = APIRouter()


def _login_page(request: Request, *, needs_admin: bool, error: str | None,
                next_url: str, username: str):
    return templates.TemplateResponse(request, "login.html", {
        "needs_admin": needs_admin,
        "error": error,
        "next": next_url,
        "username": username,
    })


@router.get("/login")
def login_form(request: Request, next: str = ""):
    if request.state.user:
        return RedirectResponse("/", status_code=303)
    conn = get_conn()
    empty = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
    return _login_page(
        request, needs_admin=empty, error=None,
        next_url=safe_next(next), username="",
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    conn = get_conn()
    empty = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
    if empty:
        # 零用户时页面没有表单。直接 POST 也不创建账号。
        return _login_page(
            request, needs_admin=True, error=None,
            next_url=safe_next(next), username="",
        )
    user = models.get_user_by_username(conn, username)
    stored = user["password_hash"] if user else None
    # 先跑哈希（用户不存在时用 dummy），再区分停用。错密码一律同一句，即使账号已停用。
    if not password_ok(password, stored):
        return _login_page(
            request, needs_admin=False, error="用户名或密码错误",
            next_url=safe_next(next), username=username,
        )
    if user["disabled_at"]:
        return _login_page(
            request, needs_admin=False, error="账号已停用，请联系管理员",
            next_url=safe_next(next), username=username,
        )
    token = models.create_session(conn, user["id"], days=session_days())
    response = RedirectResponse(safe_next(next), status_code=303)
    set_session_cookie(response, token)
    return response


@router.post("/logout")
def logout(request: Request):
    token = request.cookies.get("reflow_session")
    if token:
        models.delete_session(get_conn(), token)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/account/password")
def password_form(request: Request):
    return templates.TemplateResponse(request, "password.html", {"error": None})


@router.post("/account/password")
def password_submit(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password2: str = Form(""),
):
    user = request.state.user
    conn = get_conn()
    row = models.get_user_by_username(conn, user["username"])

    def fail(message: str):
        return templates.TemplateResponse(request, "password.html", {"error": message})

    if row is None or not verify_password(current_password, row["password_hash"]):
        return fail("当前密码不正确")
    if new_password != new_password2:
        return fail("两次输入的新密码不一致")
    try:
        models.update_password(
            conn, user["id"], new_password,
            keep_token=request.cookies.get("reflow_session"),
        )
    except ValueError as exc:
        return fail(str(exc))
    return RedirectResponse("/?flash=密码已更新", status_code=303)
