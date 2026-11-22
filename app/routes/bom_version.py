"""BOM 版本页：只读初始清单、影响预览、单事务提交、转让负责人。

单板页不再写 initial_bom。共用清单只从这里修正。
"""
import json
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from app.authz import can_correct_bom_version, can_view_bom_version, forbidden_response
from app.main import get_conn, templates
from app import models

router = APIRouter()

_CANNOT_CORRECT = "你还不能修正这份初始 BOM。"
_NO_ACCESS = "你没有这个 BOM 版本的访问权限。"


def _owner_name(conn, version) -> str:
    row = conn.execute(
        "SELECT username FROM users WHERE id=?", (version["owner_user_id"],)
    ).fetchone()
    return row["username"] if row else ""


def _page(request: Request, conn, version, user_id: int, *, error: str | None = None):
    rows = sorted(models.get_initial_bom(
        conn, version["board_name"], version["pcb_version"], version["bom_version"],
    ).items())
    return templates.TemplateResponse(request, "bom_version.html", {
        "version": version,
        "rows": rows,
        "can_correct": can_correct_bom_version(conn, user_id, version["id"]),
        "is_owner": version["owner_user_id"] == user_id,
        "owner_username": _owner_name(conn, version),
        "error": error,
    })


def _missing(version):
    if version is None:
        raise HTTPException(status_code=404, detail="BOM 版本不存在")


def _no_access(request: Request, conn, version):
    return forbidden_response(
        request, owner_username=_owner_name(conn, version) or None)


def _guard_correct(request: Request, conn, version):
    """能看但还不能改：固定文案，正文不出现任何单板 ID。"""
    user_id = request.state.user["id"]
    if not can_view_bom_version(conn, user_id, version["id"]):
        return PlainTextResponse(_NO_ACCESS, status_code=403)
    if not can_correct_bom_version(conn, user_id, version["id"]):
        return PlainTextResponse(_CANNOT_CORRECT, status_code=403)
    return None


def _stale(request: Request, username: str):
    message = f"已被 {username} 修改，请刷新"
    return templates.TemplateResponse(
        request, "_form_error.html", {"message": message},
        status_code=200,
        headers={
            "HX-Reswap": "none",
            "HX-Trigger": json.dumps({"showToast": message}),
        },
    )


def _redirect(request: Request, url: str):
    if request.headers.get("hx-request", "").lower() == "true":
        resp = PlainTextResponse("")
        resp.headers["HX-Redirect"] = url
        return resp
    return RedirectResponse(url, status_code=303)


def _choices_from_form(form) -> dict | str:
    """测试客户端直接带 JSON。页面上每个冲突是一组独立的 radio，名字 choice_{节点}。"""
    radios = {}
    for key, value in form.multi_items():
        if key.startswith("choice_"):
            radios[key[len("choice_"):]] = str(value)
    if radios:
        return radios
    return str(form.get("choices") or "")


@router.get("/bom-version/{version_id}")
def bom_version_page(request: Request, version_id: int):
    conn = get_conn()
    version = models.get_bom_version(conn, version_id)
    _missing(version)
    user_id = request.state.user["id"]
    if not can_view_bom_version(conn, user_id, version_id):
        return _no_access(request, conn, version)
    return _page(request, conn, version, user_id)


@router.post("/bom-version/{version_id}/preview")
def bom_version_preview(
    request: Request, version_id: int,
    revision: int = Form(...), reference: str = Form(""),
    op: str = Form(""), part: str = Form(""),
):
    """只算不写。revision 已经对不上时不展开各板，避免拿旧页面配新清单。"""
    conn = get_conn()
    version = models.get_bom_version(conn, version_id)
    _missing(version)
    denied = _guard_correct(request, conn, version)
    if denied is not None:
        return denied
    if int(version["revision"]) != int(revision):
        return _stale(request, models._actor_label(conn, version["revised_by"]))
    try:
        items = models.preview_bom_version_change(
            conn, version_id, reference, op, part)
    except models.BomVersionError as exc:
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": str(exc)},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
    _stored, fix_note = models.normalized_bom_part(reference.strip(), op, part)
    conflict_count = sum(len(item["conflicts"]) for item in items)
    return templates.TemplateResponse(request, "_bom_version_impact.html", {
        "version": version,
        "items": items,
        "reference": reference.strip(),
        "op": op,
        "part": "" if _stored is None else _stored,
        "fix_note": fix_note,
        "conflict_count": conflict_count,
        "revision": version["revision"],
    })


@router.post("/bom-version/{version_id}")
async def bom_version_apply(request: Request, version_id: int):
    conn = get_conn()
    version = models.get_bom_version(conn, version_id)
    _missing(version)
    denied = _guard_correct(request, conn, version)
    if denied is not None:
        return denied
    form = await request.form()
    try:
        revision = int(form.get("revision"))
    except (TypeError, ValueError):
        return PlainTextResponse("版本号无效", status_code=400)
    try:
        models.apply_bom_version_change(
            conn, version_id, request.state.user["id"], revision,
            str(form.get("reference") or ""),
            str(form.get("op") or ""),
            str(form.get("part") or ""),
            _choices_from_form(form),
        )
    except models.BomRevisionError as exc:
        return _stale(request, exc.username)
    except models.BomVersionError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    flash = quote("✓ 已修正初始清单", safe="")
    return _redirect(request, f"/bom-version/{version_id}?flash={flash}")


@router.post("/bom-version/{version_id}/transfer")
def bom_version_transfer(request: Request, version_id: int, username: str = Form("")):
    conn = get_conn()
    version = models.get_bom_version(conn, version_id)
    _missing(version)
    user_id = request.state.user["id"]
    if not can_view_bom_version(conn, user_id, version_id):
        return _no_access(request, conn, version)
    if version["owner_user_id"] != user_id:
        return PlainTextResponse("只有负责人能转让这份 BOM 版本。", status_code=403)
    try:
        models.transfer_bom_version_owner(conn, version_id, user_id, username.strip())
    except ValueError as exc:
        return _page(request, conn, version, user_id, error=str(exc))
    flash = quote(f"✓ 已把负责人转让给 {username.strip()}", safe="")
    return RedirectResponse(f"/bom-version/{version_id}?flash={flash}", status_code=303)
