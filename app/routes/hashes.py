"""哈希跳转路由：
- /hash/{value} 按长/短哈希定位节点或硬更改并重定向（详情页链接用，出错走 404 页）。
- /hash-lookup?q=… header 输入框用的 HTMX 端点：命中→HX-Redirect 整页跳转；
  出错→200 + showToast 停留原页。
"""
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.main import get_conn
from app import models, hashing

router = APIRouter()

MIN_PREFIX = 4   # 类比 git：短哈希前缀至少 4 位才接受


def find_hash_target(conn, value: str, allowed_board_ids: set[int]):
    """按长/短哈希前缀定位节点或硬更改。只在 allowed_board_ids 里的板上匹配。

    看不见的板当不存在：唯一命中落在看不见的板上时，文案与「未找到」相同。
    返回 (错误消息, 目标路径)：成功时错误消息为 None、目标路径为 URL；
    失败时目标路径为 None、错误消息说明原因（无效/找不到/前缀不唯一）。
    """
    prefix = (value or "").strip().lower()
    if len(prefix) < MIN_PREFIX or any(c not in "0123456789abcdef" for c in prefix):
        return "无效的哈希（至少 4 位十六进制）", None
    matches: list[str] = []
    for n in models.all_committed_nodes(conn):
        if n["board_id"] not in allowed_board_ids:
            continue
        if hashing.node_hash(n["id"]).startswith(prefix):
            matches.append(f"/board/{n['board_id']}/node/{n['id']}")
    for h in models.all_hard_changes(conn):
        if h["board_id"] not in allowed_board_ids:
            continue
        if hashing.hard_change_hash(h["id"]).startswith(prefix):
            matches.append(f"/board/{h['board_id']}/hard-change/{h['id']}")
    if not matches:
        return "未找到该哈希对应的节点", None
    if len(matches) > 1:
        return "哈希前缀不唯一，请提供更长的哈希", None
    return None, matches[0]


def _allowed(conn, request: Request) -> set[int]:
    return models.visible_board_ids(conn, request.state.user["id"])


@router.get("/hash/{value}")
def resolve_hash(request: Request, value: str):
    """详情页哈希链接用：命中 302 重定向，出错抛 404 页。"""
    conn = get_conn()
    error, path = find_hash_target(conn, value, _allowed(conn, request))
    if error is not None:
        raise HTTPException(status_code=404, detail=error)
    return RedirectResponse(path, status_code=302)


@router.get("/hash-lookup")
def hash_lookup(request: Request, q: str = ""):
    """header 输入框用（HTMX）：命中→HX-Redirect 整页跳转；出错→toast 停留原页。"""
    conn = get_conn()
    error, path = find_hash_target(conn, q, _allowed(conn, request))
    if error is not None:
        return Response(
            status_code=200,
            headers={"HX-Trigger": json.dumps({"showToast": error})})
    return Response(status_code=204, headers={"HX-Redirect": path})
