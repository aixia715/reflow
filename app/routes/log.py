from fastapi import APIRouter, Request, HTTPException
from app.main import templates, get_conn
from app import models

router = APIRouter()


@router.get("/board/{board_id}/log")
def board_log(request: Request, board_id: int,
              reference: str = "", node: str = ""):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    node_id = int(node) if node.strip() else None
    rows = models.list_board_log(conn, board_id,
                                 reference=reference.strip() or None, node_id=node_id)
    return templates.TemplateResponse(
        request, "log.html",
        {"board": board, "board_id": board_id, "rows": rows,
         "nodes": models.list_nodes(conn, board_id),
         "reference": reference, "node": node})
