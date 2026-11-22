"""系统管理员的账号页。不开放注册，也不能删除用户。

停用和重置密码会吊销对方的全部会话。强制转让只看名称，不读取板内容。
"""
import json
import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from app.main import get_conn, templates
from app import models

router = APIRouter()


def _view(conn) -> dict:
    return {
        "users": models.list_users(conn),
        "enabled_admin_count": models.count_enabled_admins(conn),
        "orphan_boards": models.list_boards_with_disabled_owner(conn),
        "orphan_versions": models.list_bom_versions_with_disabled_owner(conn),
    }


def _body(request: Request, conn, *, toast: str | None = None):
    headers = {}
    if toast:
        headers["HX-Trigger"] = json.dumps({"showToast": toast})
    return templates.TemplateResponse(
        request, "_admin_users_body.html", _view(conn), headers=headers,
    )


def _error(request: Request, message: str):
    return templates.TemplateResponse(
        request, "_form_error.html", {"message": message},
        headers={"HX-Retarget": "#admin-error", "HX-Reswap": "innerHTML"},
    )


def _toast_only(message: str) -> Response:
    """规则拒绝：toast 说明原因，不改页面。正文也带这句话，方便非 htmx 的请求读到。"""
    resp = Response(status_code=200, content=message)
    resp.headers["HX-Reswap"] = "none"
    resp.headers["HX-Trigger"] = json.dumps({"showToast": message})
    return resp


def _username(conn, user_id: int) -> str:
    row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    return row["username"] if row else ""


@router.get("/admin/users")
def users_page(request: Request):
    return templates.TemplateResponse(
        request, "admin_users.html", _view(get_conn()),
    )


@router.post("/admin/users")
def users_create(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    is_admin: str = Form(""),
):
    if password != password2:
        return _error(request, "两次输入的新密码不一致")
    conn = get_conn()
    try:
        user_id = models.create_user(
            conn, username, password, is_admin=is_admin in {"1", "on", "true"},
        )
    except ValueError as exc:
        return _error(request, str(exc))
    except sqlite3.IntegrityError:
        return _error(request, "这个登录名已经有人用了")
    name = _username(conn, user_id)
    return _body(request, conn, toast=f"✓ 已创建账号「{name}」")


@router.post("/admin/users/{user_id}/disable")
def users_disable(request: Request, user_id: int):
    conn = get_conn()
    name = _username(conn, user_id)
    try:
        models.disable_user(conn, request.state.user["id"], user_id)
    except ValueError as exc:
        return _toast_only(str(exc))
    return _body(request, conn, toast=f"✓ 已停用「{name}」，对方将不能登录")


@router.post("/admin/users/{user_id}/enable")
def users_enable(request: Request, user_id: int):
    conn = get_conn()
    name = _username(conn, user_id)
    try:
        models.enable_user(conn, user_id)
    except ValueError as exc:
        return _error(request, str(exc))
    return _body(request, conn, toast=f"✓ 已重新启用「{name}」")


@router.post("/admin/users/{user_id}/password")
def users_reset_password(
    request: Request,
    user_id: int,
    password: str = Form(""),
    password2: str = Form(""),
):
    if user_id == request.state.user["id"]:
        return _toast_only("请到「修改密码」修改自己的密码")
    if password != password2:
        return _error(request, "两次输入的新密码不一致")
    conn = get_conn()
    try:
        models.set_password(conn, user_id, password)
    except ValueError as exc:
        return _error(request, str(exc))
    return _body(request, conn, toast="✓ 已重置密码，对方需要重新登录")


@router.post("/admin/boards/{board_id}/transfer")
def transfer_board(request: Request, board_id: int, username: str = Form("")):
    conn = get_conn()
    try:
        models.force_transfer_board(
            conn, request.state.user["id"], board_id, username.strip(),
        )
    except ValueError as exc:
        return _error(request, str(exc))
    return _body(request, conn, toast="✓ 已转让单板")


@router.post("/admin/bom-versions/{version_id}/transfer")
def transfer_bom_version(request: Request, version_id: int, username: str = Form("")):
    conn = get_conn()
    try:
        models.force_transfer_bom_version(
            conn, request.state.user["id"], version_id, username.strip(),
        )
    except ValueError as exc:
        return _error(request, str(exc))
    return _body(request, conn, toast="✓ 已更换 BOM 版本负责人")
