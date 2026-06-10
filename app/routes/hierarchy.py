from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse

from app.main import templates, get_conn
from app import models
from app.csv_import import parse_bom_csv

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
    if not models.get_initial_bom(conn, board_name, pcb_version, bom_version):
        if file is None or not file.filename:
            raise HTTPException(status_code=400, detail="新 BOM 版本必须上传初始 BOM CSV")
        entries, problems, err = await _read_csv(file)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if problems:
            raise HTTPException(status_code=400, detail="CSV 存在校验问题，无法创建")
        models.create_bom_version(conn, board_name, pcb_version, bom_version, entries)
    board_id = models.create_board(conn, board_name, pcb_version, bom_version, board_uid)
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已创建 板 {board_uid}",
                            status_code=303)
