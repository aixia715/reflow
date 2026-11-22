import json
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from app.main import templates, get_conn
from app import models, storage
from app.csv_import import lint_entries, parse_bom_csv
from app.validation import validate_new_name

router = APIRouter()


def _board_card(conn, row) -> dict:
    ws = models.workspace_node(conn, row["id"])
    return {
        "id": row["id"], "board_uid": row["board_uid"],
        "role": row["role"],
        "node_count": len(models.list_nodes(conn, row["id"])),
        "pending": len(models.get_changeset(conn, ws["id"])) if ws else 0,
    }


@router.get("/")
def home(request: Request):
    conn = get_conn()
    user_id = request.state.user["id"]
    groups: dict[str, list[dict]] = {}
    seen: set[tuple] = set()
    for row in models.list_visible_boards(conn, user_id):
        key = (row["board_name"], row["pcb_version"], row["bom_version"])
        if key not in seen:
            seen.add(key)
            groups.setdefault(row["board_name"], []).append({
                "pcb_version": row["pcb_version"],
                "bom_version": row["bom_version"],
                "ref_count": len(models.get_initial_bom(
                    conn, row["board_name"], row["pcb_version"], row["bom_version"])),
                "owns_pcb": models.user_owns_all(
                    conn, user_id, board_name=row["board_name"],
                    pcb_version=row["pcb_version"]),
                "owns_bom": models.user_owns_all(
                    conn, user_id, board_name=row["board_name"],
                    pcb_version=row["pcb_version"], bom_version=row["bom_version"]),
                "boards": [],
            })
        groups[row["board_name"]][-1]["boards"].append(_board_card(conn, row))
    group_owns = {
        name: models.user_owns_all(conn, user_id, board_name=name)
        for name in groups
    }
    return templates.TemplateResponse(request, "home.html", {
        "groups": groups, "group_owns": group_owns,
    })


def _strip(*vals: str) -> list[str]:
    return [v.strip() for v in vals]


async def _read_csv(file: UploadFile):
    """读取上传 CSV，返回 (entries, problems, lint_notes, error_message)。"""
    try:
        text = (await file.read()).decode("utf-8")
        entries, problems = parse_bom_csv(text)
        entries, lint_notes = lint_entries(entries)
        return entries, problems, lint_notes, None
    except UnicodeDecodeError:
        return [], [], [], "文件不是 UTF-8 编码"
    except ValueError as e:
        return [], [], [], str(e)


def _version_choices(conn, request):
    user_id = request.state.user["id"]
    return models.list_version_choices(conn, user_id)


@router.get("/board/new")
def board_new(request: Request):
    conn = get_conn()
    versions = _version_choices(conn, request)
    return templates.TemplateResponse(request, "board_new.html", {
        "names": sorted({v["board_name"] for v in versions}),
        "pcbs": sorted({v["pcb_version"] for v in versions}),
        "boms": sorted({v["bom_version"] for v in versions}),
    })


@router.post("/board/new/preview")
async def board_new_preview(
    request: Request,
    board_name: str = Form(""), pcb_version: str = Form(""),
    bom_version: str = Form(""), file: UploadFile | None = File(None),
):
    conn = get_conn()
    board_name, pcb_version, bom_version = _strip(board_name, pcb_version, bom_version)
    ctx: dict = {"ready": False, "status": "fill", "problems": [], "lint_notes": [],
                 "ref_count": 0, "message": ""}
    if board_name and pcb_version and bom_version:
        existing = models.get_initial_bom(conn, board_name, pcb_version, bom_version)
        version = models.get_bom_version_row(conn, board_name, pcb_version, bom_version)
        if existing or version:
            owner_id = version["owner_user_id"] if version else None
            if owner_id != request.state.user["id"]:
                ctx.update(status="owned_by_other", ready=False,
                           ref_count=len(existing))
            else:
                ctx.update(status="exists", ready=True, ref_count=len(existing))
        elif file is None or not file.filename:
            ctx["status"] = "need_csv"
        else:
            entries, problems, lint_notes, err = await _read_csv(file)
            if err:
                ctx.update(status="bad_csv", message=err)
            else:
                ctx.update(status="csv", problems=problems, lint_notes=lint_notes,
                           ref_count=len(entries), ready=not problems)
    return templates.TemplateResponse(request, "_new_preview.html", ctx)


@router.post("/board/new")
async def board_create(
    request: Request,
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), board_uid: str = Form(...),
    file: UploadFile | None = File(None),
):
    conn = get_conn()
    board_name, pcb_version, bom_version, board_uid = _strip(
        board_name, pcb_version, bom_version, board_uid)
    if not board_uid:
        raise HTTPException(status_code=400, detail="单板 ID 不能为空")
    user_id = request.state.user["id"]
    version = models.get_bom_version_row(conn, board_name, pcb_version, bom_version)
    has_initial = bool(models.get_initial_bom(conn, board_name, pcb_version, bom_version))
    if version or has_initial:
        owner_id = version["owner_user_id"] if version else None
        if owner_id != user_id:
            return PlainTextResponse(
                "这个 BOM 版本已存在，只有负责人能在其下新建单板。", status_code=403)
    else:
        if file is None or not file.filename:
            raise HTTPException(status_code=400, detail="新 BOM 版本必须上传初始 BOM CSV")
        entries, problems, _lint_notes, err = await _read_csv(file)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if problems:
            raise HTTPException(status_code=400, detail="CSV 存在校验问题，无法创建")
        models.create_bom_version(
            conn, board_name, pcb_version, bom_version, entries,
            owner_user_id=user_id,
        )
    if models.board_uid_exists(conn, board_name, pcb_version, bom_version, board_uid):
        raise HTTPException(status_code=400,
                            detail=f"单板 ID “{board_uid}” 在该 BOM 版本下已存在")
    board_id = models.create_board(
        conn, board_name, pcb_version, bom_version, board_uid,
        owner_user_id=user_id,
    )
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已创建 板 {board_uid}",
                            status_code=303)


def _hx_redirect(url: str) -> Response:
    resp = Response(status_code=200)
    resp.headers["HX-Redirect"] = url
    return resp


@router.delete("/board/{board_id}")
def board_delete(board_id: int):
    conn = get_conn()
    if not models.get_board(conn, board_id):
        raise HTTPException(status_code=404, detail="单板不存在")
    att_paths = models.board_attachment_paths(conn, board_id)
    storage.delete_images(models.delete_board(conn, board_id))
    storage.delete_files(att_paths)
    return _hx_redirect("/")


async def _merged_params(request: Request) -> dict:
    """查询串和表单都认。htmx 的 DELETE 把字段放在正文里。"""
    data = {k: v for k, v in request.query_params.items()}
    ctype = request.headers.get("content-type", "")
    if "form" in ctype or "multipart" in ctype:
        form = await request.form()
        for key, value in form.multi_items():
            data[key] = str(value)
    return data


def _impact(request: Request, boards, *, kind: str, action: str, method: str,
            fields: list[tuple[str, str]], confirm_label: str = ""):
    return templates.TemplateResponse(
        request, "_scope_impact.html",
        {"boards": boards, "kind": kind, "action": action, "method": method,
         "fields": fields, "confirm_label": confirm_label},
        headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"},
    )


def _scope_or_deny(request, conn, user_id, **scope):
    """范围内有板但并非每块都是 owner 时，返回拒绝响应。没有板时返回 None。"""
    boards = models.boards_in_scope(conn, **scope)
    if not boards:
        return None, []
    if not models.user_owns_all(conn, user_id, **scope):
        from app.authz import scope_denied_response
        return scope_denied_response(request), boards
    return None, boards


@router.delete("/bom-version")
async def bom_version_delete(request: Request):
    conn = get_conn()
    data = await _merged_params(request)
    board_name = (data.get("board_name") or "").strip()
    pcb_version = (data.get("pcb_version") or "").strip()
    bom_version = (data.get("bom_version") or "").strip()
    confirm_name = (data.get("confirm_name") or "").strip()
    if not models.get_initial_bom(conn, board_name, pcb_version, bom_version):
        raise HTTPException(status_code=404, detail="BOM 版本不存在")
    user_id = request.state.user["id"]
    denied, boards = _scope_or_deny(
        request, conn, user_id, board_name=board_name,
        pcb_version=pcb_version, bom_version=bom_version)
    if denied is not None:
        return denied
    if confirm_name != bom_version:
        if confirm_name:
            return PlainTextResponse("确认名称不一致", status_code=400)
        return _impact(
            request, boards, kind="delete", method="delete",
            action="/bom-version?" + urlencode({
                "board_name": board_name, "pcb_version": pcb_version,
                "bom_version": bom_version,
            }),
            fields=[("board_name", board_name), ("pcb_version", pcb_version),
                    ("bom_version", bom_version)],
            confirm_label=bom_version,
        )
    board_ids = [b["id"] for b in boards]
    att_paths = [p for bid in board_ids for p in models.board_attachment_paths(conn, bid)]
    storage.delete_images(models.delete_bom_version(conn, board_name, pcb_version, bom_version))
    storage.delete_files(att_paths)
    return _hx_redirect("/")


@router.delete("/board-group")
async def board_group_delete(request: Request):
    conn = get_conn()
    data = await _merged_params(request)
    board_name = (data.get("board_name") or "").strip()
    confirm_name = (data.get("confirm_name") or "").strip()
    user_id = request.state.user["id"]
    denied, boards = _scope_or_deny(request, conn, user_id, board_name=board_name)
    if denied is not None:
        return denied
    if not boards:
        att_paths = models.board_attachment_paths_by_name(conn, board_name)
        storage.delete_images(models.delete_board_name(conn, board_name))
        storage.delete_files(att_paths)
        return _hx_redirect("/")
    if confirm_name != board_name:
        if confirm_name:
            return PlainTextResponse("确认名称不一致", status_code=400)
        return _impact(
            request, boards, kind="delete", method="delete",
            action="/board-group?" + urlencode({"board_name": board_name}),
            fields=[("board_name", board_name)],
            confirm_label=board_name,
        )
    att_paths = models.board_attachment_paths_by_name(conn, board_name)
    storage.delete_images(models.delete_board_name(conn, board_name))
    storage.delete_files(att_paths)
    return _hx_redirect("/")


def _toast_error(msg: str) -> Response:
    """重命名失败：200 + 弹 toast，不重定向，保留输入框。"""
    return Response(status_code=200,
                    headers={"HX-Trigger": json.dumps({"showToast": msg})})


def _confirmed(request: Request, data: dict) -> bool:
    return (data.get("confirmed") or "") == "1"


@router.post("/board-group/rename")
async def board_group_rename(request: Request):
    conn = get_conn()
    data = await _merged_params(request)
    board_name = (data.get("board_name") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    err = validate_new_name(new_name) or models.board_name_rename_error(conn, board_name, new_name)
    if err:
        return _toast_error(err)
    user_id = request.state.user["id"]
    denied, boards = _scope_or_deny(request, conn, user_id, board_name=board_name)
    if denied is not None:
        return denied
    if not boards:
        return _toast_error("没有可重命名的单板")
    if not _confirmed(request, data):
        return _impact(
            request, boards, kind="rename", method="post", action="/board-group/rename",
            fields=[("board_name", board_name), ("new_name", new_name), ("confirmed", "1")],
        )
    try:
        models.rename_board_name(conn, board_name, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/pcb-version/rename")
async def pcb_version_rename(request: Request):
    conn = get_conn()
    data = await _merged_params(request)
    board_name = (data.get("board_name") or "").strip()
    pcb_version = (data.get("pcb_version") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    err = validate_new_name(new_name) or models.pcb_version_rename_error(
        conn, board_name, pcb_version, new_name)
    if err:
        return _toast_error(err)
    user_id = request.state.user["id"]
    denied, boards = _scope_or_deny(
        request, conn, user_id, board_name=board_name, pcb_version=pcb_version)
    if denied is not None:
        return denied
    if not boards:
        return _toast_error("没有可重命名的单板")
    if not _confirmed(request, data):
        return _impact(
            request, boards, kind="rename", method="post", action="/pcb-version/rename",
            fields=[("board_name", board_name), ("pcb_version", pcb_version),
                    ("new_name", new_name), ("confirmed", "1")],
        )
    try:
        models.rename_pcb_version(conn, board_name, pcb_version, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/bom-version/rename")
async def bom_version_rename(request: Request):
    conn = get_conn()
    data = await _merged_params(request)
    board_name = (data.get("board_name") or "").strip()
    pcb_version = (data.get("pcb_version") or "").strip()
    bom_version = (data.get("bom_version") or "").strip()
    new_name = (data.get("new_name") or "").strip()
    err = validate_new_name(new_name) or models.bom_version_rename_error(
        conn, board_name, pcb_version, bom_version, new_name)
    if err:
        return _toast_error(err)
    user_id = request.state.user["id"]
    denied, boards = _scope_or_deny(
        request, conn, user_id, board_name=board_name,
        pcb_version=pcb_version, bom_version=bom_version)
    if denied is not None:
        return denied
    if not boards:
        return _toast_error("没有可重命名的单板")
    if not _confirmed(request, data):
        return _impact(
            request, boards, kind="rename", method="post", action="/bom-version/rename",
            fields=[("board_name", board_name), ("pcb_version", pcb_version),
                    ("bom_version", bom_version), ("new_name", new_name),
                    ("confirmed", "1")],
        )
    try:
        models.rename_bom_version(conn, board_name, pcb_version, bom_version, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/board/{board_id}/rename")
def board_uid_rename(board_id: int, new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
    try:
        models.rename_board_uid(conn, board_id, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")
