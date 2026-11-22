"""会话中间件、CSRF，以及失败关闭的路由守卫表。

表里没有、也不在白名单的路由直接 500，避免新路由忘了登记却默默放行。
viewer / editor / owner 按单板角色检查；custom 只保证已登录，细规则在处理函数里。
"""

import json
import secrets
from urllib.parse import quote

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from app.auth import cookie_secure, role_at_least, session_days, tokens_equal

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
# /uploads 不再挂成静态目录，下载一律走带成员检查的路由。
_LOGIN = "login"
_VIEWER = "viewer"
_EDITOR = "editor"
_OWNER = "owner"
_CUSTOM = "custom"
GUARD: dict[tuple[str, str], str] = {
    ("GET", "/"): _LOGIN,
    ("GET", "/board/new"): _LOGIN,
    ("POST", "/board/new/preview"): _LOGIN,
    ("POST", "/board/new"): _LOGIN,
    ("DELETE", "/board/{board_id}"): _OWNER,
    ("DELETE", "/bom-version"): _CUSTOM,
    ("DELETE", "/board-group"): _CUSTOM,
    ("POST", "/board-group/rename"): _CUSTOM,
    ("POST", "/pcb-version/rename"): _CUSTOM,
    ("POST", "/bom-version/rename"): _CUSTOM,
    ("POST", "/board/{board_id}/rename"): _OWNER,
    ("GET", "/board/{board_id}"): _VIEWER,
    ("GET", "/board/{board_id}/node/{node_id}"): _VIEWER,
    ("GET", "/board/{board_id}/node/{node_id}/download"): _VIEWER,
    ("POST", "/board/{board_id}/node/{node_id}/edit-info"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/lint-changes"): _VIEWER,
    ("POST", "/board/{board_id}/node/{node_id}/edit"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/undo"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/undo-all"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/delete"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/delete-confirm"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/resolve"): _EDITOR,
    ("POST", "/board/{board_id}/workspace/edit"): _EDITOR,
    ("GET", "/board/{board_id}/node/{node_id}/import/template"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/import/preview"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/import"): _EDITOR,
    ("GET", "/board/{board_id}/node/{parent_id}/insert"): _EDITOR,
    ("POST", "/board/{board_id}/node/{parent_id}/insert/import/preview"): _EDITOR,
    ("POST", "/board/{board_id}/node/{parent_id}/insert/check"): _EDITOR,
    ("POST", "/board/{board_id}/node/{parent_id}/insert"): _EDITOR,
    ("POST", "/board/{board_id}/node/{node_id}/copy-to-draft"): _EDITOR,
    ("POST", "/board/{board_id}/commit"): _EDITOR,
    ("GET", "/board/{board_id}/compare"): _VIEWER,
    ("GET", "/board/{board_id}/compare/download"): _VIEWER,
    ("GET", "/board/{board_id}/node/{node_id}/attachments"): _VIEWER,
    ("POST", "/board/{board_id}/node/{node_id}/attachments"): _EDITOR,
    ("GET", "/board/{board_id}/node/{node_id}/attachments/{aid}/download"): _VIEWER,
    ("POST", "/board/{board_id}/node/{node_id}/attachments/{aid}/delete"): _EDITOR,
    ("GET", "/board/{board_id}/log"): _VIEWER,
    ("GET", "/board/{board_id}/hard-change/new"): _EDITOR,
    ("POST", "/board/{board_id}/hard-change"): _EDITOR,
    ("GET", "/board/{board_id}/hard-change/{hc_id}"): _VIEWER,
    ("GET", "/board/{board_id}/hard-change/{hc_id}/edit"): _EDITOR,
    ("POST", "/board/{board_id}/hard-change/{hc_id}/edit"): _EDITOR,
    ("POST", "/board/{board_id}/hard-change/{hc_id}/delete"): _EDITOR,
    ("GET", "/board/{board_id}/hard-change/{hc_id}/images/{image_id}"): _VIEWER,
    ("GET", "/board/{board_id}/members"): _OWNER,
    ("POST", "/board/{board_id}/members"): _OWNER,
    ("POST", "/board/{board_id}/members/{user_id}"): _OWNER,
    ("POST", "/board/{board_id}/members/{user_id}/remove"): _OWNER,
    ("POST", "/board/{board_id}/transfer"): _OWNER,
    ("GET", "/hash/{value}"): _CUSTOM,
    ("GET", "/hash-lookup"): _CUSTOM,
    ("POST", "/logout"): _LOGIN,
    ("GET", "/account/password"): _LOGIN,
    ("POST", "/account/password"): _LOGIN,
    ("GET", "/admin/users"): "admin",
    ("POST", "/admin/users"): "admin",
    ("POST", "/admin/users/{user_id}/disable"): "admin",
    ("POST", "/admin/users/{user_id}/enable"): "admin",
    ("POST", "/admin/users/{user_id}/password"): "admin",
    ("POST", "/admin/users/{user_id}/admin"): "admin",
    ("POST", "/admin/boards/{board_id}/transfer"): "admin",
    ("POST", "/admin/bom-versions/{version_id}/transfer"): "admin",
}

# 角色不够时，局部写操作的 toast。跨范围重命名/删除用另一句，不走这里。
_DENIED_TOAST = "没有权限做这个操作"
_SCOPE_DENIED = "这个范围内还有不属于你的单板，不能整体重命名或删除。"


class AuthzDenied(Exception):
    """让异常处理器原样返回已经组装好的响应（403 页或 HTMX toast）。"""

    def __init__(self, response: Response):
        self.response = response

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


def _partial_write(request: Request) -> bool:
    """局部写操作被拒绝时用 toast，避免 htmx 把 403 页换进表格。"""
    return _is_htmx(request) and request.method.upper() not in _SAFE_METHODS


def _toast(message: str) -> Response:
    resp = Response(status_code=200, content=b"")
    resp.headers["HX-Reswap"] = "none"
    resp.headers["HX-Trigger"] = json.dumps({"showToast": message})
    return resp


def forbidden_response(request: Request, *, owner_username: str | None,
                       member: bool = False) -> Response:
    """不是成员，或角色不够的整页请求。局部写操作改走 toast。"""
    if _partial_write(request):
        return _toast(_DENIED_TOAST)
    from app.main import templates
    return templates.TemplateResponse(
        request, "forbidden.html",
        {"owner_username": owner_username, "member": member},
        status_code=403,
    )


def scope_denied_response(request: Request) -> Response:
    """跨范围改名/删除时，范围内还有不属于自己的板。正文不列那些板的 ID。"""
    if _is_htmx(request):
        return _toast(_SCOPE_DENIED)
    return PlainTextResponse(_SCOPE_DENIED, status_code=403)


def enforce(request: Request, conn) -> Response | None:
    """返回 None 表示通过。否则是要直接交出去的响应。

    单板角色先看单板在不在（404），再看成员与角色。没有 board_id 的路由
    不能登记成 viewer / editor / owner。
    """
    from app import models

    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:
        return None
    key = (_method(request), path)
    if key in WHITELIST:
        return None
    level = GUARD.get(key)
    if level is None:
        raise HTTPException(status_code=500, detail="路由未登记权限")
    user = request.state.user
    if user is None:
        return unauthenticated_response(request)
    if level in (_LOGIN, _CUSTOM):
        return None
    if level == "admin":
        if not user["is_admin"]:
            return forbidden_response(request, owner_username=None)
        return None
    board_id = request.path_params.get("board_id")
    if board_id is None or level not in ("viewer", "editor", "owner"):
        raise HTTPException(status_code=500, detail="路由未登记权限")
    board_id = int(board_id)
    if models.get_board(conn, board_id) is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    role = models.load_membership(conn, user["id"], board_id)
    if not role_at_least(role, level):
        return forbidden_response(
            request,
            owner_username=models.board_owner_username(conn, board_id),
            member=role is not None,
        )
    request.state.board_role = role
    return None


def require_registered(request: Request) -> None:
    """路由匹配之后跑。没登记的路由 500，而不是默默放行。"""
    from app.main import get_conn
    response = enforce(request, get_conn())
    if response is not None:
        raise AuthzDenied(response)


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
