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
