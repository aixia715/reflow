import json
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse

from app.main import templates, get_conn
from app import models, propagation, audit
from app.bom_engine import fold_bom
from app.validation import validate_edit

router = APIRouter()


def _node_diff(conn, node):
    """返回 (完整BOM dict, {reference: 'add'|'modify'|'remove'}) 相对父节点的 diff。"""
    initial, chain = models.get_chain(conn, node["id"])
    full = fold_bom(initial, chain)
    diff = {c["reference"]: c["op"] for c in models.get_changeset(conn, node["id"])}
    return full, diff


def _validate(conn, node_id, reference, op, part) -> str | None:
    """对被编辑节点折叠后的 BOM 做位号编辑校验。"""
    initial, chain = models.get_chain(conn, node_id)
    return validate_edit(fold_bom(initial, chain), reference, op, part)


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
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    reference = reference.strip()
    err = _validate(conn, node_id, reference, op, part)
    if err:
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": err},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
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


@router.post("/board/{board_id}/node/{node_id}/undo")
def undo_change(request: Request, board_id: int, node_id: int,
                reference: str = Form(...)):
    """撤销草稿节点对某位号的修改（从 changeset 删除，恢复继承）。仅限未提交节点。"""
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["is_committed"]:
        return templates.TemplateResponse(
            request, "_form_error.html",
            {"message": "已提交节点不能撤销，请使用「修正历史记录」"},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
    models.delete_change(conn, node_id, reference)
    node = models.get_node(conn, node_id)
    full, diff = _node_diff(conn, node)
    return templates.TemplateResponse(
        request, "_bom_table.html",
        {"board_id": board_id, "node": node, "bom": sorted(full.items()), "diff": diff},
        headers={"HX-Trigger": json.dumps({"showToast": f"↩ 已撤销 {reference} 的修改"})})


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
    reference = reference.strip()
    err = _validate(conn, ws["id"], reference, op, part)
    if err:
        return PlainTextResponse(err, status_code=400)
    part_val = None if op == "remove" else part
    propagation.apply_node_edit(conn, ws["id"], reference, op, part_val)
    return RedirectResponse(f"/board/{board_id}/node/{ws['id']}", status_code=303)


@router.post("/board/{board_id}/commit")
def commit(board_id: int, message: str = Form(...)):
    conn = get_conn()
    models.commit_workspace(conn, board_id, message)
    return RedirectResponse(f"/board/{board_id}", status_code=303)
