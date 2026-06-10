import json
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse

from app.main import templates, get_conn
from app import models, propagation, audit
from app.bom_engine import fold_bom
from app.validation import validate_edit

router = APIRouter()


def _node_context(conn, board_id: int, node) -> dict:
    """节点页/片段的完整渲染上下文：行数据（含旧值）、不贴行、修改面板、统计。"""
    board = models.get_board(conn, board_id)
    initial, chain = models.get_chain(conn, node["id"])
    full = fold_bom(initial, chain)
    parent_full = fold_bom(initial, chain[:-1])
    changes = {c["reference"]: c for c in models.get_changeset(conn, node["id"])}

    rows = []
    for ref, part in sorted(full.items()):
        ch = changes.get(ref)
        rows.append({
            "reference": ref, "part": part,
            "state": "mine" if ch else None,
            "op": ch["op"] if ch else None,
            "old": parent_full.get(ref),
        })

    # 「不贴」行 = 出现过（初始或链上 add/modify）但不在折叠结果里的位号
    known = set(initial)
    for cs in chain:
        for c in cs:
            if c["op"] in ("add", "modify"):
                known.add(c["reference"])

    def _last_value(ref):
        v = initial.get(ref)
        for cs in chain:
            for c in cs:
                if c["reference"] == ref and c["op"] != "remove":
                    v = c["part"]
        return v

    removed = [
        {"reference": ref, "part": _last_value(ref),
         "state": "mine" if ref in changes else "upstream"}
        for ref in sorted(known - set(full))
    ]
    return {
        "board": board, "board_id": board_id, "node": node,
        "rows": rows, "removed": removed,
        "changes": list(changes.values()),
        "all_refs": sorted(known),
        "total": len(full), "mine_count": len(changes), "removed_count": len(removed),
    }


def _validate(conn, node_id, reference, op, part) -> str | None:
    """对被编辑节点折叠后的 BOM 做位号编辑校验。"""
    initial, chain = models.get_chain(conn, node_id)
    return validate_edit(fold_bom(initial, chain), reference, op, part)


@router.get("/board/{board_id}")
def state_graph(request: Request, board_id: int):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    nodes = models.list_nodes(conn, board_id)
    return templates.TemplateResponse(
        request, "state_graph.html",
        {"board": board, "board_id": board_id, "nodes": nodes})


@router.get("/board/{board_id}/node/{node_id}")
def node_detail(request: Request, board_id: int, node_id: int):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return templates.TemplateResponse(
        request, "node_detail.html", _node_context(conn, board_id, node))


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

    node = models.get_node(conn, node_id)
    ctx = _node_context(conn, board_id, node)
    if conflicts:
        ctx.update({"conflicts": conflicts, "node_id": node_id})
        return templates.TemplateResponse(
            request, "_conflict_modal.html", ctx,
            headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})
    label = {"add": "已新增", "modify": "已修改", "remove": "已设为不贴"}[op]
    msg = f"✓ {label}：{reference}" + (f" → {part_val}" if part_val else "")
    return templates.TemplateResponse(
        request, "_node_update.html", ctx,
        headers={"HX-Trigger": json.dumps({"showToast": msg})})


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
    reference = reference.strip()
    models.delete_change(conn, node_id, reference)
    return templates.TemplateResponse(
        request, "_node_update.html", _node_context(conn, board_id, node),
        headers={"HX-Trigger": json.dumps({"showToast": f"↩ 已撤销 {reference} 的修改"})})


@router.post("/board/{board_id}/node/{node_id}/resolve")
def resolve(request: Request, board_id: int, node_id: int,
            downstream_node_id: list[int] = Form(...),
            reference: list[str] = Form(...),
            choice: list[str] = Form(...)):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    for ds, ref, ch in zip(downstream_node_id, reference, choice):
        ds_val = propagation._resolved_value(conn, ds, ref)
        corrected = propagation._resolved_value(conn, node_id, ref)
        propagation.resolve_conflict(
            conn, propagation.Conflict(ds, ref, ds_val, corrected), ch)
    return RedirectResponse(f"/board/{board_id}/node/{node_id}?flash=✓ 冲突已确认", status_code=303)


@router.post("/board/{board_id}/workspace/edit")
def workspace_edit(board_id: int, reference: str = Form(...),
                   op: str = Form(...), part: str = Form(None)):
    conn = get_conn()
    ws = models.workspace_node(conn, board_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="单板不存在")
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
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已提交：{message}", status_code=303)
