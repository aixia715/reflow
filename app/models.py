from datetime import datetime, timezone
import sqlite3

from app.csv_import import CsvEntry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 层级 & 初始 BOM ----------

def create_bom_version(
    conn: sqlite3.Connection,
    board_name: str, pcb_version: str, bom_version: str,
    entries: list[CsvEntry],
) -> None:
    """建立一个 BOM版本 的初始 BOM。三元组重复时抛 ValueError。"""
    exists = conn.execute(
        "SELECT 1 FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
        (board_name, pcb_version, bom_version),
    ).fetchone()
    if exists:
        raise ValueError("该 (单板名称, PCB版本, BOM版本) 已存在")
    conn.executemany(
        "INSERT INTO initial_bom(board_name,pcb_version,bom_version,reference,part)"
        " VALUES(?,?,?,?,?)",
        [(board_name, pcb_version, bom_version, e.reference, e.part) for e in entries],
    )
    conn.commit()


def get_initial_bom(conn, board_name, pcb_version, bom_version) -> dict[str, str]:
    rows = conn.execute(
        "SELECT reference, part FROM initial_bom"
        " WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    )
    return {r["reference"]: r["part"] for r in rows}


def list_bom_versions(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT DISTINCT board_name, pcb_version, bom_version FROM initial_bom"
        " ORDER BY board_name, pcb_version, bom_version"
    ).fetchall()


# ---------- 单板 & 节点 ----------

def create_board(conn, board_name, pcb_version, bom_version, board_uid) -> int:
    """新建单板ID：建层级行 + 根节点（已提交）+ 空工作区草稿。返回 board_id。"""
    cur = conn.execute(
        "INSERT INTO boards_hierarchy(board_name,pcb_version,bom_version,board_uid)"
        " VALUES(?,?,?,?)",
        (board_name, pcb_version, bom_version, board_uid),
    )
    board_id = cur.lastrowid
    now = _now()
    root = conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,1,?)",
        (board_id, None, "初始 BOM", now, now),
    ).lastrowid
    conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,0,NULL)",
        (board_id, root, "", now),
    )
    conn.commit()
    return board_id


def get_board(conn, board_id) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM boards_hierarchy WHERE id=?", (board_id,)
    ).fetchone()


def list_nodes(conn, board_id) -> list[sqlite3.Row]:
    """按 id 升序返回该单板所有节点（线性链，含工作区草稿）。"""
    return conn.execute(
        "SELECT * FROM nodes WHERE board_id=? ORDER BY id", (board_id,)
    ).fetchall()


def get_node(conn, node_id) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()


# ---------- changeset ----------

def set_change(conn, node_id, reference, op, part) -> None:
    """新增或覆盖某节点对某位号的显式操作（UNIQUE(node_id,reference) upsert）。"""
    conn.execute(
        "INSERT INTO node_changes(node_id,reference,op,part) VALUES(?,?,?,?)"
        " ON CONFLICT(node_id,reference) DO UPDATE SET op=excluded.op, part=excluded.part",
        (node_id, reference, op, part),
    )
    conn.commit()


def delete_change(conn, node_id, reference) -> None:
    conn.execute(
        "DELETE FROM node_changes WHERE node_id=? AND reference=?", (node_id, reference)
    )
    conn.commit()


def get_changeset(conn, node_id) -> list[dict]:
    rows = conn.execute(
        "SELECT reference, op, part FROM node_changes WHERE node_id=? ORDER BY id",
        (node_id,),
    )
    return [{"reference": r["reference"], "op": r["op"], "part": r["part"]} for r in rows]


def node_summaries(conn, board_id) -> dict[int, list[dict]]:
    """每个节点的 changeset 摘要 {node_id: [{'reference','op'}, ...]}（节点内按写入顺序）。"""
    rows = conn.execute(
        "SELECT n.id AS node_id, c.reference, c.op FROM nodes n"
        " LEFT JOIN node_changes c ON c.node_id = n.id"
        " WHERE n.board_id = ? ORDER BY n.id, c.id",
        (board_id,),
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["node_id"], [])
        if r["reference"] is not None:
            out[r["node_id"]].append({"reference": r["reference"], "op": r["op"]})
    return out


def get_change(conn, node_id, reference) -> dict | None:
    r = conn.execute(
        "SELECT reference, op, part FROM node_changes WHERE node_id=? AND reference=?",
        (node_id, reference),
    ).fetchone()
    return {"reference": r["reference"], "op": r["op"], "part": r["part"]} if r else None


def list_boards(conn, board_name, pcb_version, bom_version) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM boards_hierarchy WHERE board_name=? AND pcb_version=? AND bom_version=?"
        " ORDER BY board_uid",
        (board_name, pcb_version, bom_version),
    ).fetchall()


def board_uid_exists(conn, board_name, pcb_version, bom_version, board_uid) -> bool:
    """同一 BOM 版本内是否已存在该 board_uid（用于新建单板去重）。"""
    return conn.execute(
        "SELECT 1 FROM boards_hierarchy"
        " WHERE board_name=? AND pcb_version=? AND bom_version=? AND board_uid=?"
        " LIMIT 1",
        (board_name, pcb_version, bom_version, board_uid),
    ).fetchone() is not None


def delete_board(conn, board_id) -> None:
    """删除单板及其全部节点、changeset、审计日志（级联）。"""
    node_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM nodes WHERE board_id=?", (board_id,)
    ).fetchall()]
    if node_ids:
        ph = ",".join("?" * len(node_ids))
        conn.execute(f"DELETE FROM edit_log WHERE node_id IN ({ph})", node_ids)
        conn.execute(f"DELETE FROM node_changes WHERE node_id IN ({ph})", node_ids)
        conn.execute("DELETE FROM nodes WHERE board_id=?", (board_id,))
    conn.execute("DELETE FROM boards_hierarchy WHERE id=?", (board_id,))
    conn.commit()


def delete_bom_version(conn, board_name, pcb_version, bom_version) -> None:
    """删除 BOM 版本下的所有单板及初始 BOM（级联）。"""
    boards = conn.execute(
        "SELECT id FROM boards_hierarchy WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    ).fetchall()
    for b in boards:
        delete_board(conn, b["id"])
    conn.execute(
        "DELETE FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    )
    conn.commit()


def delete_board_name(conn, board_name) -> None:
    """删除单板名称下所有 BOM 版本及其数据（级联）。"""
    versions = conn.execute(
        "SELECT DISTINCT pcb_version, bom_version FROM initial_bom WHERE board_name=?",
        (board_name,),
    ).fetchall()
    for v in versions:
        delete_bom_version(conn, board_name, v["pcb_version"], v["bom_version"])
    conn.commit()


def update_initial_bom(conn, board_name, pcb_version, bom_version, reference, part) -> None:
    """修正根节点初始 BOM 的某位号（part=None 表示删除该位号）。"""
    if part is None:
        conn.execute(
            "DELETE FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=? AND reference=?",
            (board_name, pcb_version, bom_version, reference),
        )
    else:
        conn.execute(
            "INSERT INTO initial_bom(board_name,pcb_version,bom_version,reference,part)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(board_name,pcb_version,bom_version,reference) DO UPDATE SET part=excluded.part",
            (board_name, pcb_version, bom_version, reference, part),
        )
    conn.commit()


def commit_workspace(conn, board_id, message) -> int:
    """把工作区草稿翻成正式节点，新开空草稿，返回被提交节点 id。"""
    draft = conn.execute(
        "SELECT * FROM nodes WHERE board_id=? AND is_committed=0 ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()
    now = _now()
    conn.execute(
        "UPDATE nodes SET is_committed=1, committed_at=?, message=? WHERE id=?",
        (now, message, draft["id"]),
    )
    conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,0,NULL)",
        (board_id, draft["id"], "", now),
    )
    conn.commit()
    return draft["id"]


def workspace_node(conn, board_id) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM nodes WHERE board_id=? AND is_committed=0 ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()


def list_board_log(conn, board_id, reference=None, node_id=None) -> list[sqlite3.Row]:
    """单板全链审计日志（带节点说明），可按位号模糊/节点过滤，最新在前。"""
    sql = ("SELECT l.*, n.message AS node_message, n.is_committed AS node_committed"
           " FROM edit_log l JOIN nodes n ON n.id = l.node_id WHERE n.board_id = ?")
    args: list = [board_id]
    if reference:
        sql += " AND l.reference LIKE ?"
        args.append(f"%{reference}%")
    if node_id:
        sql += " AND l.node_id = ?"
        args.append(node_id)
    sql += " ORDER BY l.id DESC"
    return conn.execute(sql, args).fetchall()


def _ancestry(conn, node_id) -> list[sqlite3.Row]:
    """从根到 node_id（含）的节点行列表。"""
    chain: list[sqlite3.Row] = []
    cur = get_node(conn, node_id)
    while cur is not None:
        chain.append(cur)
        cur = get_node(conn, cur["parent_id"]) if cur["parent_id"] else None
    chain.reverse()
    return chain


def get_chain(conn, node_id) -> tuple[dict[str, str], list[list[dict]]]:
    """返回 (该单板初始 BOM, 从根到 node_id 每节点的 changeset 列表)。"""
    ancestry = _ancestry(conn, node_id)
    board = get_board(conn, ancestry[0]["board_id"])
    initial = get_initial_bom(
        conn, board["board_name"], board["pcb_version"], board["bom_version"]
    )
    chain = [get_changeset(conn, n["id"]) for n in ancestry]
    return initial, chain


# ---------- 硬更改 ----------

def create_hard_change(conn, board_id, title, description, occurred_at, images) -> int:
    """新建硬更改 + 附图。images: [(存盘名, 原始名), ...]，按序写 sort_order。返回 id。"""
    now = _now()
    hc_id = conn.execute(
        "INSERT INTO hard_changes(board_id,title,description,occurred_at,created_at)"
        " VALUES(?,?,?,?,?)",
        (board_id, title, description, occurred_at, now),
    ).lastrowid
    for i, (fn, orig) in enumerate(images):
        conn.execute(
            "INSERT INTO hard_change_images"
            "(hard_change_id,filename,original_name,sort_order,created_at)"
            " VALUES(?,?,?,?,?)",
            (hc_id, fn, orig, i, now),
        )
    conn.commit()
    return hc_id


def get_hard_change(conn, hc_id) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM hard_changes WHERE id=?", (hc_id,)).fetchone()


def list_hard_changes(conn, board_id) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM hard_changes WHERE board_id=? ORDER BY occurred_at, id",
        (board_id,),
    ).fetchall()


def list_hard_change_images(conn, hc_id) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM hard_change_images WHERE hard_change_id=? ORDER BY sort_order, id",
        (hc_id,),
    ).fetchall()


def update_hard_change(conn, hc_id, title, description, occurred_at) -> None:
    conn.execute(
        "UPDATE hard_changes SET title=?, description=?, occurred_at=? WHERE id=?",
        (title, description, occurred_at, hc_id),
    )
    conn.commit()


def add_hard_change_images(conn, hc_id, images) -> None:
    """追加附图，sort_order 接续现有最大值。images: [(存盘名, 原始名), ...]。"""
    now = _now()
    start = conn.execute(
        "SELECT COALESCE(MAX(sort_order)+1, 0) AS n FROM hard_change_images"
        " WHERE hard_change_id=?",
        (hc_id,),
    ).fetchone()["n"]
    for i, (fn, orig) in enumerate(images):
        conn.execute(
            "INSERT INTO hard_change_images"
            "(hard_change_id,filename,original_name,sort_order,created_at)"
            " VALUES(?,?,?,?,?)",
            (hc_id, fn, orig, start + i, now),
        )
    conn.commit()


def delete_hard_change_images(conn, image_ids) -> list[str]:
    """按图片 id 删除行，返回被删的 filename 列表（供删盘）。"""
    if not image_ids:
        return []
    ph = ",".join("?" * len(image_ids))
    rows = conn.execute(
        f"SELECT filename FROM hard_change_images WHERE id IN ({ph})", image_ids
    ).fetchall()
    conn.execute(f"DELETE FROM hard_change_images WHERE id IN ({ph})", image_ids)
    conn.commit()
    return [r["filename"] for r in rows]


def delete_hard_change(conn, hc_id) -> list[str]:
    """删除硬更改及其附图行，返回被删的 filename 列表（供删盘）。"""
    rows = conn.execute(
        "SELECT filename FROM hard_change_images WHERE hard_change_id=?", (hc_id,)
    ).fetchall()
    conn.execute("DELETE FROM hard_change_images WHERE hard_change_id=?", (hc_id,))
    conn.execute("DELETE FROM hard_changes WHERE id=?", (hc_id,))
    conn.commit()
    return [r["filename"] for r in rows]
