import json
import os
import re
from urllib.parse import quote
from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse, Response, FileResponse

from app.main import templates, get_conn
from app import models, propagation, hard_change, attachments, storage
from app.bom_engine import fold_bom, stage_change
from app.bom_export import bom_to_csv, changes_to_csv, diff_to_csv
from app.component_lint import lint_part, lint_warning_for
from app.csv_import import (
    ChangeEntry, parse_change_csv, plan_changes, change_csv_template,
    parse_bom_csv, plan_full_changes, full_bom_csv_template, lint_entries,
    build_preview_rows,
)
from app.validation import (
    validate_edit, validate_insert_time, validate_changes_payload, insert_time_bounds,
)
from app import compare
from app.models import _now

router = APIRouter()


def _actor_id(request: Request) -> int:
    """当前登录用户。审计操作人由路由传入，propagation 不读请求。"""
    return request.state.user["id"]


def _coerce_revision(raw, node) -> int:
    """打开页面时的版本号。没带时用当前值，已有测试客户端不用逐个补字段。

    页面和 hx-vals 总会带上。带了旧值才会被拒绝。
    """
    if raw is None or (isinstance(raw, str) and str(raw).strip() == ""):
        return int(node["revision"])
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def _stale(request: Request, username: str, *, fallback: str | None = None):
    """版本对不上。HTMX：200 + HX-Reswap none，#form-error 与 toast 同一句话。"""
    message = f"已被 {username} 修改，请刷新"
    if request.headers.get("HX-Request") or not fallback:
        return templates.TemplateResponse(
            request, "_revision_stale.html", {"message": message},
            status_code=200,
            headers={
                "HX-Reswap": "none",
                "HX-Trigger": json.dumps({"showToast": message}),
            },
        )
    return RedirectResponse(
        f"{fallback}?flash={quote(message, safe='')}", status_code=303)


def _trigger(msg: str, node=None) -> str:
    payload: dict = {"showToast": msg}
    if node is not None:
        payload["revisionBumped"] = {"revision": int(node["revision"])}
    return json.dumps(payload)


def _enriched_conflicts(conn, conflicts) -> list[dict]:
    """弹窗要带上算出冲突时每个下游节点的 revision。"""
    out = []
    for conflict in conflicts:
        row = models.get_node(conn, conflict.downstream_node_id)
        item = conflict._asdict()
        item["downstream_revision"] = int(row["revision"]) if row is not None else -1
        out.append(item)
    return out


def _downstream_revisions(ids, revs) -> dict[int, int]:
    found: dict[int, int] = {}
    for node_id, rev in zip(ids, revs):
        try:
            found[int(node_id)] = int(rev)
        except (TypeError, ValueError):
            found[int(node_id)] = -1
    return found


def _choice_map(ids, refs, choices) -> dict[tuple[int, str], str]:
    return {
        (int(node_id), ref): choice
        for node_id, ref, choice in zip(ids, refs, choices)
    }


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


def _node_context(conn, board_id: int, node, with_lint: bool = True) -> dict:
    """节点页/片段的完整渲染上下文：行数据（含旧值）、不贴行、修改面板、统计。

    with_lint=False 时跳过逐行 lint，面板渲染路径与检查解耦——节点页首次加载走
    这条，行先显示「检查中」，再由 /lint-changes 异步补上结果。
    """
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
        "changes": [{**dict(c),
                     "lint_warning": lint_warning_for(c["reference"], c["part"]) if with_lint else None}
                    for c in changes.values()],
        "lint_ready": with_lint,
        "fixed_ref": None,
        "fixed_note": "",
        "all_refs": sorted(known),
        "total": len(full), "mine_count": len(changes), "removed_count": len(removed),
        "attachments": models.list_node_attachments(conn, node["id"]),
    }


def _panel_context(conn, board_id: int, node) -> dict:
    """只给「本节点修改」面板用的轻量上下文。

    lint-changes 是页面加载后补一次检查结果的小端点，没必要为渲染一个面板重算
    整个节点上下文（`_node_context` 里有两次 fold_bom 和一次附件查询）。
    """
    return {
        "board_id": board_id, "node": node,
        "changes": [{**dict(c), "lint_warning": lint_warning_for(c["reference"], c["part"])}
                    for c in models.get_changeset(conn, node["id"])],
        "lint_ready": True, "fixed_ref": None, "fixed_note": "",
    }


def _validate(conn, node_id, reference, op, part) -> str | None:
    """对被编辑节点折叠后的 BOM 做位号编辑校验。"""
    initial, chain = models.get_chain(conn, node_id)
    return validate_edit(fold_bom(initial, chain), reference, op, part)


def _lint_with_note(reference: str, op: str, part: str | None) -> tuple[str | None, str]:
    """落库前归一，并回报本次的 fix 文案。返回 (归一后的值, fix文案)。

    fix 只有在这一刻拿得到：归一后原始写法就不留存了（存的是 3.9nF，不是用户
    敲的 3.9Nf），对已落库的值重跑 lint 永远只剩 warning 级——见
    `lint_warning_for` 的 docstring。所以面板上的 ⓘ 是一次性的，只能由执行
    这次写入的请求顺手带出来，刷新页面后就没了。
    """
    if op == "remove" or part is None:
        return None, ""
    fixed, issues = lint_part(reference, part)
    return fixed, next((i.message for i in issues if i.level == "fix"), "")


def _lint_or_none(reference: str, op: str, part: str | None) -> str | None:
    """落库前的服务端归一：remove/缺失原样传 None，否则跑 lint_part 拿修正值。

    输入路径上没有任何前端 lint（blur lint 已删），服务端不能只信表单传回来
    的值——用户可能绕过浏览器直接调接口，落库前必须自己归一。

    契约：**任何写入 changeset / initial_bom 的路径都必须先经本函数或
    `lint_entries`**（现有写入方：节点编辑、工作区编辑、CSV 导入、插入节点、
    建板初始 BOM；BOM 版本页走 `models.normalized_bom_part`，同一条 lint；
    复制到草稿搬的是已归一化的值）。渲染修改面板的
    `lint_warning_for` 依赖这一点——它只报 warning 级，新增写入路径若漏掉
    lint，fix 级问题会被静默吞掉：既不修正，也不在界面上提示。
    """
    return _lint_with_note(reference, op, part)[0]


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
         "attach_node_ids": models.node_ids_with_attachments(conn, board_id),
         "initial_count": initial_count,
         "workspace_revision": int(models.workspace_node(conn, board_id)["revision"]),
         "has_siblings": len(models.list_sibling_boards(conn, board_id)) > 1})


@router.get("/board/{board_id}/node/{node_id}")
def node_detail(request: Request, board_id: int, node_id: int):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    ctx = _node_context(conn, board_id, node, with_lint=False)
    ws = models.workspace_node(conn, board_id)
    ctx["workspace_revision"] = int(ws["revision"]) if ws is not None else 0
    ctx["bom_version_id"] = None
    ctx["initial_correction"] = None
    if node["parent_id"] is None:
        version = models.get_bom_version_row(
            conn, ctx["board"]["board_name"], ctx["board"]["pcb_version"],
            ctx["board"]["bom_version"])
        if version is not None:
            ctx["bom_version_id"] = version["id"]
        ctx["initial_correction"] = models.latest_initial_correction(conn, node["id"])
    return templates.TemplateResponse(request, "node_detail.html", ctx)


def _content_disposition(filename: str) -> str:
    """ASCII fallback + RFC 5987，兼顾各浏览器与中文文件名。"""
    ascii_name = re.sub(r"[^\x20-\x7e]", "_", filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _safe_filename(raw: str) -> str:
    """去掉文件系统/HTTP 头敏感字符，空白折叠为下划线。"""
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", raw)
    return re.sub(r"\s+", "_", safe).strip("_")


def _download_filename(board, node, scope: str = "full") -> str:
    """下载文件名：含定位信息，去掉文件系统/HTTP 头敏感字符。

    scope="changes" 时末尾追加「修改项」，与完整 BOM 的文件区分开。
    """
    if node["is_committed"]:
        label = node["message"] or f"节点{node['id']}"
    else:
        label = "工作区草稿"
    parts = [board["board_name"], board["pcb_version"], board["bom_version"], label]
    if scope == "changes":
        parts.append("修改项")
    raw = "_".join(p for p in parts if p)
    return (_safe_filename(raw) or "bom") + ".csv"


@router.get("/board/{board_id}/node/{node_id}/download")
def download_node_bom(board_id: int, node_id: int, scope: str = "full"):
    """下载 CSV：scope=full 为折叠后的完整 BOM，scope=changes 为本节点修改项。

    缺省是 full，已有的链接与书签行为不变。修改项 = 本节点相对父节点的
    changeset（不是相对初始 BOM 的累计差量）；根节点没有父节点、changeset 恒空，
    与其给一个只有表头的空文件，不如直接报错——界面上也不给这个入口。
    """
    if scope not in ("full", "changes"):
        raise HTTPException(status_code=400, detail="scope 只能是 full 或 changes")
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if scope == "changes" and node["parent_id"] is None:
        raise HTTPException(status_code=400,
                            detail="初始状态没有「相对父节点的修改」，请下载完整 BOM")
    board = models.get_board(conn, board_id)
    if scope == "changes":
        csv_text = changes_to_csv(models.get_changeset(conn, node_id))
    else:
        initial, chain = models.get_chain(conn, node_id)
        csv_text = bom_to_csv(fold_bom(initial, chain))
    # UTF-8 带 BOM，Excel/WPS 打开中文不乱码
    body = ("﻿" + csv_text).encode("utf-8")
    filename = _download_filename(board, node, scope)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/board/{board_id}/node/{node_id}/edit-info")
def edit_node_info(request: Request, board_id: int, node_id: int,
                   message: str = Form(""), description: str = Form(""),
                   revision: str = Form(None)):
    """编辑节点的标题（提交说明）与长文本说明。根节点（初始状态）不可改。"""
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["parent_id"] is None:
        return PlainTextResponse("根节点（初始状态）不支持编辑信息", status_code=400)
    fallback = f"/board/{board_id}/node/{node_id}"

    def _write():
        models.update_node_info(
            conn, node_id, message.strip(), description.strip(), commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(node_id, _coerce_revision(revision, node))], _write)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username, fallback=fallback)
    return RedirectResponse(f"{fallback}?flash=✓ 已更新节点信息", status_code=303)


@router.post("/board/{board_id}/node/{node_id}/lint-changes")
def lint_changes(request: Request, board_id: int, node_id: int):
    """批量取「本节点修改」面板各行的元器件值检查结果。

    面板首次渲染不算 lint（渲染路径与检查解耦），由本端点异步补上，一次请求
    带回全部行的结果。只会返回 warning 级：面板行的值都已在写入时归一，fix 级
    问题不复存在（见 `_lint_with_note`）。
    """
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return templates.TemplateResponse(
        request, "_changes_panel_block.html", _panel_context(conn, board_id, node))


def _conflict_modal(request, conn, board_id, node_id, reference, op, part_val, fix_note):
    """有下游冲突时不写库，只把当时的版本号放进确认框。"""
    new_value = None if op == "remove" else part_val
    conflicts = _enriched_conflicts(
        conn, propagation.preview_edit_conflicts(conn, node_id, reference, new_value))
    node = models.get_node(conn, node_id)
    ctx = _node_context(conn, board_id, node)
    ctx.update({
        "conflicts": conflicts, "node_id": node_id,
        "edit_reference": reference, "edit_op": op,
        "edit_part": "" if part_val is None else part_val,
        "edit_revision": int(node["revision"]),
        "fix_note": fix_note,
    })
    return templates.TemplateResponse(
        request, "_conflict_modal.html", ctx,
        headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})


def _apply_reference_edit(request, conn, board_id, node, reference, op, part_val, revision):
    """无冲突则写入并加版本号。有冲突抛 WriteAborted，调用方改弹窗、不留半截修改。"""
    new_value = None if op == "remove" else part_val

    def _write():
        if propagation.preview_edit_conflicts(conn, node["id"], reference, new_value):
            raise models.WriteAborted()
        propagation.apply_node_edit(
            conn, node["id"], reference, op, part_val, drop_noop=True,
            actor_user_id=_actor_id(request), commit=False)

    models.guarded_write(
        conn, _actor_id(request),
        [(node["id"], _coerce_revision(revision, node))], _write)


@router.post("/board/{board_id}/node/{node_id}/edit")
def edit_node(request: Request, board_id: int, node_id: int,
              reference: str = Form(...), op: str = Form(...), part: str = Form(None),
              revision: str = Form(None)):
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["parent_id"] is None:
        # 共用初始清单只在 BOM 版本页修正。这里拒绝，避免改到别的板。
        board = models.get_board(conn, board_id)
        version = models.get_bom_version_row(
            conn, board["board_name"], board["pcb_version"], board["bom_version"])
        message = "根节点是初始 BOM，请到 BOM 版本页修正。"
        if version is not None:
            message += f" /bom-version/{version['id']}"
        return PlainTextResponse(message, status_code=403)
    reference = reference.strip()
    part_val, fix_note = _lint_with_note(reference, op, part)
    err = _validate(conn, node_id, reference, op, part_val)
    if err:
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": err},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
    try:
        _apply_reference_edit(request, conn, board_id, node, reference, op, part_val, revision)
    except models.WriteAborted:
        return _conflict_modal(request, conn, board_id, node_id, reference, op, part_val, fix_note)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username)

    node = models.get_node(conn, node_id)
    ctx = _node_context(conn, board_id, node)
    if fix_note:
        # ⓘ 只能在这一刻挂上：值已归一，之后任何一次重算都拿不到 fix 级问题。
        ctx.update({"fixed_ref": reference, "fixed_note": fix_note})
    # 根节点的改动写 initial_bom 不写 node_changes，面板里没有这一行可挂图标，
    # lint 提示会无处落地——用表单下方的一次性提示兜底。
    if not any(c["reference"] == reference for c in ctx["changes"]):
        ctx["orphan_fix"] = fix_note
        ctx["orphan_warning"] = lint_warning_for(reference, part_val) or ""
    # 改回上游原样时 changeset 行会被丢掉（drop_noop），面板上不会出现这一条——
    # 沿用「已修改」的文案会和眼前的界面对不上，得说清楚发生了什么。
    if (not node["is_committed"]
            and models.get_change(conn, node_id, reference) is None):
        msg = f"↩ {reference} 已是上游原值，不记这条修改"
    else:
        label = {"add": "已新增", "modify": "已修改", "remove": "已设为不贴"}[op]
        msg = f"✓ {label}：{reference}" + (f" → {part_val}" if part_val else "")
    return templates.TemplateResponse(
        request, "_node_update.html", ctx,
        headers={"HX-Trigger": _trigger(msg, node)})


@router.post("/board/{board_id}/node/{node_id}/undo")
def undo_change(request: Request, board_id: int, node_id: int,
                reference: str = Form(...), revision: str = Form(None)):
    """撤销草稿节点对某位号的修改（从 changeset 删除，恢复继承）。仅限未提交节点。"""
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["is_committed"]:
        return templates.TemplateResponse(
            request, "_form_error.html",
            {"message": "已提交节点不能撤销，请使用右侧「修订」"},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
    reference = reference.strip()

    def _write():
        models.delete_change(conn, node_id, reference, commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(node_id, _coerce_revision(revision, node))], _write)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username)
    node = models.get_node(conn, node_id)
    return templates.TemplateResponse(
        request, "_node_update.html", _node_context(conn, board_id, node),
        headers={"HX-Trigger": _trigger(f"↩ 已撤销 {reference} 的修改", node)})


@router.post("/board/{board_id}/node/{node_id}/undo-all")
def undo_all_changes(request: Request, board_id: int, node_id: int,
                     revision: str = Form(None)):
    """一键撤销草稿节点的**全部**修改。仅限未提交节点。

    与单条撤销同语义：删 changeset 行、不记审计日志（草稿从未对外可见，撤销不是
    历史事件）。存在的理由是导入几十条修改后逐条撤销不现实。
    """
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["is_committed"]:
        return templates.TemplateResponse(
            request, "_form_error.html",
            {"message": "已提交节点不能撤销，请使用右侧「修订」"},
            headers={"HX-Retarget": "#form-error", "HX-Reswap": "innerHTML"})
    counted = {"n": 0}

    def _write():
        counted["n"] = models.clear_changes(conn, node_id, commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(node_id, _coerce_revision(revision, node))], _write)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username)
    node = models.get_node(conn, node_id)
    return templates.TemplateResponse(
        request, "_node_update.html", _node_context(conn, board_id, node),
        headers={"HX-Trigger": _trigger(f"↩ 已清除全部 {counted['n']} 条修改", node)})


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


def _delete_modal(request, conn, board_id, node):
    conflicts = _enriched_conflicts(
        conn, propagation.detect_delete_conflicts(conn, node["id"]))
    fresh = models.get_node(conn, node["id"])
    return templates.TemplateResponse(
        request, "_delete_conflict_modal.html",
        {"board_id": board_id, "node_id": node["id"], "node": fresh,
         "node_revision": int(fresh["revision"]), "conflicts": conflicts},
        headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})


@router.post("/board/{board_id}/node/{node_id}/delete")
def delete_node(request: Request, board_id: int, node_id: int,
                revision: str = Form(None)):
    """删除已提交节点：无冲突直接删并跳转；有下游受影响位号则弹确认框（1-A）。"""
    conn = get_conn()
    node, err = _check_deletable(conn, board_id, node_id)
    if err is not None:
        return err
    try:
        propagation.delete_node(
            conn, node_id, {}, actor_user_id=_actor_id(request),
            expected_revision=_coerce_revision(revision, node),
            require_choices=True)
    except models.WriteAborted:
        return _delete_modal(request, conn, board_id, node)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username, fallback=f"/board/{board_id}")
    return _deleted_redirect(board_id, node_id)


@router.post("/board/{board_id}/node/{node_id}/delete-confirm")
def delete_node_confirm(request: Request, board_id: int, node_id: int,
                        reference: list[str] = Form(default=[]),
                        choice: list[str] = Form(default=[]),
                        revision: str = Form(None),
                        downstream_node_id: list[int] = Form(default=[]),
                        downstream_revision: list[str] = Form(default=[])):
    """确认删除：按每个受影响位号的 keep/take 选择执行删除（1-A）。"""
    conn = get_conn()
    node, err = _check_deletable(conn, board_id, node_id)
    if err is not None:
        return err
    try:
        propagation.delete_node(
            conn, node_id, dict(zip(reference, choice)),
            actor_user_id=_actor_id(request),
            expected_revision=_coerce_revision(revision, node),
            downstream_revisions=_downstream_revisions(
                downstream_node_id, downstream_revision) or None,
            require_choices=True)
    except models.WriteAborted:
        return _delete_modal(request, conn, board_id, node)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username, fallback=f"/board/{board_id}")
    return _deleted_redirect(board_id, node_id)


@router.post("/board/{board_id}/node/{node_id}/resolve")
def resolve(request: Request, board_id: int, node_id: int,
            edit_reference: str = Form(""), op: str = Form(""),
            part: str = Form(""), revision: str = Form(None),
            downstream_node_id: list[int] = Form(default=[]),
            downstream_revision: list[str] = Form(default=[]),
            reference: list[str] = Form(default=[]),
            choice: list[str] = Form(default=[])):
    """确认冲突时才写入。被编辑节点和每个下游节点的版本号在同一事务里核对。"""
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    edit_reference = edit_reference.strip()
    if op not in ("add", "modify", "remove") or not edit_reference:
        return PlainTextResponse("缺少待确认的修改", status_code=400)
    part_val, _fix = _lint_with_note(edit_reference, op, part)
    err = _validate(conn, node_id, edit_reference, op, part_val)
    if err:
        return PlainTextResponse(err, status_code=400)
    fallback = f"/board/{board_id}/node/{node_id}"
    ds_revs = _downstream_revisions(downstream_node_id, downstream_revision)
    for ds in downstream_node_id:
        ds_revs.setdefault(int(ds), -1 if models.get_node(conn, int(ds)) is None
                           else int(models.get_node(conn, int(ds))["revision"]))
    choices = _choice_map(downstream_node_id, reference, choice)
    new_value = None if op == "remove" else part_val

    def _write():
        got = propagation.apply_node_edit(
            conn, node_id, edit_reference, op, part_val, drop_noop=True,
            actor_user_id=_actor_id(request), commit=False)
        for conflict in got:
            picked = choices.get((conflict.downstream_node_id, conflict.reference))
            if conflict.downstream_node_id not in ds_revs or picked not in ("keep", "take"):
                raise models.WriteAborted()
            propagation.resolve_conflict(
                conn, conflict, picked, actor_user_id=_actor_id(request), commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(node_id, _coerce_revision(revision, node)), *ds_revs.items()],
            _write)
    except models.WriteAborted:
        return _conflict_modal(
            request, conn, board_id, node_id, edit_reference, op, part_val, _fix)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username, fallback=fallback)
    return RedirectResponse(f"{fallback}?flash=✓ 冲突已确认", status_code=303)


@router.post("/board/{board_id}/workspace/edit")
def workspace_edit(request: Request, board_id: int, reference: str = Form(...),
                   op: str = Form(...), part: str = Form(None),
                   revision: str = Form(None)):
    conn = get_conn()
    ws = models.workspace_node(conn, board_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    reference = reference.strip()
    part_val, fix_note = _lint_with_note(reference, op, part)
    err = _validate(conn, ws["id"], reference, op, part_val)
    if err:
        return PlainTextResponse(err, status_code=400)
    try:
        _apply_reference_edit(request, conn, board_id, ws, reference, op, part_val, revision)
    except models.WriteAborted:
        return _conflict_modal(request, conn, board_id, ws["id"], reference, op, part_val, fix_note)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username, fallback=f"/board/{board_id}/node/{ws['id']}")
    return RedirectResponse(f"/board/{board_id}/node/{ws['id']}", status_code=303)


async def _read_upload(file) -> tuple[str, str | None]:
    """读取上传的 CSV 文本，返回 (text, error_message)。"""
    if file is None or not file.filename:
        return "", "请选择 CSV 文件"
    try:
        return (await file.read()).decode("utf-8"), None
    except UnicodeDecodeError:
        return "", "文件不是 UTF-8 编码"


def _import_draft(conn, board_id: int, node_id: int):
    """导入只允许对工作区草稿做。返回草稿节点行；非草稿返回 None。"""
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return None if node["is_committed"] else node


@router.get("/board/{board_id}/node/{node_id}/import/template")
def import_csv_template(board_id: int, node_id: int, mode: str = "diff"):
    """下载导入模板。mode=full 仅 Reference/Part 两列；否则含 OP 列（差异模式）。

    模板本身与节点状态无关；入口只在工作区草稿面板展示。
    """
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    body = (full_bom_csv_template() if mode == "full"
            else change_csv_template()).encode("utf-8")
    fname = "full_bom_template.csv" if mode == "full" else "change_template.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(fname)},
    )


@router.post("/board/{board_id}/node/{node_id}/import/preview")
async def import_preview(request: Request, board_id: int, node_id: int,
                         file: UploadFile | None = File(None),
                         mode: str = Form("diff")):
    """解析上传的 CSV，渲染预览；不写库。有任一问题行则不给应用按钮。

    mode="diff"：CSV 是修改清单（现有语义）。
    mode="full"：CSV 是完整目标 BOM，与当前折叠 BOM 求差得出修改。
    """
    conn = get_conn()
    draft = _import_draft(conn, board_id, node_id)
    if draft is None:
        return PlainTextResponse("只有工作区草稿支持导入修改", status_code=400)

    ctx = {"board_id": board_id, "node_id": node_id, "message": "",
           "revision": int(draft["revision"]),
           "changes": [], "problems": [], "rows": [], "ready": False,
           "mode": mode, "unchanged": 0,
           # 显式传 False：模板的 {% if ready and client_apply %} 若靠 Undefined
           # 为假值，改成 StrictUndefined 那天这里会直接 500
           "client_apply": False,
           "counts": {"add": 0, "modify": 0, "remove": 0}, "changes_json": "[]"}

    text, err = await _read_upload(file)
    if err:
        ctx["message"] = err
        return templates.TemplateResponse(request, "_import_preview.html", ctx)

    initial, chain = models.get_chain(conn, node_id)
    current = fold_bom(initial, chain)
    try:
        if mode == "full":
            entries, problems = parse_bom_csv(text, forbid_op=True)
            entries, lint_notes = lint_entries(entries)
            # 已被 parse 判为问题的位号（空 Part、CSV 内重复等）不参与求差：
            # 两端都剔除，既不重复报错，也不会因缺席而被误算成 remove。
            bad = {p.reference for p in problems}
            target = {e.reference: e.part for e in entries if e.reference not in bad}
            base = {ref: part for ref, part in current.items() if ref not in bad}
            changes, invalid = plan_full_changes(base, target)
            ctx["unchanged"] = sum(1 for ref, part in base.items()
                                   if target.get(ref) == part)
        else:
            entries, problems = parse_change_csv(text)
            entries, lint_notes = lint_entries(entries)
            changes, invalid = plan_changes(current, entries)
    except ValueError as e:
        ctx["message"] = str(e)
        return templates.TemplateResponse(request, "_import_preview.html", ctx)

    problems = problems + invalid
    dicts = [c._asdict() for c in changes]
    ctx.update(
        changes=dicts, problems=problems,
        # 变更行、问题行、lint 提示合成一张表渲染；changes_json 仍取自 changes，
        # 应用接口的载荷不因为界面合并而多带字段。
        rows=build_preview_rows(changes, problems, lint_notes),
        ready=bool(changes) and not problems,
        counts={op: sum(1 for c in changes if c.op == op)
                for op in ("add", "modify", "remove")},
        changes_json=json.dumps(dicts, ensure_ascii=False),
    )
    return templates.TemplateResponse(request, "_import_preview.html", ctx)


@router.post("/board/{board_id}/node/{node_id}/import")
def import_apply(request: Request, board_id: int, node_id: int,
                 changes: str = Form("[]"), revision: str = Form(None)):
    """应用预览过的修改清单。落库前重新校验（草稿可能已变），全有或全无。"""
    conn = get_conn()
    if _import_draft(conn, board_id, node_id) is None:
        return PlainTextResponse("只有工作区草稿支持导入修改", status_code=400)

    def _err(msg):
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": msg},
            headers={"HX-Retarget": "#import-error", "HX-Reswap": "innerHTML"})

    payload, perr = validate_changes_payload(changes)
    if perr:
        return _err(f"导入被拒绝：{perr}")
    if not payload:
        return _err("没有可导入的修改")

    entries = [ChangeEntry(c["reference"].strip(), c.get("op"), c.get("part") or "")
               for c in payload]
    # 服务端归一：changes_json 是预览阶段回传的值，正常流程已经 lint 过，
    # 但这里是直接可调的接口，不能只信客户端传回来的字符串，落库前再 lint 一遍。
    entries, _lint_notes = lint_entries(entries)
    initial, chain = models.get_chain(conn, node_id)
    planned, problems = plan_changes(fold_bom(initial, chain), entries)
    if problems:
        # 不归因「草稿已变化」——非法 op 等结构性问题并非草稿变化所致；
        # detail 取自 validate_edit，本身已说明具体原因。
        return _err(f"导入被拒绝：{problems[0].reference} {problems[0].detail}")

    node = models.get_node(conn, node_id)

    def _write():
        for change in planned:
            propagation.apply_node_edit(
                conn, node_id, change.reference, change.op, change.part,
                actor_user_id=_actor_id(request), commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(node_id, _coerce_revision(revision, node))], _write)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username)

    flash = quote(f"✓ 已导入 {len(planned)} 条修改", safe="")
    return Response(status_code=204, headers={
        "HX-Redirect": f"/board/{board_id}/node/{node_id}?flash={flash}"})


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
    min_ts, max_ts = insert_time_bounds(parent["committed_at"], child["committed_at"])
    return templates.TemplateResponse(request, "insert_node.html", {
        "board": board, "board_id": board_id, "parent": parent, "child": child,
        "placed": placed, "unplaced": unplaced, "all_refs": sorted(known),
        # 页面只需要可选区间的两个端点；相邻节点的原始时刻不再下发，免得
        # 前端又去自己截断秒算边界（那正是与 validate_insert_time 漂移的来源）
        "min_ts": min_ts, "max_ts": max_ts,
    })


def _insert_boms(conn, parent_id, pending) -> tuple[dict, dict]:
    """插入页要用的两张 BOM：(父节点折叠结果, 叠上本页暂存改动后的当前视图)。

    校验和「相对屏幕」的求差用后者——先手工加一条再导入，基准必须是用户眼前
    看到的表；换算落库用的 op 则用前者，changeset 是相对父节点的。
    """
    initial, chain = models.get_chain(conn, parent_id)
    parent_bom = fold_bom(initial, chain)
    current = dict(parent_bom)
    for ch in pending:
        ref = (ch.get("reference") or "").strip()
        if ch.get("op") == "remove":
            current.pop(ref, None)
        else:
            current[ref] = ch.get("part")
    return parent_bom, current


@router.post("/board/{board_id}/node/{parent_id}/insert/import/preview")
async def insert_import_preview(request: Request, board_id: int, parent_id: int,
                                file: UploadFile | None = File(None),
                                mode: str = Form("diff"),
                                changes: str = Form("[]"),
                                revision: str = Form(None)):
    """插入页的 CSV 导入预览：**只算不写库**，结果交回浏览器合进暂存。

    与节点页的 `/import/preview` 同一套解析与求差逻辑，两点不同：
    · 基准是「父节点折叠 BOM + 本页已暂存的改动」，即用户眼前那张表；
    · 除了展示用的 changes，另外回一份 `staged`——每条按 `stage_change` 换算成
      相对父节点的 op，值回到父节点原样的标成 drop，与 `/insert/check` 同口径。

    没有「应用」端点：这一页的改动本就不落库，前端拿 staged 合进暂存即可。
    """
    conn = get_conn()
    parent = models.get_node(conn, parent_id)
    if parent is None or parent["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")

    ctx = {"board_id": board_id, "node_id": parent_id, "message": "",
           "revision": int(parent["revision"]),
           "changes": [], "problems": [], "rows": [], "ready": False,
           "mode": mode, "unchanged": 0, "client_apply": True, "staged_json": "[]",
           "counts": {"add": 0, "modify": 0, "remove": 0}, "changes_json": "[]"}

    def _render(msg=""):
        ctx["message"] = msg
        return templates.TemplateResponse(request, "_import_preview.html", ctx)

    child = _insert_child(conn, parent_id)
    if not parent["is_committed"] or child is None or not child["is_committed"]:
        return _render("该位置不可插入（链末请直接用工作区编辑）")
    if _coerce_revision(revision, parent) != int(parent["revision"]):
        message = f"已被 {models._actor_label(conn, parent['revised_by'])} 修改，请刷新"
        resp = _render(message)
        resp.headers["HX-Trigger"] = json.dumps(
            {"insertChecked": {"ok": False, "error": message}})
        return resp
    pending, perr = validate_changes_payload(changes)
    if perr:
        return _render(perr)

    text, err = await _read_upload(file)
    if err:
        return _render(err)

    parent_bom, current = _insert_boms(conn, parent_id, pending)
    try:
        if mode == "full":
            entries, problems = parse_bom_csv(text, forbid_op=True)
            entries, lint_notes = lint_entries(entries)
            bad = {p.reference for p in problems}
            target = {e.reference: e.part for e in entries if e.reference not in bad}
            base = {ref: part for ref, part in current.items() if ref not in bad}
            planned, invalid = plan_full_changes(base, target)
            ctx["unchanged"] = sum(1 for ref, part in base.items()
                                   if target.get(ref) == part)
        else:
            entries, problems = parse_change_csv(text)
            entries, lint_notes = lint_entries(entries)
            planned, invalid = plan_changes(current, entries)
    except ValueError as e:
        return _render(str(e))

    problems = problems + invalid
    # lint 提示随载荷一起带回：手工「添加这条」的暂存行有 ⓘ/⚠，预览行也有，
    # 唯独导入进暂存后消失的话，同一条改动在三处观感不一致。
    notes = {n.reference: n for n in lint_notes}
    staged = []
    for c in planned:
        action, op_, part = stage_change(
            parent_bom, c.reference, None if c.op == "remove" else c.part)
        note = notes.get(c.reference)
        staged.append({
            "reference": c.reference, "op": op_, "part": part, "action": action,
            "fix": note.detail if note and note.level == "fix" else "",
            "warning": note.detail if note and note.level == "warning" else "",
        })
    ctx.update(
        changes=[c._asdict() for c in planned], problems=problems,
        rows=build_preview_rows(planned, problems, lint_notes),
        ready=bool(planned) and not problems,
        counts={op_: sum(1 for c in planned if c.op == op_)
                for op_ in ("add", "modify", "remove")},
        staged_json=json.dumps(staged, ensure_ascii=False),
    )
    return _render()


@router.post("/board/{board_id}/node/{parent_id}/insert/check")
def insert_check(board_id: int, parent_id: int, reference: str = Form(""),
                 op: str = Form(""), part: str = Form(None),
                 changes: str = Form("[]"), revision: str = Form(None)):
    """插入页「添加这条」的服务端校验 + lint 归一。204 + `HX-Trigger` 带回结果。

    插入页的改动暂存在浏览器里，曾因此在 JS 里手抄了一份 `validate_edit`（连
    中文错误文案一起抄）和一份 op 推断，两份规则各改各的必然漂移；客户端又完全
    没有 lint，服务端只在保存时静默归一，用户看不到自己敲的 3.9Nf 变成了 3.9nF。
    本端点把校验、op 推断、lint 三件事都收回服务端，客户端只负责显示与暂存。

    `changes` 是本页已暂存的改动清单：基准 BOM = 父节点折叠结果 + 这些改动，
    这样「先 add D1 再 modify D1」才判得对。返回的 op 则一律**相对父节点**
    （暂存里 add 过的位号再改，落库仍是一条 add），与 `insert_save` 的写入一致。

    action 为 drop 表示这次编辑把值改回了父节点原样（或撤掉了本页刚 add 的位号）
    = 无操作，界面应把这条从暂存里删掉，而不是留一条空转的修改。
    """
    conn = get_conn()
    parent = models.get_node(conn, parent_id)
    if parent is None or parent["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")

    def _reply(payload):
        return Response(status_code=204, headers={
            "HX-Trigger": json.dumps({"insertChecked": payload})})

    def _bad(msg):
        return _reply({"ok": False, "error": msg})

    child = _insert_child(conn, parent_id)
    if not parent["is_committed"] or child is None or not child["is_committed"]:
        return _bad("该位置不可插入（链末请直接用工作区编辑）")
    if _coerce_revision(revision, parent) != int(parent["revision"]):
        return _bad(
            f"已被 {models._actor_label(conn, parent['revised_by'])} 修改，请刷新")
    pending, perr = validate_changes_payload(changes)
    if perr:
        return _bad(perr)

    reference = (reference or "").strip()
    part_val, fix_note = _lint_with_note(reference, op, part)

    parent_bom, sim = _insert_boms(conn, parent_id, pending)
    err = validate_edit(sim, reference, op, part_val)
    if err:
        return _bad(err)

    # op 相对父节点算、抵消判定为 drop——与 CSV 批量导入共用同一套换算
    action, stored_op, stored_part = stage_change(
        parent_bom, reference, None if op == "remove" else part_val)

    return _reply({"ok": True, "action": action, "reference": reference,
                   "op": stored_op, "part": stored_part, "fix": fix_note,
                   "warning": lint_warning_for(reference, part_val) or ""})


@router.post("/board/{board_id}/node/{parent_id}/insert")
def insert_save(request: Request, board_id: int, parent_id: int,
                committed_at: str = Form(""), message: str = Form(""),
                description: str = Form(""), changes: str = Form("[]"),
                revision: str = Form(None),
                downstream_node_id: list[int] = Form(default=[]),
                downstream_revision: list[str] = Form(default=[]),
                reference: list[str] = Form(default=[]),
                choice: list[str] = Form(default=[])):
    conn = get_conn()
    parent = models.get_node(conn, parent_id)
    if parent is None or parent["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    child = _insert_child(conn, parent_id)

    def _err(msg):
        return templates.TemplateResponse(
            request, "_form_error.html", {"message": msg},
            headers={
                "HX-Retarget": "#form-error", "HX-Reswap": "innerHTML",
                "HX-Trigger": json.dumps(
                    {"insertChecked": {"ok": False, "error": msg}}),
            })

    if not parent["is_committed"] or child is None or not child["is_committed"]:
        return _err("该位置不可插入（链末请直接用工作区编辑）")
    chs, perr = validate_changes_payload(changes)
    if perr:
        return _err(perr)
    if not chs:
        return _err("请至少添加一条修改（不允许插入空节点）")
    terr = validate_insert_time(parent["committed_at"], child["committed_at"], committed_at)
    if terr:
        return _err(terr)
    # 服务端归一：客户端提交的批量改动同样不可信，落库前统一 lint 一遍，
    # 后面的模拟校验和实际写入都用这份修正后的值，两处保持一致。
    for ch in chs:
        ch["part"] = _lint_or_none(
            (ch.get("reference") or "").strip(), ch.get("op"), ch.get("part"))
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

    ds_revs = _downstream_revisions(downstream_node_id, downstream_revision)
    choices = _choice_map(downstream_node_id, reference, choice)
    locks = [(parent_id, _coerce_revision(revision, parent)), *ds_revs.items(),
             (child["id"], int(child["revision"]))]
    holder: dict = {}

    def _write():
        found = propagation.preview_insert_conflicts(conn, parent_id, chs)
        for conflict in found:
            picked = choices.get((conflict.downstream_node_id, conflict.reference))
            if picked not in ("keep", "take"):
                raise models.WriteAborted()
        new_id = models.insert_node_after(
            conn, parent_id, committed_at, message.strip(), description.strip(),
            commit=False)
        for ch in chs:
            ref = (ch.get("reference") or "").strip()
            propagation.apply_node_edit(
                conn, new_id, ref, ch.get("op"), ch.get("part"),
                actor_user_id=_actor_id(request), commit=False)
        for conflict in found:
            propagation.resolve_conflict(
                conn, conflict,
                choices[(conflict.downstream_node_id, conflict.reference)],
                actor_user_id=_actor_id(request), commit=False)
        holder["id"] = new_id

    try:
        models.guarded_write(conn, _actor_id(request), locks, _write)
    except models.WriteAborted:
        conflicts = _enriched_conflicts(
            conn, propagation.preview_insert_conflicts(conn, parent_id, chs))
        return templates.TemplateResponse(
            request, "_insert_conflict_modal.html",
            {"board_id": board_id, "parent_id": parent_id,
             "committed_at": committed_at, "message": message,
             "description": description, "changes": changes,
             "revision": int(models.get_node(conn, parent_id)["revision"]),
             "conflicts": conflicts},
            headers={"HX-Retarget": "#modal", "HX-Reswap": "innerHTML"})
    except models.NodeRevisionError as exc:
        return _err(str(exc))

    new_id = holder["id"]
    # flash 整体编码：含 # 与中文/✓，# 必须编码成 %23，否则被当作 URL fragment 截断
    flash = quote(f"✓ 已插入节点 #{new_id}")
    dest = f"/board/{board_id}/node/{new_id}?flash={flash}"
    if request.headers.get("HX-Request"):
        from fastapi.responses import Response
        return Response(status_code=204, headers={"HX-Redirect": dest})
    return RedirectResponse(dest, status_code=303)


@router.post("/board/{board_id}/node/{node_id}/copy-to-draft")
def copy_to_draft(request: Request, board_id: int, node_id: int,
                  revision: str = Form(None)):
    """一键复制某节点全部 changeset 到工作区草稿（覆盖合并），标题/说明仅当草稿为空时填入。

    策略（issue #57 实现要点）：
    · 复制范围 A：复制源节点 changeset（位号+op+part），不复制折叠后的继承值；
    · 撞车合并 B（覆盖）：草稿已有同位号 op 时以复制值覆盖，草稿独有修改保留；
    · 标题/说明 A：仅当草稿标题/说明为空时才填入源节点的标题与长文本说明。
    """
    conn = get_conn()
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node["parent_id"] is None:
        return PlainTextResponse("根节点没有修改可复制", status_code=400)
    ws = models.workspace_node(conn, board_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    if ws["id"] == node_id:
        return PlainTextResponse("不能复制草稿到自身", status_code=400)

    copied = {"n": 0}

    def _write():
        changes = models.get_changeset(conn, node_id)
        copied["n"] = len(changes)
        for ch in changes:
            part_val = None if ch["op"] == "remove" else ch["part"]
            propagation.apply_node_edit(
                conn, ws["id"], ch["reference"], ch["op"], part_val,
                actor_user_id=_actor_id(request), commit=False)
        # 标题/说明：仅当草稿对应字段为空时才填入（各自独立判断）
        fresh_ws = models.get_node(conn, ws["id"])
        cur_msg = (fresh_ws["message"] or "").strip()
        cur_desc = (fresh_ws["description"] or "").strip()
        src_msg = (node["message"] or "").strip()
        src_desc = (node["description"] or "").strip()
        new_msg = cur_msg if cur_msg else src_msg
        new_desc = cur_desc if cur_desc else src_desc
        if new_msg != cur_msg or new_desc != cur_desc:
            models.update_node_info(conn, ws["id"], new_msg, new_desc, commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(ws["id"], _coerce_revision(revision, ws))], _write)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username, fallback=f"/board/{board_id}/node/{ws['id']}")

    flash = quote(f"✓ 已复制 {copied['n']} 条修改到草稿", safe="")
    resp = PlainTextResponse("")
    resp.headers["HX-Redirect"] = f"/board/{board_id}/node/{ws['id']}?flash={flash}"
    return resp


@router.post("/board/{board_id}/commit")
def commit(request: Request, board_id: int, message: str = Form(...),
           description: str = Form(""), revision: str = Form(None)):
    conn = get_conn()
    ws = models.workspace_node(conn, board_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="单板不存在")

    def _write():
        models.commit_workspace(conn, board_id, message, description.strip(), commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(ws["id"], _coerce_revision(revision, ws))], _write)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username, fallback=f"/board/{board_id}/node/{ws['id']}")
    return RedirectResponse(f"/board/{board_id}?flash=✓ 已提交：{message}", status_code=303)


def _node_ts(node) -> str:
    """节点用于时间区间的时间戳：已提交/根节点用 committed_at，草稿用当下。"""
    return node["committed_at"] or _now()


def _node_option_label(n) -> str:
    """对比页节点下拉的选项文案。"""
    if not n["is_committed"]:
        return "工作区草稿"
    if n["parent_id"] is None:
        return "初始状态"
    return f"#{n['id']} {n['message'] or '(无说明)'}"


def _sibling_node(conn, sibling_ids: set[int], node_id: int):
    """取节点并校验其归属单板在可比较范围（siblings）内，否则 404。"""
    n = models.get_node(conn, node_id)
    if n is None or n["board_id"] not in sibling_ids:
        raise HTTPException(status_code=404, detail="节点不存在")
    return n


def _compare_side_label(n, b, cross: bool) -> str:
    """对比页/下载文件里一侧节点的可读描述：跨板时加板号前缀。"""
    prefix = f"板 {b['board_uid']} · " if cross else ""
    return prefix + _node_option_label(n)


@router.get("/board/{board_id}/compare")
def compare_nodes(request: Request, board_id: int, left: int | None = None, right: int | None = None):
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    if left is None:
        raise HTTPException(status_code=404, detail="缺少对比节点参数")
    # 可比较范围 = 同「单板名称 + PCB版本」下、当前用户看得见的兄弟单板
    siblings = [
        s for s in models.list_sibling_boards(conn, board_id)
        if s["id"] in models.visible_board_ids(conn, request.state.user["id"])
    ]
    sibling_ids = {s["id"] for s in siblings}
    ln = _sibling_node(conn, sibling_ids, left)
    if right is None:
        # 跨板入口只带 left：右侧默认取第一块其他兄弟单板的最新已提交节点
        other = next((s for s in siblings if s["id"] != ln["board_id"]), None)
        if other is None:
            raise HTTPException(status_code=404, detail="缺少对比节点参数")
        rn = models.get_node(
            conn, models.latest_committed_node_id(conn, other["id"]))
        right = rn["id"]
    else:
        rn = _sibling_node(conn, sibling_ids, right)
    if left == right:
        return RedirectResponse(
            f"/board/{ln['board_id']}?flash=不能和自己比", status_code=303)
    li, lc = models.get_chain(conn, left)
    ri, rc = models.get_chain(conn, right)
    left_bom = fold_bom(li, lc)
    right_bom = fold_bom(ri, rc)
    rows = compare.diff_boms(left_bom, right_bom)
    diff_rows = [r for r in rows if r["kind"] != "same"]
    same_rows = [r for r in rows if r["kind"] == "same"]
    counts = {k: sum(1 for r in rows if r["kind"] == k)
              for k in ("add", "modify", "remove", "same")}
    cross = ln["board_id"] != rn["board_id"]
    if cross:
        # 硬更改是单板维度的记录，跨板对比时时间区间语义不成立，不展示
        between = None
    else:
        hcs = [dict(h) for h in models.list_hard_changes(conn, ln["board_id"])]
        between = compare.hard_changes_between(hcs, _node_ts(ln), _node_ts(rn))
    boards_by_id = {s["id"]: s for s in siblings}
    # 板/节点下拉数据 + 各板默认节点（最新已提交），换板即用默认节点跳转
    selects, defaults = [], {}
    for s in siblings:
        defaults[s["id"]] = models.latest_committed_node_id(conn, s["id"])
        selects.append({
            "id": s["id"],
            "label": f"板 {s['board_uid']} · {s['bom_version']}",
            "nodes": [{"id": n["id"], "label": _node_option_label(n)}
                      for n in models.board_chain_nodes(conn, s["id"])],
        })
    left_board, right_board = boards_by_id[ln["board_id"]], boards_by_id[rn["board_id"]]

    return templates.TemplateResponse(request, "compare.html", {
        "board": board, "board_id": board_id,
        "left_node": ln, "right_node": rn,
        "left_board": left_board, "right_board": right_board,
        "left_label": _compare_side_label(ln, left_board, cross),
        "right_label": _compare_side_label(rn, right_board, cross),
        "left_options": next(s["nodes"] for s in selects if s["id"] == ln["board_id"]),
        "right_options": next(s["nodes"] for s in selects if s["id"] == rn["board_id"]),
        "cross": cross, "board_selects": selects, "board_defaults": defaults,
        "diff_rows": diff_rows, "same_rows": same_rows,
        "counts": counts, "hard_changes": between,
    })


def _compare_download_filename(board, left_label: str, right_label: str) -> str:
    """对比差异 CSV 的下载文件名：含单板与两侧节点描述，去掉文件系统/HTTP 头敏感字符。"""
    raw = "_".join(p for p in [
        board["board_name"], board["pcb_version"], "对比差异", left_label, right_label,
    ] if p)
    return (_safe_filename(raw) or "compare") + ".csv"


@router.get("/board/{board_id}/compare/download")
def download_compare_diff(request: Request, board_id: int, left: int, right: int):
    """下载对比页当前差异表的 CSV：只含有差异的行（新增/修改/不贴），不含相同行。"""
    conn = get_conn()
    board = models.get_board(conn, board_id)
    if board is None:
        raise HTTPException(status_code=404, detail="单板不存在")
    allowed = models.visible_board_ids(conn, request.state.user["id"])
    siblings = [
        s for s in models.list_sibling_boards(conn, board_id) if s["id"] in allowed
    ]
    sibling_ids = {s["id"] for s in siblings}
    ln = _sibling_node(conn, sibling_ids, left)
    rn = _sibling_node(conn, sibling_ids, right)
    boards_by_id = {s["id"]: s for s in siblings}
    left_board, right_board = boards_by_id[ln["board_id"]], boards_by_id[rn["board_id"]]
    cross = ln["board_id"] != rn["board_id"]
    li, lc = models.get_chain(conn, left)
    ri, rc = models.get_chain(conn, right)
    rows = compare.diff_boms(fold_bom(li, lc), fold_bom(ri, rc))
    diff_rows = [r for r in rows if r["kind"] != "same"]
    left_label = _compare_side_label(ln, left_board, cross)
    right_label = _compare_side_label(rn, right_board, cross)
    csv_text = diff_to_csv(diff_rows, left_label, right_label)
    # UTF-8 带 BOM，Excel/WPS 打开中文不乱码
    body = ("﻿" + csv_text).encode("utf-8")
    filename = _compare_download_filename(board, left_label, right_label)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


# ---------- 节点附件 ----------

def _require_node(conn, board_id, node_id):
    node = models.get_node(conn, node_id)
    if node is None or node["board_id"] != board_id:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


def _attach_ctx(conn, board_id, node):
    return {
        "board": models.get_board(conn, board_id), "board_id": board_id, "node": node,
        "attachments": models.list_node_attachments(conn, node["id"]),
    }


@router.get("/board/{board_id}/node/{node_id}/attachments")
def attachments_list(request: Request, board_id: int, node_id: int):
    """附件区域局部片段（HTMX 局部刷新返回）。"""
    conn = get_conn()
    node = _require_node(conn, board_id, node_id)
    return templates.TemplateResponse(request, "_attachments.html", _attach_ctx(conn, board_id, node))


@router.post("/board/{board_id}/node/{node_id}/attachments")
async def attachments_upload(request: Request, board_id: int, node_id: int,
                              revision: str = Form(None),
                              files: list[UploadFile] = File(default=[])):
    """上传一个或多个附件，落盘到 uploads/<board>/<node>/ 并刷新附件区域。"""
    conn = get_conn()
    node = _require_node(conn, board_id, node_id)
    node_id = node["id"]
    reals = [f for f in files if f.filename]
    blobs = [(f.filename, await f.read()) for f in reals]
    oversized = []
    to_save = []
    for name, data in blobs:
        if len(data) > attachments.MAX_ATTACHMENT_BYTES:
            oversized.append(name)
        else:
            to_save.append((name, data))
    saved_rels: list[str] = []

    def _write():
        for name, data in to_save:
            stored = attachments.make_stored_name(name)
            rel = attachments.rel_path(board_id, node_id, stored)
            storage.save_attachment(rel, data)
            saved_rels.append(rel)
            models.add_node_attachment(conn, node_id, name, rel, commit=False)

    if to_save:
        try:
            models.guarded_write(
                conn, _actor_id(request),
                [(node_id, _coerce_revision(revision, node))], _write)
        except models.NodeRevisionError as exc:
            storage.delete_files(saved_rels)
            return _stale(request, exc.username)
        except Exception:
            storage.delete_files(saved_rels)
            raise
    saved = len(to_save)
    if oversized:
        limit_mb = attachments.MAX_ATTACHMENT_BYTES // (1024 * 1024)
        msg = (f"✓ 已上传 {saved} 个；{len(oversized)} 个超过 {limit_mb}MB 未上传："
               f"{', '.join(oversized)}")
    else:
        msg = f"✓ 已上传 {saved} 个附件" if saved else "未选择文件"
    node = models.get_node(conn, node_id)
    return templates.TemplateResponse(
        request, "_attachments.html", _attach_ctx(conn, board_id, node),
        headers={"HX-Trigger": _trigger(msg, node if saved else None)})


@router.get("/board/{board_id}/node/{node_id}/attachments/{aid}/download")
def attachments_download(board_id: int, node_id: int, aid: int):
    conn = get_conn()
    node = _require_node(conn, board_id, node_id)
    row = models.get_node_attachment(conn, aid)
    if row is None or row["node_id"] != node["id"]:
        raise HTTPException(status_code=404, detail="附件不存在")
    full = os.path.join(storage.upload_dir(), row["storage_path"])
    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail="附件文件缺失")
    fname = attachments.safe_filename(row["filename"])
    # 流式返回，避免大文件被整份读进内存
    return FileResponse(
        full,
        media_type="application/octet-stream",
        headers={"Content-Disposition": _content_disposition(fname)},
    )


@router.post("/board/{board_id}/node/{node_id}/attachments/{aid}/delete")
def attachments_delete(request: Request, board_id: int, node_id: int, aid: int,
                       revision: str = Form(None)):
    """删除一个附件（DB 行 + 磁盘文件），刷新附件区域。"""
    conn = get_conn()
    node = _require_node(conn, board_id, node_id)
    row = models.get_node_attachment(conn, aid)
    if row is None or row["node_id"] != node["id"]:
        raise HTTPException(status_code=404, detail="附件不存在")
    fname = row["filename"]
    removed = {"path": None}

    def _write():
        removed["path"] = models.delete_node_attachment(conn, aid, commit=False)

    try:
        models.guarded_write(
            conn, _actor_id(request),
            [(node["id"], _coerce_revision(revision, node))], _write)
    except models.NodeRevisionError as exc:
        return _stale(request, exc.username)
    if removed["path"]:
        storage.delete_files([removed["path"]])
    msg = f"✓ 已删除附件 {fname}"
    node = models.get_node(conn, node["id"])
    return templates.TemplateResponse(
        request, "_attachments.html", _attach_ctx(conn, board_id, node),
        headers={"HX-Trigger": _trigger(msg, node)})
