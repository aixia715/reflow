"""硬更改路由：新建、详情、编辑、删除。"""
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, Response

from app.main import templates, get_conn
from app import models, hard_change, storage

router = APIRouter()


def _now_minute() -> str:
    """提交兜底：当 occurred_at 为空（如绕过 required 直接 POST）时用当前分钟。
    注意：新建表单的默认时间已改由前端按浏览器本地时区填充，不再走此函数。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def _hx_redirect(url: str) -> Response:
    """返回带 HX-Redirect 的 200 响应；URL 中的非 ASCII 字符自动编码（HTTP 头只接受 latin-1）。"""
    resp = Response(status_code=200)
    # HTTP 头只允许 latin-1，将 URL 中的非 ASCII 部分做 percent-encode
    safe_url = quote(url, safe="/:?=&#%+@!$'()*,;~")
    resp.headers["HX-Redirect"] = safe_url
    return resp


def _require_board(conn, board_id):
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    return board


def _require_hc(conn, board_id, hc_id):
    hc = models.get_hard_change(conn, hc_id)
    if hc is None or hc["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="硬更改不存在")
    return hc


@router.get("/board/{board_id}/hard-change/new")
def hc_new_form(request: Request, board_id: int):
    conn = get_conn()
    board = _require_board(conn, board_id)
    return templates.TemplateResponse(request, "hard_change_form.html", {
        "board": board, "board_id": board_id, "mode": "new",
        "hc": None, "images": [], "form": {}, "error": None,
        "default_time": "",
    })


@router.post("/board/{board_id}/hard-change")
async def hc_create(request: Request, board_id: int,
                    title: str = Form(""), occurred_at: str = Form(""),
                    description: str = Form(""),
                    files: list[UploadFile] = File(default=[])):
    conn = get_conn()
    board = _require_board(conn, board_id)
    title = title.strip()
    reals = [f for f in files if f.filename]
    blobs = [(f.filename, await f.read()) for f in reals]
    err = hard_change.validate_upload(title, [(n, len(b)) for n, b in blobs]) \
        or hard_change.validate_content_types([f.content_type for f in reals])
    if err:
        return templates.TemplateResponse(request, "hard_change_form.html", {
            "board": board, "board_id": board_id, "mode": "new", "hc": None,
            "images": [], "error": err, "default_time": occurred_at,
            "form": {"title": title, "occurred_at": occurred_at, "description": description},
        }, status_code=200)
    saved = []
    for name, data in blobs:
        stored = hard_change.make_stored_name(name)
        storage.save_image(stored, data)
        saved.append((stored, name))
    occurred = occurred_at.strip() or _now_minute()
    models.create_hard_change(conn, board_id, title, description.strip(), occurred, saved)
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已记录硬更改", status_code=303)


@router.get("/board/{board_id}/hard-change/{hc_id}")
def hc_detail(request: Request, board_id: int, hc_id: int):
    conn = get_conn()
    board = _require_board(conn, board_id)
    hc = _require_hc(conn, board_id, hc_id)
    return templates.TemplateResponse(request, "hard_change_detail.html", {
        "board": board, "board_id": board_id, "hc": hc,
        "images": models.list_hard_change_images(conn, hc_id),
    })


@router.get("/board/{board_id}/hard-change/{hc_id}/edit")
def hc_edit_form(request: Request, board_id: int, hc_id: int):
    conn = get_conn()
    board = _require_board(conn, board_id)
    hc = _require_hc(conn, board_id, hc_id)
    return templates.TemplateResponse(request, "hard_change_form.html", {
        "board": board, "board_id": board_id, "mode": "edit", "hc": hc,
        "images": models.list_hard_change_images(conn, hc_id), "form": {}, "error": None,
        "default_time": hc["occurred_at"],
    })


@router.post("/board/{board_id}/hard-change/{hc_id}/edit")
async def hc_edit(request: Request, board_id: int, hc_id: int,
                  title: str = Form(""), occurred_at: str = Form(""),
                  description: str = Form(""),
                  delete_image_ids: list[int] = Form(default=[]),
                  files: list[UploadFile] = File(default=[])):
    conn = get_conn()
    board = _require_board(conn, board_id)
    hc = _require_hc(conn, board_id, hc_id)
    title = title.strip()
    reals = [f for f in files if f.filename]
    blobs = [(f.filename, await f.read()) for f in reals]
    existing = models.list_hard_change_images(conn, hc_id)
    remaining = [im for im in existing if im["id"] not in set(delete_image_ids)]
    err = hard_change.validate_upload(title, [(n, len(b)) for n, b in blobs]) \
        or hard_change.validate_content_types([f.content_type for f in reals])
    if err is None and len(remaining) + len(blobs) > hard_change.MAX_IMAGES:
        err = f"附图最多 {hard_change.MAX_IMAGES} 张"
    if err:
        return templates.TemplateResponse(request, "hard_change_form.html", {
            "board": board, "board_id": board_id, "mode": "edit", "hc": hc,
            "images": existing, "error": err, "default_time": occurred_at or hc["occurred_at"],
            "form": {"title": title, "occurred_at": occurred_at, "description": description},
        }, status_code=200)
    own_ids = {im["id"] for im in existing}
    to_delete = [i for i in delete_image_ids if i in own_ids]
    if to_delete:
        storage.delete_images(models.delete_hard_change_images(conn, to_delete))
    saved = []
    for name, data in blobs:
        stored = hard_change.make_stored_name(name)
        storage.save_image(stored, data)
        saved.append((stored, name))
    if saved:
        models.add_hard_change_images(conn, hc_id, saved)
    models.update_hard_change(conn, hc_id, title, description.strip(),
                              occurred_at.strip() or hc["occurred_at"])
    return RedirectResponse(
        f"/board/{board_id}/hard-change/{hc_id}?flash=✓ 已更新硬更改", status_code=303)


@router.post("/board/{board_id}/hard-change/{hc_id}/delete")
def hc_delete(board_id: int, hc_id: int):
    conn = get_conn()
    _require_board(conn, board_id)
    _require_hc(conn, board_id, hc_id)
    storage.delete_images(models.delete_hard_change(conn, hc_id))
    return _hx_redirect(f"/board/{board_id}?flash=✓ 已删除硬更改")
