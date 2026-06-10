from fastapi import APIRouter, Request
from app.main import templates, get_conn
from app import models

router = APIRouter()


@router.get("/board/{board_id}/log")
def board_log(request: Request, board_id: int):
    conn = get_conn()
    node_ids = [n["id"] for n in models.list_nodes(conn, board_id)]
    placeholders = ",".join("?" * len(node_ids))
    rows = conn.execute(
        f"SELECT * FROM edit_log WHERE node_id IN ({placeholders}) ORDER BY id",
        node_ids,
    ).fetchall() if node_ids else []
    return templates.TemplateResponse(
        request, "log.html", {"board_id": board_id, "rows": rows})
