from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from app.main import templates, get_conn
from app import models, propagation, audit
from app.bom_engine import fold_bom

router = APIRouter()


def _node_diff(conn, node):
    """返回 (完整BOM dict, {reference: 'add'|'modify'|'remove'}) 相对父节点的 diff。"""
    initial, chain = models.get_chain(conn, node["id"])
    full = fold_bom(initial, chain)
    diff = {c["reference"]: c["op"] for c in models.get_changeset(conn, node["id"])}
    return full, diff


@router.get("/board/{board_id}")
def state_graph(request: Request, board_id: int):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    nodes = models.list_nodes(conn, board_id)
    return templates.TemplateResponse(
        request, "state_graph.html",
        {"board": board, "board_id": board_id, "nodes": nodes})


@router.get("/board/{board_id}/node/{node_id}")
def node_detail(request: Request, board_id: int, node_id: int):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    full, diff = _node_diff(conn, node)
    return templates.TemplateResponse(
        request, "node_detail.html",
        {"board_id": board_id, "node": node, "bom": sorted(full.items()), "diff": diff})


@router.post("/board/{board_id}/node/{node_id}/edit")
def edit_node(request: Request, board_id: int, node_id: int,
              reference: str = Form(...), op: str = Form(...), part: str = Form(None)):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    part_val = None if op == "remove" else part
    if node["parent_id"] is None:
        board = models.get_board(conn, board_id)
        old_value = propagation._resolved_value(conn, node_id, reference)
        models.update_initial_bom(conn, board["board_name"], board["pcb_version"],
                                  board["bom_version"], reference, part_val)
        audit.record_edit(conn, node_id, reference, old_value, part_val, op, "direct")
        conflicts = propagation._detect_downstream_conflicts(conn, node, reference, part_val)
    else:
        conflicts = propagation.apply_node_edit(conn, node_id, reference, op, part_val)

    if conflicts:
        return templates.TemplateResponse(
            request, "_conflicts.html",
            {"board_id": board_id, "node_id": node_id, "conflicts": conflicts})
    node = models.get_node(conn, node_id)
    full, diff = _node_diff(conn, node)
    return templates.TemplateResponse(
        request, "_bom_table.html",
        {"board_id": board_id, "node": node, "bom": sorted(full.items()), "diff": diff})


@router.post("/board/{board_id}/node/{node_id}/resolve")
def resolve(request: Request, board_id: int, node_id: int,
            downstream_node_id: list[int] = Form(...),
            reference: list[str] = Form(...),
            choice: list[str] = Form(...)):
    conn = get_conn()
    for ds, ref, ch in zip(downstream_node_id, reference, choice):
        ds_val = propagation._resolved_value(conn, ds, ref)
        corrected = propagation._resolved_value(conn, node_id, ref)
        propagation.resolve_conflict(
            conn, propagation.Conflict(ds, ref, ds_val, corrected), ch)
    return RedirectResponse(f"/board/{board_id}/node/{node_id}", status_code=303)


@router.post("/board/{board_id}/workspace/edit")
def workspace_edit(board_id: int, reference: str = Form(...),
                   op: str = Form(...), part: str = Form(None)):
    conn = get_conn()
    ws = models.workspace_node(conn, board_id)
    part_val = None if op == "remove" else part
    propagation.apply_node_edit(conn, ws["id"], reference, op, part_val)
    return RedirectResponse(f"/board/{board_id}/node/{ws['id']}", status_code=303)


@router.post("/board/{board_id}/commit")
def commit(board_id: int, message: str = Form(...)):
    conn = get_conn()
    models.commit_workspace(conn, board_id, message)
    return RedirectResponse(f"/board/{board_id}", status_code=303)
