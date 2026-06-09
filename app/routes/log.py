from fastapi import APIRouter, Request
from app.main import templates, get_conn
from app import models, audit

router = APIRouter()


@router.get("/board/{board_id}/log")
def board_log(request: Request, board_id: int):
    conn = get_conn()
    node_ids = [n["id"] for n in models.list_nodes(conn, board_id)]
    rows = [r for r in audit.list_log(conn) if r["node_id"] in node_ids]
    return templates.TemplateResponse(
        request, "log.html", {"board_id": board_id, "rows": rows})
