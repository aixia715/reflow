from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse

from app.main import templates, get_conn
from app import models
from app.csv_import import parse_bom_csv

router = APIRouter()


@router.get("/")
def home(request: Request):
    conn = get_conn()
    versions = models.list_bom_versions(conn)
    return templates.TemplateResponse(request, "home.html", {"versions": versions})


@router.get("/bom-version/new")
def new_bom_version(request: Request):
    conn = get_conn()
    versions = models.list_bom_versions(conn)
    return templates.TemplateResponse(request, "new_bom_version.html", {"versions": versions})


@router.post("/bom-version/import-preview")
async def import_preview(
    request: Request,
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), file: UploadFile = File(...),
):
    text = (await file.read()).decode("utf-8")
    entries, problems = parse_bom_csv(text)
    return templates.TemplateResponse(
        request, "import_preview.html",
        {"entries": entries, "problems": problems,
         "board_name": board_name, "pcb_version": pcb_version,
         "bom_version": bom_version, "csv_text": text},
    )


@router.post("/bom-version")
def create_bom_version(
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), csv_text: str = Form(...),
):
    conn = get_conn()
    entries, _ = parse_bom_csv(csv_text)
    models.create_bom_version(conn, board_name, pcb_version, bom_version, entries)
    return RedirectResponse("/", status_code=303)


@router.post("/board")
def create_board(
    board_name: str = Form(...), pcb_version: str = Form(...),
    bom_version: str = Form(...), board_uid: str = Form(...),
):
    conn = get_conn()
    board_id = models.create_board(conn, board_name, pcb_version, bom_version, board_uid)
    return RedirectResponse(f"/board/{board_id}", status_code=303)
