"""单板成员：添加、改角色、移除、转让。仅所有者。"""
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from app.main import get_conn, templates
from app import models

router = APIRouter()


def _panel(request: Request, conn, board_id: int, *, toast: str | None = None):
    board = models.get_board(conn, board_id)
    headers = {}
    if toast:
        headers["HX-Trigger"] = json.dumps({"showToast": toast})
    return templates.TemplateResponse(
        request, "_members_update.html",
        {"board": board, "board_id": board_id,
         "members": models.list_members(conn, board_id)},
        headers=headers,
    )


def _error(request: Request, message: str):
    return templates.TemplateResponse(
        request, "_form_error.html", {"message": message},
        headers={"HX-Retarget": "#member-error", "HX-Reswap": "innerHTML"},
    )


@router.get("/board/{board_id}/members")
def members_page(request: Request, board_id: int):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    return templates.TemplateResponse(request, "members.html", {
        "board": board, "board_id": board_id,
        "members": models.list_members(conn, board_id),
    })


@router.post("/board/{board_id}/members")
def members_add(request: Request, board_id: int,
                username: str = Form(""), role: str = Form("viewer")):
    conn = get_conn()
    try:
        models.add_member(conn, board_id, username.strip(), role)
    except ValueError as exc:
        return _error(request, str(exc))
    return _panel(request, conn, board_id, toast="✓ 已添加成员")


@router.post("/board/{board_id}/members/{user_id}")
def members_set_role(request: Request, board_id: int, user_id: int,
                     role: str = Form(...)):
    conn = get_conn()
    try:
        models.set_member_role(conn, board_id, user_id, role)
    except ValueError as exc:
        return _error(request, str(exc))
    return _panel(request, conn, board_id, toast="✓ 已更新角色")


@router.post("/board/{board_id}/members/{user_id}/remove")
def members_remove(request: Request, board_id: int, user_id: int):
    conn = get_conn()
    try:
        models.remove_member(conn, board_id, user_id)
    except ValueError as exc:
        return _error(request, str(exc))
    return _panel(request, conn, board_id, toast="✓ 已移出成员")


@router.post("/board/{board_id}/transfer")
def members_transfer(request: Request, board_id: int, user_id: int = Form(...)):
    conn = get_conn()
    actor = request.state.user["id"]
    try:
        models.transfer_ownership(conn, board_id, actor, user_id)
    except ValueError as exc:
        return _error(request, str(exc))
    # 转让后自己不再是 owner，整页回到状态图，避免停在自己已经打不开的成员页。
    # HTTP 头只能是 latin-1，中文 flash 要先百分号编码。
    resp = Response(status_code=200)
    target = quote(
        f"/board/{board_id}?flash=✓ 已转让所有权",
        safe="/:?=&#%+@!$'()*,;~",
    )
    resp.headers["HX-Redirect"] = target
    return resp
