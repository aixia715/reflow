import json
from urllib.parse import quote
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse

from app.main import templates, get_conn
from app import models, propagation, audit, hard_change
from app.bom_engine import fold_bom
from app.validation import validate_edit, validate_insert_time
from app import compare
from app.models import _now

router = APIRouter()


def _known_refs(initial, chain):
    """链上出现过的全部位号（初始 + 任意 add/modify 引入的）。"""
    known = set(initial)
    for cs in chain:
        for c in cs:
            if c["op"] in ("add", "modify"):
                known.add(c["reference"])
    return known


def _last_value(initial, chain, ref):
    """某位号沿链最后一次非 remove 的取值（用于「不贴」行展示其原值）。"""
    v = initial.get(ref)
    for cs in chain:
        for c in cs:
            if c["reference"] == ref and c["op"] != "remove":
                v = c["part"]
    return v


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
    known = _known_refs(initial, chain)
    removed = [
        {"reference": ref, "part": _last_value(initial, chain, ref),
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
    hcs = [dict(h) for h in models.list_hard_changes(conn, board_id)]
    timeline = hard_change.merge_timeline(nodes, hcs)
    initial_count = len(models.get_initial_bom(
        conn, board["board_name"], board["pcb_version"], board["bom_version"]))
    # 可插入位置 = 已提交节点且其直接子节点也是已提交（即不是最后一个已提交节点）；
    # 链末加变更直接用工作区，不走插入。
    child_of = {n["parent_id"]: n for n in nodes if n["parent_id"] is not None}
    insertable_ids = {
        n["id"] for n in nodes
        if n["is_committed"] and (child_of.get(n["id"]) is not None
                                  and child_of[n["id"]]["is_committed"])
    }
    return templates.TemplateResponse(
        request, "state_graph.html",
        {"board": board, "board_id": board_id, "timeline": timeline,
         "summaries": models.node_summaries(conn, board_id),
         "insertable_ids": insertable_ids,
         "initial_count": initial_count})


@router.get("/board/{board_id}/node/{node_id}")
def node_detail(request: Request, board_id: int, node_id: int):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return templates.TemplateResponse(
        request, "node_detail.html", _node_context(conn, board_id, node))


@router.post("/board/{board_id}/node/{node_id}/edit-info")
def edit_node_info(board_id: int, node_id: int,
                   message: str = Form(""), description: str = Form("")):
    """编辑节点的标题（提交说明）与长文本说明。根节点（初始状态）不可改。"""
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["parent_id"] is None:
        return PlainTextResponse("根节点（初始状态）不支持编辑信息", status_code=400)
    models.update_node_info(conn, node_id, message.strip(), description.strip())
    return RedirectResponse(
        f"/board/{board_id}/node/{node_id}?flash=✓ 已更新节点信息", status_code=303)


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


def _check_deletable(conn, board_id, node_id):
    """删除前的公共校验：节点存在且属于该单板、非根、已提交。
    返回 (node, error_response)；error_response 非 None 时直接返回它。"""
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["parent_id"] is None:
        return node, PlainTextResponse("根节点（初始状态）不可删除", status_code=400)
    if not node["is_committed"]:
        return node, PlainTextResponse(
            "工作区草稿不可删除，用「撤销」清空修改即可", status_code=400)
    return node, None


def _deleted_redirect(board_id, node_id):
    """删除成功后让 HTMX 整页跳转到状态图并提示。
    HX-Redirect 是手动设的响应头（须 latin-1），故对非 ASCII 的 flash 做 URL 编码。"""
    flash = quote(f"✓ 已删除节点 {node_id}", safe="")
    resp = PlainTextResponse("")
    resp.headers["HX-Redirect"] = f"/board/{board_id}?flash={flash}"
    return resp


@router.post("/board/{board_id}/node/{node_id}/delete")
def delete_node(request: Request, board_id: int, node_id: int):
    """删除已提交节点：无冲突直接删并跳转；有下游受影响位号则弹确认框（1-A）。"""
    conn = get_conn()
    node, err = _check_deletable(conn, board_id, node_id)
    if err is not None:
        return err
    conflicts = propagation.detect_delete_conflicts(conn, node_id)
    if conflicts:
        return templates.TemplateResponse(
            request, "_delete_conflict_modal.html",
            {"board_id": board_id, "node_id": node_id, "node": node,
             "conflicts": conflicts},
            headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})
    propagation.delete_node(conn, node_id)
    return _deleted_redirect(board_id, node_id)


@router.post("/board/{board_id}/node/{node_id}/delete-confirm")
def delete_node_confirm(board_id: int, node_id: int,
                        reference: list[str] = Form(default=[]),
                        choice: list[str] = Form(default=[])):
    """确认删除：按每个受影响位号的 keep/take 选择执行删除（1-A）。"""
    conn = get_conn()
    _node, err = _check_deletable(conn, board_id, node_id)
    if err is not None:
        return err
    propagation.delete_node(conn, node_id, dict(zip(reference, choice)))
    return _deleted_redirect(board_id, node_id)


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


def _insert_child(conn, parent_id):
    """parent 在链上的直接子节点（线性链至多一个）。"""
    return conn.execute(
        "SELECT * FROM nodes WHERE parent_id=?", (parent_id,)
    ).fetchone()


@router.get("/board/{board_id}/node/{parent_id}/insert")
def insert_page(request: Request, board_id: int, parent_id: int):
    """「在此节点后插入变更节点」的编辑界面：本地暂存，保存才落库建节点。"""
    conn = get_conn()
    parent = models.get_node(conn, parent_id)
    if parent is None or parent["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    child = _insert_child(conn, parent_id)
    if not parent["is_committed"] or child is None or not child["is_committed"]:
        return RedirectResponse(
            f"/board/{board_id}?flash=该位置不可插入（链末请直接用工作区编辑）",
            status_code=303)
    board = models.get_board(conn, board_id)
    initial, chain = models.get_chain(conn, parent_id)
    placed = fold_bom(initial, chain)
    known = _known_refs(initial, chain)
    unplaced = {ref: _last_value(initial, chain, ref)
                for ref in sorted(known - set(placed))}
    return templates.TemplateResponse(request, "insert_node.html", {
        "board": board, "board_id": board_id, "parent": parent, "child": child,
        "placed": placed, "unplaced": unplaced, "all_refs": sorted(known),
        "prev_ts": parent["committed_at"], "next_ts": child["committed_at"],
    })


@router.post("/board/{board_id}/node/{parent_id}/insert")
def insert_save(request: Request, board_id: int, parent_id: int,
                committed_at: str = Form(""), message: str = Form(""),
                description: str = Form(""), changes: str = Form("[]")):
    conn = get_conn()
    parent = models.get_node(conn, parent_id)
    if parent is None or parent["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    child = _insert_child(conn, parent_id)

    def _err(msg):
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": msg},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})

    if not parent["is_committed"] or child is None or not child["is_committed"]:
        return _err("该位置不可插入（链末请直接用工作区编辑）")
    try:
        chs = json.loads(changes)
    except (ValueError, TypeError):
        chs = []
    if not chs:
        return _err("请至少添加一条修改（不允许插入空节点）")
    terr = validate_insert_time(parent["committed_at"], child["committed_at"], committed_at)
    if terr:
        return _err(terr)
    # 落库前先在内存里逐条校验（含同一节点内重复/依赖前序改动）
    initial, chain = models.get_chain(conn, parent_id)
    sim = fold_bom(initial, chain)
    for ch in chs:
        ref = (ch.get("reference") or "").strip()
        verr = validate_edit(sim, ref, ch.get("op"), ch.get("part"))
        if verr:
            return _err(verr)
        if ch.get("op") == "remove":
            sim.pop(ref, None)
        else:
            sim[ref] = ch.get("part")

    new_id = models.insert_node_after(
        conn, parent_id, committed_at, message.strip(), description.strip())
    conflicts = []
    for ch in chs:
        ref = (ch.get("reference") or "").strip()
        op = ch.get("op")
        part_val = None if op == "remove" else ch.get("part")
        conflicts += propagation.apply_node_edit(conn, new_id, ref, op, part_val)

    if conflicts:
        return templates.TemplateResponse(
            request, "_insert_conflict_modal.html",
            {"board_id": board_id, "node_id": new_id, "conflicts": conflicts},
            headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})

    from urllib.parse import quote
    # flash 整体编码：含 # 与中文/✓，# 必须编码成 %23，否则被当作 URL fragment 截断
    flash = quote(f"✓ 已插入节点 #{new_id}")
    dest = f"/board/{board_id}/node/{new_id}?flash={flash}"
    if request.headers.get("HX-Request"):
        from fastapi.responses import Response
        return Response(status_code=204, headers={"HX-Redirect": dest})
    return RedirectResponse(dest, status_code=303)


@router.post("/board/{board_id}/commit")
def commit(board_id: int, message: str = Form(...), description: str = Form("")):
    conn = get_conn()
    models.commit_workspace(conn, board_id, message, description.strip())
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已提交：{message}", status_code=303)


def _node_ts(node) -> str:
    """节点用于时间区间的时间戳：已提交/根节点用 committed_at，草稿用当下。"""
    return node["committed_at"] or _now()


@router.get("/board/{board_id}/compare")
def compare_nodes(request: Request, board_id: int, left: int | None = None, right: int | None = None):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    if left is None or right is None:
        raise HTTPException(status_code=404, detail="缺少对比节点参数")
    if left == right:
        return RedirectResponse(
            f"/board/{board_id}?flash=不能和自己比", status_code=303)
    ln = models.get_node(conn, left)
    rn = models.get_node(conn, right)
    for n in (ln, rn):
        if n is None or n["board_id"] != board_id:
            raise HTTPException(status_code=404, detail="节点不存在")
    li, lc = models.get_chain(conn, left)
    ri, rc = models.get_chain(conn, right)
    left_bom = fold_bom(li, lc)
    right_bom = fold_bom(ri, rc)
    rows = compare.diff_boms(left_bom, right_bom)
    diff_rows = [r for r in rows if r["kind"] != "same"]
    same_rows = [r for r in rows if r["kind"] == "same"]
    counts = {k: sum(1 for r in rows if r["kind"] == k)
              for k in ("add", "modify", "remove", "same")}
    hcs = [dict(h) for h in models.list_hard_changes(conn, board_id)]
    between = compare.hard_changes_between(hcs, _node_ts(ln), _node_ts(rn))
    return templates.TemplateResponse(request, "compare.html", {
        "board": board, "board_id": board_id,
        "left_node": ln, "right_node": rn,
        "diff_rows": diff_rows, "same_rows": same_rows,
        "counts": counts, "hard_changes": between,
    })
