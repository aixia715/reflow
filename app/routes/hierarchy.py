import json

from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException, Query
from fastapi.responses import RedirectResponse, Response

from app.main import templates, get_conn
from app import models, storage
from app.csv_import import parse_bom_csv
from app.validation import validate_new_name

router = APIRouter()


@router.get("/")
def home(request: Request):
    conn = get_conn()
    groups: dict[str, list[dict]] = {}
    for v in models.list_bom_versions(conn):
        boards = []
        for b in models.list_boards(conn, v["board_name"], v["pcb_version"], v["bom_version"]):
            ws = models.workspace_node(conn, b["id"])
            boards.append({
                "id": b["id"], "board_uid": b["board_uid"],
                "node_count": len(models.list_nodes(conn, b["id"])),
                "pending": len(models.get_changeset(conn, ws["id"])) if ws else 0,
            })
        ref_count = len(models.get_initial_bom(
            conn, v["board_name"], v["pcb_version"], v["bom_version"]))
        groups.setdefault(v["board_name"], []).append({
            "pcb_version": v["pcb_version"], "bom_version": v["bom_version"],
            "ref_count": ref_count, "boards": boards,
        })
    return templates.TemplateResponse(request, "home.html", {"groups": groups})


def _strip(*vals: str) -> list[str]:
    return [v.strip() for v in vals]


async def _read_csv(file: UploadFile):
    """读取上传 CSV，返回 (entries, problems, error_message)。"""
    try:
        text = (await file.read()).decode("utf-8")
        entries, problems = parse_bom_csv(text)
        return entries, problems, None
    except UnicodeDecodeError:
        return [], [], "文件不是 UTF-8 编码"
    except ValueError as e:
        return [], [], str(e)


@router.get("/board/new")
def board_new(request: Request):
    conn = get_conn()
    versions = models.list_bom_versions(conn)
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
    ctx: dict = {"ready": False, "status": "fill", "problems": [], "ref_count": 0,
                 "message": ""}
    if board_name and pcb_version and bom_version:
        existing = models.get_initial_bom(conn, board_name, pcb_version, bom_version)
        if existing:
            ctx.update(status="exists", ready=True, ref_count=len(existing))
        elif file is None or not file.filename:
            ctx["status"] = "need_csv"
        else:
            entries, problems, err = await _read_csv(file)
            if err:
                ctx.update(status="bad_csv", message=err)
            else:
                ctx.update(status="csv", problems=problems,
                           ref_count=len(entries), ready=not problems)
    return templates.TemplateResponse(request, "_new_preview.html", ctx)


@router.post("/board/new")
async def board_create(
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), board_uid: str = Form(...),
    file: UploadFile | None = File(None),
):
    conn = get_conn()
    board_name, pcb_version, bom_version, board_uid = _strip(
        board_name, pcb_version, bom_version, board_uid)
    if not board_uid:
        raise HTTPException(status_code=400, detail="单板 ID 不能为空")
    if not models.get_initial_bom(conn, board_name, pcb_version, bom_version):
        if file is None or not file.filename:
            raise HTTPException(status_code=400, detail="新 BOM 版本必须上传初始 BOM CSV")
        entries, problems, err = await _read_csv(file)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if problems:
            raise HTTPException(status_code=400, detail="CSV 存在校验问题，无法创建")
        models.create_bom_version(conn, board_name, pcb_version, bom_version, entries)
    if models.board_uid_exists(conn, board_name, pcb_version, bom_version, board_uid):
        raise HTTPException(status_code=400,
                            detail=f"单板 ID “{board_uid}” 在该 BOM 版本下已存在")
    board_id = models.create_board(conn, board_name, pcb_version, bom_version, board_uid)
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


@router.delete("/bom-version")
def bom_version_delete(
    board_name: str = Query(...),
    pcb_version: str = Query(...),
    bom_version: str = Query(...),
):
    conn = get_conn()
    if not models.get_initial_bom(conn, board_name, pcb_version, bom_version):
        raise HTTPException(status_code=404, detail="BOM 版本不存在")
    board_ids = [b["id"] for b in models.list_boards(conn, board_name, pcb_version, bom_version)]
    att_paths = [p for bid in board_ids for p in models.board_attachment_paths(conn, bid)]
    storage.delete_images(models.delete_bom_version(conn, board_name, pcb_version, bom_version))
    storage.delete_files(att_paths)
    return _hx_redirect("/")


@router.delete("/board-group")
def board_group_delete(board_name: str = Query(...)):
    conn = get_conn()
    versions = models.list_bom_versions(conn)
    att_paths = []
    for v in versions:
        if v["board_name"] == board_name:
            for b in models.list_boards(conn, v["board_name"], v["pcb_version"], v["bom_version"]):
                att_paths += models.board_attachment_paths(conn, b["id"])
    storage.delete_images(models.delete_board_name(conn, board_name))
    storage.delete_files(att_paths)
    return _hx_redirect("/")


def _toast_error(msg: str) -> Response:
    """重命名失败：200 + 弹 toast，不重定向，保留输入框。"""
    return Response(status_code=200,
                    headers={"HX-Trigger": json.dumps({"showToast": msg})})


@router.post("/board-group/rename")
def board_group_rename(board_name: str = Form(...), new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
    try:
        models.rename_board_name(conn, board_name, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/pcb-version/rename")
def pcb_version_rename(board_name: str = Form(...), pcb_version: str = Form(...),
                       new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
    try:
        models.rename_pcb_version(conn, board_name, pcb_version, new_name)
    except ValueError as e:
        return _toast_error(str(e))
    return _hx_redirect("/")


@router.post("/bom-version/rename")
def bom_version_rename(board_name: str = Form(...), pcb_version: str = Form(...),
                       bom_version: str = Form(...), new_name: str = Form(...)):
    conn = get_conn()
    new_name = new_name.strip()
    err = validate_new_name(new_name)
    if err:
        return _toast_error(err)
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
