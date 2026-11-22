"""会话中间件、CSRF，以及失败关闭的路由守卫表。

阶段 1 的业务路由全部登记为 login：登录即可，还不检查单板角色。
表里没有、也不在白名单的路由直接 500，避免新路由忘了登记却默默放行。
"""

import json
import secrets
from urllib.parse import quote

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app.auth import cookie_secure, session_days, tokens_equal

SESSION_COOKIE = "reflow_session"
CSRF_COOKIE = "reflow_csrf"

# (方法, 路由模板)。/static 前缀另判，不进这张表。
WHITELIST = frozenset({
    ("GET", "/login"),
    ("POST", "/login"),
    ("GET", "/healthz"),
    ("GET", "/version"),
})

# 与 create_app() 的路由一一对应（白名单除外，/static 挂载除外）。
# /uploads 仍是 StaticFiles 挂载，阶段 1 要求登录后才能取，所以登记为 login。
_LOGIN = "login"
GUARD: dict[tuple[str, str], str] = {
    ("GET", "/"): _LOGIN,
    ("GET", "/board/new"): _LOGIN,
    ("POST", "/board/new/preview"): _LOGIN,
    ("POST", "/board/new"): _LOGIN,
    ("DELETE", "/board/{board_id}"): _LOGIN,
    ("DELETE", "/bom-version"): _LOGIN,
    ("DELETE", "/board-group"): _LOGIN,
    ("POST", "/board-group/rename"): _LOGIN,
    ("POST", "/pcb-version/rename"): _LOGIN,
    ("POST", "/bom-version/rename"): _LOGIN,
    ("POST", "/board/{board_id}/rename"): _LOGIN,
    ("GET", "/board/{board_id}"): _LOGIN,
    ("GET", "/board/{board_id}/node/{node_id}"): _LOGIN,
    ("GET", "/board/{board_id}/node/{node_id}/download"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/edit-info"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/lint-changes"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/edit"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/undo"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/undo-all"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/delete"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/delete-confirm"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/resolve"): _LOGIN,
    ("POST", "/board/{board_id}/workspace/edit"): _LOGIN,
    ("GET", "/board/{board_id}/node/{node_id}/import/template"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/import/preview"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/import"): _LOGIN,
    ("GET", "/board/{board_id}/node/{parent_id}/insert"): _LOGIN,
    ("POST", "/board/{board_id}/node/{parent_id}/insert/import/preview"): _LOGIN,
    ("POST", "/board/{board_id}/node/{parent_id}/insert/check"): _LOGIN,
    ("POST", "/board/{board_id}/node/{parent_id}/insert"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/copy-to-draft"): _LOGIN,
    ("POST", "/board/{board_id}/commit"): _LOGIN,
    ("GET", "/board/{board_id}/compare"): _LOGIN,
    ("GET", "/board/{board_id}/compare/download"): _LOGIN,
    ("GET", "/board/{board_id}/node/{node_id}/attachments"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/attachments"): _LOGIN,
    ("GET", "/board/{board_id}/node/{node_id}/attachments/{aid}/download"): _LOGIN,
    ("POST", "/board/{board_id}/node/{node_id}/attachments/{aid}/delete"): _LOGIN,
    ("GET", "/board/{board_id}/log"): _LOGIN,
    ("GET", "/board/{board_id}/hard-change/new"): _LOGIN,
    ("POST", "/board/{board_id}/hard-change"): _LOGIN,
    ("GET", "/board/{board_id}/hard-change/{hc_id}"): _LOGIN,
    ("GET", "/board/{board_id}/hard-change/{hc_id}/edit"): _LOGIN,
    ("POST", "/board/{board_id}/hard-change/{hc_id}/edit"): _LOGIN,
    ("POST", "/board/{board_id}/hard-change/{hc_id}/delete"): _LOGIN,
    ("GET", "/hash/{value}"): _LOGIN,
    ("GET", "/hash-lookup"): _LOGIN,
    ("POST", "/logout"): _LOGIN,
    ("GET", "/account/password"): _LOGIN,
    ("POST", "/account/password"): _LOGIN,
    ("GET", "/uploads"): _LOGIN,
}

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_USER_FIELDS = ("id", "username", "is_admin", "disabled_at", "created_at")


def _method(request: Request) -> str:
    method = request.method.upper()
    return "GET" if method == "HEAD" else method


def is_public(request: Request) -> bool:
    path = request.url.path
    if path == "/static" or path.startswith("/static/"):
        return True
    return (_method(request), path) in WHITELIST


def _login_location(request: Request) -> str:
    nxt = request.url.path
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    return "/login?next=" + quote(nxt, safe="/")


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def unauthenticated_response(request: Request) -> Response:
    """整页 303；htmx 必须是 200 + HX-Redirect，否则登录页会被塞进局部区域。"""
    loc = _login_location(request)
    if _is_htmx(request):
        resp = Response(status_code=200, content=b"")
        resp.headers["HX-Redirect"] = loc
        return resp
    return RedirectResponse(loc, status_code=303)


def csrf_failure(request: Request) -> Response:
    if _is_htmx(request):
        resp = Response(status_code=200, content=b"")
        resp.headers["HX-Reswap"] = "none"
        resp.headers["HX-Trigger"] = json.dumps({"showToast": "页面已过期，请刷新后再试"})
        return resp
    return PlainTextResponse("页面已过期，请刷新后再试", status_code=403)


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE, token,
        httponly=False, samesite="lax", secure=cookie_secure(), path="/",
    )


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, samesite="lax", secure=cookie_secure(), path="/",
        max_age=session_days() * 86400,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax", secure=cookie_secure())


async def csrf_ok(request: Request, cookie_token: str) -> bool:
    """表单字段 csrf_token 或请求头 X-CSRF-Token 必须与 cookie 一致。"""
    if not cookie_token:
        return False
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied:
        # 先把 body 缓存进 request._body。form() 自己走 stream()，
        # BaseHTTPMiddleware 看到 stream 被读完会给下游一个空 body。
        await request.body()
        form = await request.form()
        supplied = str(form.get("csrf_token") or "")
    return tokens_equal(cookie_token, supplied)


def require_registered(request: Request) -> None:
    """路由匹配之后跑。没登记的路由 500，而不是默默当成已登录放行。"""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return
    key = (_method(request), path)
    if key in WHITELIST:
        return
    level = GUARD.get(key)
    if level is None:
        raise HTTPException(status_code=500, detail="路由未登记权限")
    if level == "login":
        if request.state.user is None:
            raise HTTPException(status_code=401, detail="未登录")
        return
    raise HTTPException(status_code=500, detail="路由未登记权限")


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """先解析会话，再拦未登录，再验 CSRF。静态资源不查库。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/static" or path.startswith("/static/"):
            return await call_next(request)

        # 函数内导入，避免 authz ↔ main 在模块加载时循环
        from app.main import get_conn
        from app import models

        cookie_token = request.cookies.get(CSRF_COOKIE) or ""
        csrf_token = cookie_token
        minted = False
        if not csrf_token:
            csrf_token = secrets.token_urlsafe(32)
            minted = True
        request.state.csrf = csrf_token

        conn = get_conn()
        session_token = request.cookies.get(SESSION_COOKIE) or ""
        user = None
        if session_token:
            row = models.user_by_session_token(conn, session_token)
            if row is not None:
                user = {field: row[field] for field in _USER_FIELDS}
        request.state.user = user
        slid = models.session_was_slid(conn)

        if user is None and not is_public(request):
            response = unauthenticated_response(request)
            if minted:
                set_csrf_cookie(response, csrf_token)
            if session_token:
                clear_session_cookie(response)
            return response

        if request.method.upper() not in _SAFE_METHODS:
            if not await csrf_ok(request, cookie_token):
                response = csrf_failure(request)
                if minted:
                    set_csrf_cookie(response, csrf_token)
                return response

        response = await call_next(request)
        if minted:
            set_csrf_cookie(response, csrf_token)
        if slid and session_token:
            set_session_cookie(response, session_token)
        return response
