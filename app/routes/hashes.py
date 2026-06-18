"""哈希跳转路由：/hash/{value} 按长/短哈希定位节点或硬更改并重定向。"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.main import get_conn
from app import models, hashing

router = APIRouter()

MIN_PREFIX = 4   # 类比 git：短哈希前缀至少 4 位才接受


@router.get("/hash/{value}")
def resolve_hash(value: str):
    prefix = value.strip().lower()
    if len(prefix) < MIN_PREFIX or any(c not in "0123456789abcdef" for c in prefix):
        raise HTTPException(status_code=404, detail="无效的哈希")
    conn = get_conn()
    matches: list[str] = []
    for n in models.all_committed_nodes(conn):
        if hashing.node_hash(n["id"]).startswith(prefix):
            matches.append(f"/board/{n['board_id']}/node/{n['id']}")
    for h in models.all_hard_changes(conn):
        if hashing.hard_change_hash(h["id"]).startswith(prefix):
            matches.append(f"/board/{h['board_id']}/hard-change/{h['id']}")
    if not matches:
        raise HTTPException(status_code=404, detail="未找到该哈希对应的节点")
    if len(matches) > 1:
        raise HTTPException(status_code=404, detail="哈希前缀不唯一，请提供更长的哈希")
    return RedirectResponse(matches[0], status_code=302)
