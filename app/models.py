from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets
import sqlite3

from app.auth import hash_password, normalize_username, session_days
from app.csv_import import CsvEntry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 层级 & 初始 BOM ----------

def create_bom_version(
    conn: sqlite3.Connection,
    board_name: str, pcb_version: str, bom_version: str,
    entries: list[CsvEntry],
    *,
    owner_user_id: int | None = None,
) -> None:
    """建立一个 BOM版本 的初始 BOM。三元组重复时抛 ValueError。

    owner_user_id 给出时，在同一事务里写下 bom_versions 负责人。
    三元组已经有负责人则不改。
    """
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
    if owner_user_id is not None:
        ensure_bom_version(conn, board_name, pcb_version, bom_version, owner_user_id)
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


def ensure_bom_version(conn, board_name, pcb_version, bom_version, owner_user_id) -> int:
    """已有三元组则返回 id，不改负责人。没有则插入一行。不提交。"""
    row = conn.execute(
        "SELECT id FROM bom_versions"
        " WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO bom_versions(board_name,pcb_version,bom_version,owner_user_id,created_at)"
        " VALUES(?,?,?,?,?)",
        (board_name, pcb_version, bom_version, owner_user_id, _now()),
    )
    return cur.lastrowid


def get_bom_version_row(conn, board_name, pcb_version, bom_version):
    return conn.execute(
        "SELECT * FROM bom_versions WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    ).fetchone()


def bom_version_exists(conn, board_name, pcb_version, bom_version) -> bool:
    """三元组在初始 BOM 或 bom_versions 里出现过，就算已存在。"""
    if get_bom_version_row(conn, board_name, pcb_version, bom_version):
        return True
    return conn.execute(
        "SELECT 1 FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
        (board_name, pcb_version, bom_version),
    ).fetchone() is not None


# ---------- 单板 & 节点 ----------

def create_board(
    conn, board_name, pcb_version, bom_version, board_uid, *,
    owner_user_id: int | None = None,
) -> int:
    """新建单板ID：建层级行 + 根节点（已提交）+ 空工作区草稿。返回 board_id。

    owner_user_id 非空时，在同一事务里写下 owner 行。不传则不写成员
    （直接插表的旧测试保持可用；网页创建单板的路由会传）。
    """
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
    if owner_user_id is not None:
        conn.execute(
            "INSERT INTO board_members(board_id,user_id,role,created_at) VALUES(?,?,?,?)",
            (board_id, owner_user_id, "owner", now),
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


def all_committed_nodes(conn) -> list[sqlite3.Row]:
    """全库已提交节点的 (id, board_id)，供哈希解析枚举。"""
    return conn.execute(
        "SELECT id, board_id FROM nodes WHERE is_committed=1"
    ).fetchall()


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


def clear_changes(conn, node_id) -> int:
    """清空某节点的整个 changeset，返回删掉的条数（一键撤销草稿全部修改用）。"""
    cur = conn.execute("DELETE FROM node_changes WHERE node_id=?", (node_id,))
    conn.commit()
    return cur.rowcount


def delete_node(conn, node_id) -> list[str]:
    """物理删除一个节点：把它的子节点重接到它的父节点，再删其 changeset、附件、节点行本身。
    线性链中至多一个子节点；草稿也按子节点重接（4-A）。

    被删节点自身的历史审计日志**重挂到父节点而非删除**（append-only）：受
    `edit_log.node_id NOT NULL` + 外键所限不能悬挂，重挂到父节点后与同样挂在父节点的
    `delete_node` 事件相邻，仍可追溯。审计的「删除事件 / 传播日志」由 propagation 层负责，
    这里只做结构删除。根节点不可删（无父节点），调用前应已校验，这里再 fail-fast。

    返回被删节点附件的 storage_path 列表（供调用方删除磁盘文件）。"""
    node = get_node(conn, node_id)
    assert node["parent_id"] is not None, "不能删除根节点（无父节点）"
    parent_id = node["parent_id"]
    paths = [r["storage_path"] for r in conn.execute(
        "SELECT storage_path FROM node_attachments WHERE node_id=?", (node_id,)
    ).fetchall()]
    conn.execute(
        "UPDATE nodes SET parent_id=? WHERE parent_id=?", (parent_id, node_id)
    )
    conn.execute("DELETE FROM node_changes WHERE node_id=?", (node_id,))
    conn.execute("DELETE FROM node_attachments WHERE node_id=?", (node_id,))
    conn.execute("UPDATE edit_log SET node_id=? WHERE node_id=?", (parent_id, node_id))
    conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
    conn.commit()
    return paths


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


def list_sibling_boards(conn, board_id) -> list[sqlite3.Row]:
    """同「单板名称 + PCB版本」下的全部单板（含自身、跨 BOM 版本），供跨板对比。"""
    return conn.execute(
        "SELECT s.* FROM boards_hierarchy s"
        " JOIN boards_hierarchy b ON b.id=?"
        " WHERE s.board_name=b.board_name AND s.pcb_version=b.pcb_version"
        " ORDER BY s.bom_version, s.board_uid",
        (board_id,),
    ).fetchall()


def board_chain_nodes(conn, board_id) -> list[sqlite3.Row]:
    """按链序（根 → 工作区草稿）返回该单板全部节点。"""
    return _ancestry(conn, workspace_node(conn, board_id)["id"])


def latest_committed_node_id(conn, board_id) -> int:
    """链末最新已提交节点 id（= 工作区草稿的父节点；无提交时为根节点）。"""
    return workspace_node(conn, board_id)["parent_id"]


def board_uid_exists(conn, board_name, pcb_version, bom_version, board_uid) -> bool:
    """同一 BOM 版本内是否已存在该 board_uid（用于新建单板去重）。"""
    return conn.execute(
        "SELECT 1 FROM boards_hierarchy"
        " WHERE board_name=? AND pcb_version=? AND bom_version=? AND board_uid=?"
        " LIMIT 1",
        (board_name, pcb_version, bom_version, board_uid),
    ).fetchone() is not None


def delete_board(conn, board_id) -> list[str]:
    """删除单板及其节点、changeset、审计日志、硬更改（级联）。返回被删图片 filename。"""
    node_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM nodes WHERE board_id=?", (board_id,)
    ).fetchall()]
    if node_ids:
        ph = ",".join("?" * len(node_ids))
        conn.execute(f"DELETE FROM edit_log WHERE node_id IN ({ph})", node_ids)
        conn.execute(f"DELETE FROM node_changes WHERE node_id IN ({ph})", node_ids)
        conn.execute(f"DELETE FROM node_attachments WHERE node_id IN ({ph})", node_ids)
        conn.execute("DELETE FROM nodes WHERE board_id=?", (board_id,))
    hc_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM hard_changes WHERE board_id=?", (board_id,)
    ).fetchall()]
    filenames: list[str] = []
    if hc_ids:
        ph = ",".join("?" * len(hc_ids))
        filenames = [r["filename"] for r in conn.execute(
            f"SELECT filename FROM hard_change_images WHERE hard_change_id IN ({ph})",
            hc_ids,
        ).fetchall()]
        conn.execute(f"DELETE FROM hard_change_images WHERE hard_change_id IN ({ph})", hc_ids)
        conn.execute("DELETE FROM hard_changes WHERE board_id=?", (board_id,))
    conn.execute("DELETE FROM boards_hierarchy WHERE id=?", (board_id,))
    conn.commit()
    return filenames


def delete_bom_version(conn, board_name, pcb_version, bom_version) -> list[str]:
    """删除 BOM 版本下所有单板及初始 BOM（级联）。返回被删图片 filename。

    范围内已经没有单板、也没有初始 BOM 时，删掉对应的 bom_versions 行。
    """
    boards = conn.execute(
        "SELECT id FROM boards_hierarchy WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    ).fetchall()
    filenames: list[str] = []
    for b in boards:
        filenames += delete_board(conn, b["id"])
    conn.execute(
        "DELETE FROM initial_bom WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    )
    _drop_empty_bom_version(conn, board_name, pcb_version, bom_version)
    conn.commit()
    return filenames


def delete_board_name(conn, board_name) -> list[str]:
    """删除单板名称下所有 BOM 版本及其数据（级联）。返回被删图片 filename。"""
    versions = conn.execute(
        """SELECT DISTINCT pcb_version, bom_version FROM initial_bom WHERE board_name=?
           UNION
           SELECT DISTINCT pcb_version, bom_version FROM boards_hierarchy WHERE board_name=?
           UNION
           SELECT DISTINCT pcb_version, bom_version FROM bom_versions WHERE board_name=?""",
        (board_name, board_name, board_name),
    ).fetchall()
    filenames: list[str] = []
    for v in versions:
        filenames += delete_bom_version(conn, board_name, v["pcb_version"], v["bom_version"])
    conn.commit()
    return filenames


def _drop_empty_bom_version(conn, board_name, pcb_version, bom_version) -> None:
    """没有单板、也没有初始 BOM 时删掉负责人行。不提交。"""
    still = conn.execute(
        """SELECT 1 FROM boards_hierarchy
           WHERE board_name=? AND pcb_version=? AND bom_version=?
           UNION ALL
           SELECT 1 FROM initial_bom
           WHERE board_name=? AND pcb_version=? AND bom_version=?
           LIMIT 1""",
        (board_name, pcb_version, bom_version, board_name, pcb_version, bom_version),
    ).fetchone()
    if still:
        return
    conn.execute(
        "DELETE FROM bom_versions WHERE board_name=? AND pcb_version=? AND bom_version=?",
        (board_name, pcb_version, bom_version),
    )


def _version_name_taken(conn, board_name, pcb_version, bom_version) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM boards_hierarchy"
            " WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
            (board_name, pcb_version, bom_version),
        ).fetchone()
        or conn.execute(
            "SELECT 1 FROM initial_bom"
            " WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
            (board_name, pcb_version, bom_version),
        ).fetchone()
        or conn.execute(
            "SELECT 1 FROM bom_versions"
            " WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
            (board_name, pcb_version, bom_version),
        ).fetchone()
    )


def board_name_rename_error(conn, old, new) -> str | None:
    """重名时返回给页面的中文原因。新旧相同不算错。"""
    if new == old:
        return None
    exists = (
        conn.execute("SELECT 1 FROM boards_hierarchy WHERE board_name=? LIMIT 1", (new,)).fetchone()
        or conn.execute("SELECT 1 FROM initial_bom WHERE board_name=? LIMIT 1", (new,)).fetchone()
        or conn.execute("SELECT 1 FROM bom_versions WHERE board_name=? LIMIT 1", (new,)).fetchone()
    )
    if exists:
        return f"单板名称「{new}」已存在"
    return None


def pcb_version_rename_error(conn, board_name, old, new) -> str | None:
    if new == old:
        return None
    exists = (
        conn.execute(
            "SELECT 1 FROM boards_hierarchy WHERE board_name=? AND pcb_version=? LIMIT 1",
            (board_name, new)).fetchone()
        or conn.execute(
            "SELECT 1 FROM initial_bom WHERE board_name=? AND pcb_version=? LIMIT 1",
            (board_name, new)).fetchone()
        or conn.execute(
            "SELECT 1 FROM bom_versions WHERE board_name=? AND pcb_version=? LIMIT 1",
            (board_name, new)).fetchone()
    )
    if exists:
        return f"PCB 版本「{new}」已存在"
    return None


def bom_version_rename_error(conn, board_name, pcb_version, old, new) -> str | None:
    if new == old:
        return None
    if _version_name_taken(conn, board_name, pcb_version, new):
        return f"BOM 版本「{new}」已存在"
    return None


def rename_board_name(conn, old, new) -> None:
    """重命名单板名称：级联更新层级、初始 BOM 和版本负责人。同名冲突抛 ValueError。"""
    err = board_name_rename_error(conn, old, new)
    if err:
        raise ValueError(err)
    if new == old:
        return
    conn.execute("UPDATE boards_hierarchy SET board_name=? WHERE board_name=?", (new, old))
    conn.execute("UPDATE initial_bom SET board_name=? WHERE board_name=?", (new, old))
    conn.execute("UPDATE bom_versions SET board_name=? WHERE board_name=?", (new, old))
    conn.commit()


def rename_pcb_version(conn, board_name, old, new) -> None:
    """重命名 PCB 版本：级联该 board_name 下该 PCB 的所有行。冲突抛 ValueError。"""
    err = pcb_version_rename_error(conn, board_name, old, new)
    if err:
        raise ValueError(err)
    if new == old:
        return
    conn.execute("UPDATE boards_hierarchy SET pcb_version=? WHERE board_name=? AND pcb_version=?",
                 (new, board_name, old))
    conn.execute("UPDATE initial_bom SET pcb_version=? WHERE board_name=? AND pcb_version=?",
                 (new, board_name, old))
    conn.execute("UPDATE bom_versions SET pcb_version=? WHERE board_name=? AND pcb_version=?",
                 (new, board_name, old))
    conn.commit()


def rename_bom_version(conn, board_name, pcb_version, old, new) -> None:
    """重命名 BOM 版本：只动 (board_name, pcb_version, old) 三元组。冲突抛 ValueError。"""
    err = bom_version_rename_error(conn, board_name, pcb_version, old, new)
    if err:
        raise ValueError(err)
    if new == old:
        return
    conn.execute("UPDATE boards_hierarchy SET bom_version=?"
                 " WHERE board_name=? AND pcb_version=? AND bom_version=?",
                 (new, board_name, pcb_version, old))
    conn.execute("UPDATE initial_bom SET bom_version=?"
                 " WHERE board_name=? AND pcb_version=? AND bom_version=?",
                 (new, board_name, pcb_version, old))
    conn.execute("UPDATE bom_versions SET bom_version=?"
                 " WHERE board_name=? AND pcb_version=? AND bom_version=?",
                 (new, board_name, pcb_version, old))
    conn.commit()


def rename_board_uid(conn, board_id, new) -> None:
    """重命名单板 ID：只动该行。同 BOM 版本内重名抛 ValueError。"""
    row = get_board(conn, board_id)
    if row is None:
        raise ValueError("单板不存在")
    if new == row["board_uid"]:
        return
    if board_uid_exists(conn, row["board_name"], row["pcb_version"], row["bom_version"], new):
        raise ValueError(f"单板 ID「{new}」在该 BOM 版本下已存在")
    conn.execute("UPDATE boards_hierarchy SET board_uid=? WHERE id=?", (new, board_id))
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


def commit_workspace(conn, board_id, message, description="") -> int:
    """把工作区草稿翻成正式节点，新开空草稿，返回被提交节点 id。
    message=标题/提交说明，description=长文本说明，二者同时落到被提交节点行。"""
    draft = conn.execute(
        "SELECT * FROM nodes WHERE board_id=? AND is_committed=0 ORDER BY id DESC LIMIT 1",
        (board_id,),
    ).fetchone()
    now = _now()
    conn.execute(
        "UPDATE nodes SET is_committed=1, committed_at=?, message=?, description=? WHERE id=?",
        (now, message, description, draft["id"]),
    )
    conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,0,NULL)",
        (board_id, draft["id"], "", now),
    )
    conn.commit()
    return draft["id"]


def insert_node_after(conn, parent_id, committed_at, message="", description="") -> int:
    """在 parent_id 之后插入一个已提交节点，把 parent 原来的直接子节点改挂到新节点。

    用于「在此节点后插入变更节点」：新节点 committed_at 由调用方给定（须落在
    上一节点之后、下一节点之前，校验见 validation.validate_insert_time）。返回新节点 id。
    """
    parent = get_node(conn, parent_id)
    old_child = conn.execute(
        "SELECT id FROM nodes WHERE parent_id=?", (parent_id,)
    ).fetchone()
    new_id = conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,description,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,?,1,?)",
        (parent["board_id"], parent_id, message, description, _now(), committed_at),
    ).lastrowid
    if old_child is not None:
        conn.execute(
            "UPDATE nodes SET parent_id=? WHERE id=?", (new_id, old_child["id"])
        )
    conn.commit()
    return new_id


def update_node_info(conn, node_id, message, description) -> None:
    """更新节点的标题（提交说明）与长文本说明。不改 BOM 内容，不记审计日志。"""
    conn.execute(
        "UPDATE nodes SET message=?, description=? WHERE id=?",
        (message, description, node_id),
    )
    conn.commit()


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


def all_hard_changes(conn) -> list[sqlite3.Row]:
    """全库硬更改的 (id, board_id)，供哈希解析枚举。"""
    return conn.execute("SELECT id, board_id FROM hard_changes").fetchall()


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


# ---------- 节点附件 ----------

def add_node_attachment(conn, node_id, filename, storage_path) -> int:
    """记录一个附件元数据（不写文件，文件由 storage 层负责）。返回新行 id。"""
    cur = conn.execute(
        "INSERT INTO node_attachments(node_id,filename,storage_path,created_at)"
        " VALUES(?,?,?,?)",
        (node_id, filename, storage_path, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_node_attachments(conn, node_id) -> list[sqlite3.Row]:
    """按上传顺序返回某节点的附件元数据。"""
    return conn.execute(
        "SELECT * FROM node_attachments WHERE node_id=? ORDER BY id", (node_id,)
    ).fetchall()


def get_node_attachment(conn, attachment_id) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM node_attachments WHERE id=?", (attachment_id,)
    ).fetchone()


def delete_node_attachment(conn, attachment_id) -> str | None:
    """删除一个附件行，返回其 storage_path（供刷盘）；不存在返回 None。"""
    row = get_node_attachment(conn, attachment_id)
    if row is None:
        return None
    conn.execute("DELETE FROM node_attachments WHERE id=?", (attachment_id,))
    conn.commit()
    return row["storage_path"]


def board_attachment_paths(conn, board_id) -> list[str]:
    """单板下所有节点附件的 storage_path（供删单板时刷盘）。"""
    return [r["storage_path"] for r in conn.execute(
        "SELECT a.storage_path FROM node_attachments a"
        " JOIN nodes n ON n.id = a.node_id"
        " WHERE n.board_id=? ORDER BY a.id",
        (board_id,)
    ).fetchall()]


def node_ids_with_attachments(conn, board_id) -> set[int]:
    """该单板下含有附件的节点 id 集合（供状态图卡片标记回形针，避免逐节点查询）。"""
    return {r["node_id"] for r in conn.execute(
        "SELECT DISTINCT a.node_id FROM node_attachments a"
        " JOIN nodes n ON n.id = a.node_id"
        " WHERE n.board_id=?",
        (board_id,)
    ).fetchall()}


def board_attachment_paths_by_name(conn, board_name) -> list[str]:
    """单板名称下所有节点附件的 storage_path（供删整个单板名称时刷盘）。"""
    return [r["storage_path"] for r in conn.execute(
        "SELECT a.storage_path FROM node_attachments a"
        " JOIN nodes n ON n.id = a.node_id"
        " JOIN boards_hierarchy b ON b.id = n.board_id"
        " WHERE b.board_name=? ORDER BY a.id",
        (board_name,)
    ).fetchall()]


# ---------- 账号、会话、无主单板 ----------

# sqlite3.Connection 不能挂属性，也不能做弱引用。用 id 记「这次查找有没有顺延」，
# 中间件马上读走。查找一开始会覆盖，避免连接对象被回收后 id 复用到旧标记。
_session_slid: dict[int, bool] = {}


def session_was_slid(conn) -> bool:
    return _session_slid.pop(id(conn), False)

def create_user(conn, username: str, password: str, is_admin: bool) -> int:
    """创建用户。库为空时只允许系统管理员，避免出现没人能进管理、旧板也没人领。

    第一个用户创建成功后立刻认领当时还没有 owner 的单板。
    """
    name = normalize_username(username)
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if count == 0 and not is_admin:
        raise ValueError("第一个账号必须是系统管理员")
    cur = conn.execute(
        "INSERT INTO users(username,password_hash,is_admin,created_at) VALUES(?,?,?,?)",
        (name, hash_password(password), 1 if is_admin else 0, _now()),
    )
    user_id = cur.lastrowid
    if count == 0:
        assign_boards_without_owner(conn, user_id)
        assign_bom_versions_without_owner(conn, user_id)
    conn.commit()
    return user_id


def get_user_by_username(conn, username: str) -> sqlite3.Row | None:
    """按规范化后的登录名查找。格式不合法时返回 None，而不是抛给登录页。"""
    try:
        name = normalize_username(username)
    except ValueError:
        return None
    return conn.execute(
        "SELECT * FROM users WHERE username=?", (name,)
    ).fetchone()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, user_id: int, days: int = 14) -> str:
    """签发会话。返回 cookie 原文，库里只存 sha256。"""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sessions(user_id,token_hash,created_at,expires_at) VALUES(?,?,?,?)",
        (user_id, _token_hash(token), _now(), expires),
    )
    conn.commit()
    return token


def user_by_session_token(conn, token: str) -> sqlite3.Row | None:
    """用 cookie 原文换用户。过期、已停用、找不到都返回 None。

    距 expires_at 不足一半窗口时顺延。比较用与 `_now()` 相同的 UTC 字符串，
    秒精度、固定偏移，字符串序即时间序。    顺延与否用 session_was_slid(conn) 读，中间件据此决定要不要重写 cookie。
    """
    _session_slid[id(conn)] = False
    if not token:
        return None
    row = conn.execute(
        "SELECT u.id, u.username, u.password_hash, u.is_admin, u.disabled_at, u.created_at,"
        " s.id AS session_id, s.expires_at"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash=?",
        (_token_hash(token),),
    ).fetchone()
    if row is None or row["expires_at"] <= _now() or row["disabled_at"]:
        return None
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(row["expires_at"])
    window = timedelta(days=session_days())
    if expires - now < window / 2:
        new_exp = (now + window).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE sessions SET expires_at=? WHERE id=?",
            (new_exp, row["session_id"]),
        )
        conn.commit()
        _session_slid[id(conn)] = True
    return row


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
    conn.commit()


def delete_sessions_for_user(conn, user_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit()


def update_password(conn, user_id: int, password: str, *, keep_token: str | None = None) -> None:
    """更新密码哈希。keep_token 保留这一次请求的会话，其余删掉；不传则全部作废。"""
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (hash_password(password), user_id),
    )
    if keep_token is None:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    else:
        conn.execute(
            "DELETE FROM sessions WHERE user_id=? AND token_hash!=?",
            (user_id, _token_hash(keep_token)),
        )
    conn.commit()


def assign_boards_without_owner(conn, user_id: int) -> int:
    """给还没有 owner 行的单板插一行 owner。已有 owner 的不动。返回认领块数。"""
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM board_members WHERE role='owner'"
    ).fetchone()["n"]
    conn.execute(
        "INSERT INTO board_members(board_id,user_id,role,created_at)"
        " SELECT id, ?, 'owner', ? FROM boards_hierarchy b"
        " WHERE NOT EXISTS ("
        "   SELECT 1 FROM board_members m WHERE m.board_id=b.id AND m.role='owner'"
        " )",
        (user_id, _now()),
    )
    conn.commit()
    after = conn.execute(
        "SELECT COUNT(*) AS n FROM board_members WHERE role='owner'"
    ).fetchone()["n"]
    return after - before


def backfill_owners_if_unassigned(conn) -> int:
    """任何单板都还没有 owner 时，把全部无主单板认领给最早的未停用管理员。

    只要已经有一块板有了 owner，就什么都不做，避免启动过程把别人的板领走。
    """
    if conn.execute(
        "SELECT 1 FROM board_members WHERE role='owner' LIMIT 1"
    ).fetchone():
        return 0
    admin = _first_enabled_admin(conn)
    if admin is None:
        return 0
    return assign_boards_without_owner(conn, admin)


def _first_enabled_admin(conn) -> int | None:
    row = conn.execute(
        "SELECT id FROM users WHERE is_admin=1 AND disabled_at IS NULL ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def assign_bom_versions_without_owner(conn, user_id: int) -> int:
    """给还没有 bom_versions 行的三元组补负责人。已有负责人的不动。返回补上的个数。"""
    before = conn.execute("SELECT COUNT(*) AS n FROM bom_versions").fetchone()["n"]
    conn.execute(
        """INSERT INTO bom_versions(board_name,pcb_version,bom_version,owner_user_id,created_at)
           SELECT board_name, pcb_version, bom_version, ?, ? FROM (
             SELECT board_name, pcb_version, bom_version FROM initial_bom
             UNION
             SELECT board_name, pcb_version, bom_version FROM boards_hierarchy
           ) AS triples
           WHERE NOT EXISTS (
             SELECT 1 FROM bom_versions v
             WHERE v.board_name=triples.board_name
               AND v.pcb_version=triples.pcb_version
               AND v.bom_version=triples.bom_version
           )""",
        (user_id, _now()),
    )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) AS n FROM bom_versions").fetchone()["n"]
    return after - before


def backfill_bom_version_owners(conn) -> int:
    """尚无负责人的三元组归最早的未停用管理员。没有管理员时什么都不做。"""
    admin = _first_enabled_admin(conn)
    if admin is None:
        return 0
    return assign_bom_versions_without_owner(conn, admin)


def load_membership(conn, user_id: int, board_id: int) -> str | None:
    row = conn.execute(
        "SELECT role FROM board_members WHERE board_id=? AND user_id=?",
        (board_id, user_id),
    ).fetchone()
    return row["role"] if row else None


def board_owner_username(conn, board_id: int) -> str | None:
    row = conn.execute(
        """SELECT u.username FROM board_members m
           JOIN users u ON u.id=m.user_id
           WHERE m.board_id=? AND m.role='owner'""",
        (board_id,),
    ).fetchone()
    return row["username"] if row else None


def visible_board_ids(conn, user_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT board_id FROM board_members WHERE user_id=?", (user_id,)
    ).fetchall()
    return {r["board_id"] for r in rows}


def list_visible_boards(conn, user_id: int) -> list[sqlite3.Row]:
    """这个用户至少是 viewer 的单板，带上他在这块板上的角色。"""
    return conn.execute(
        """SELECT b.*, m.role FROM boards_hierarchy b
           JOIN board_members m ON m.board_id=b.id AND m.user_id=?
           ORDER BY b.board_name, b.pcb_version, b.bom_version, b.board_uid""",
        (user_id,),
    ).fetchall()


def list_version_choices(conn, user_id: int) -> list[sqlite3.Row]:
    """新建页下拉：自己已经是成员的版本，加上自己担任负责人的版本。"""
    return conn.execute(
        """SELECT DISTINCT board_name, pcb_version, bom_version FROM (
             SELECT b.board_name, b.pcb_version, b.bom_version
               FROM boards_hierarchy b
               JOIN board_members m ON m.board_id=b.id AND m.user_id=?
             UNION
             SELECT board_name, pcb_version, bom_version
               FROM bom_versions WHERE owner_user_id=?
           ) ORDER BY board_name, pcb_version, bom_version""",
        (user_id, user_id),
    ).fetchall()


def boards_in_scope(conn, *, board_name: str, pcb_version: str | None = None,
                    bom_version: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM boards_hierarchy WHERE board_name=?"
    params: list = [board_name]
    if pcb_version is not None:
        sql += " AND pcb_version=?"
        params.append(pcb_version)
    if bom_version is not None:
        sql += " AND bom_version=?"
        params.append(bom_version)
    sql += " ORDER BY pcb_version, bom_version, board_uid"
    return conn.execute(sql, params).fetchall()


def user_owns_all(conn, user_id: int, *, board_name: str,
                  pcb_version: str | None = None,
                  bom_version: str | None = None) -> bool:
    """范围内每一块单板都是这个用户的 owner。一块都没有时为假。"""
    boards = boards_in_scope(
        conn, board_name=board_name, pcb_version=pcb_version, bom_version=bom_version)
    if not boards:
        return False
    for board in boards:
        if load_membership(conn, user_id, board["id"]) != "owner":
            return False
    return True


def list_members(conn, board_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT u.id AS id, u.username, u.disabled_at, m.role, m.created_at
           FROM board_members m JOIN users u ON u.id=m.user_id
           WHERE m.board_id=?
           ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END,
                    u.username""",
        (board_id,),
    ).fetchall()


def add_member(conn, board_id: int, username: str, role: str) -> None:
    """把已有用户加为 editor 或 viewer。找不到、已停用、已是成员都抛 ValueError。"""
    if role not in ("editor", "viewer"):
        raise ValueError("只能添加为可编辑或只读")
    user = get_user_by_username(conn, username)
    if user is None:
        raise ValueError("没有这个用户")
    if user["disabled_at"]:
        raise ValueError("该账号已停用")
    existing = load_membership(conn, user["id"], board_id)
    if existing:
        raise ValueError("已经是这块单板的成员")
    conn.execute(
        "INSERT INTO board_members(board_id,user_id,role,created_at) VALUES(?,?,?,?)",
        (board_id, user["id"], role, _now()),
    )
    conn.commit()


def set_member_role(conn, board_id: int, user_id: int, role: str) -> None:
    """只在 editor 与 viewer 之间改。不能改 owner 那一行。"""
    if role not in ("editor", "viewer"):
        raise ValueError("角色只能是可编辑或只读")
    current = load_membership(conn, user_id, board_id)
    if current is None:
        raise ValueError("不是这块单板的成员")
    if current == "owner":
        raise ValueError("不能修改所有者的角色，请使用转让")
    conn.execute(
        "UPDATE board_members SET role=? WHERE board_id=? AND user_id=?",
        (role, board_id, user_id),
    )
    conn.commit()


def remove_member(conn, board_id: int, user_id: int) -> None:
    current = load_membership(conn, user_id, board_id)
    if current is None:
        raise ValueError("不是这块单板的成员")
    if current == "owner":
        raise ValueError("不能移除所有者")
    conn.execute(
        "DELETE FROM board_members WHERE board_id=? AND user_id=?",
        (board_id, user_id),
    )
    conn.commit()


def transfer_ownership(conn, board_id: int, from_user_id: int, to_user_id: int) -> None:
    """同一事务：原所有者降为 editor，目标成员升为 owner。

    先降级再升级，否则部分唯一索引会在中间态看到两名 owner。
    目标必须已是本板成员且未停用。
    """
    if from_user_id == to_user_id:
        raise ValueError("不能转让给自己")
    if load_membership(conn, from_user_id, board_id) != "owner":
        raise ValueError("只有所有者能转让")
    target_role = load_membership(conn, to_user_id, board_id)
    if target_role is None:
        raise ValueError("对方还不是这块单板的成员")
    target = conn.execute(
        "SELECT disabled_at FROM users WHERE id=?", (to_user_id,)
    ).fetchone()
    if target is None or target["disabled_at"]:
        raise ValueError("不能转给已停用的账号")
    try:
        # 先降级再升级，部分唯一索引才不会在中间态看到两名 owner。
        conn.execute(
            "UPDATE board_members SET role='editor' WHERE board_id=? AND user_id=? AND role='owner'",
            (board_id, from_user_id),
        )
        conn.execute(
            "UPDATE board_members SET role='owner' WHERE board_id=? AND user_id=?",
            (board_id, to_user_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------- 网页用户管理（阶段 3）----------

_admin_log = logging.getLogger("reflow.admin")


def list_users(conn) -> list[sqlite3.Row]:
    """全部账号。不选出密码哈希，避免管理页把它渲染出去。"""
    return conn.execute(
        "SELECT id, username, is_admin, disabled_at, created_at FROM users ORDER BY username"
    ).fetchall()


def count_enabled_admins(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE disabled_at IS NULL AND is_admin=1"
    ).fetchone()["n"]


def list_boards_with_disabled_owner(conn) -> list[sqlite3.Row]:
    """所有者已停用的单板。只有名称和登录名，不读 changeset，也不读初始 BOM。"""
    return conn.execute(
        """SELECT b.id, b.board_name, b.pcb_version, b.bom_version, b.board_uid,
                  u.username AS owner_username
           FROM boards_hierarchy b
           JOIN board_members m ON m.board_id=b.id AND m.role='owner'
           JOIN users u ON u.id=m.user_id
           WHERE u.disabled_at IS NOT NULL
           ORDER BY b.board_name, b.pcb_version, b.bom_version, b.board_uid"""
    ).fetchall()


def list_bom_versions_with_disabled_owner(conn) -> list[sqlite3.Row]:
    """负责人已停用的 BOM 版本。不读 initial_bom。"""
    return conn.execute(
        """SELECT v.id, v.board_name, v.pcb_version, v.bom_version,
                  u.username AS owner_username
           FROM bom_versions v
           JOIN users u ON u.id=v.owner_user_id
           WHERE u.disabled_at IS NOT NULL
           ORDER BY v.board_name, v.pcb_version, v.bom_version"""
    ).fetchall()


def _enabled_admin(conn, actor_id: int):
    actor = conn.execute(
        "SELECT is_admin, disabled_at FROM users WHERE id=?", (actor_id,)
    ).fetchone()
    if actor is None or not actor["is_admin"] or actor["disabled_at"]:
        raise ValueError("只有系统管理员能执行这个操作")
    return actor


def disable_user(conn, actor_id: int, target_id: int) -> None:
    """停用账号并吊销其全部会话。

    拒绝：操作者不是未停用的系统管理员；目标是最后一位仍可登录的系统管理员；
    停用自己。
    """
    _enabled_admin(conn, actor_id)
    target = conn.execute(
        "SELECT is_admin, disabled_at, username FROM users WHERE id=?", (target_id,)
    ).fetchone()
    if target is None:
        raise ValueError("没有这个用户")
    if target["disabled_at"]:
        raise ValueError("该账号已经停用")
    if target["is_admin"] and count_enabled_admins(conn) <= 1:
        raise ValueError("不能停用最后一位系统管理员")
    if actor_id == target_id:
        raise ValueError("不能停用自己")
    conn.execute("UPDATE users SET disabled_at=? WHERE id=?", (_now(), target_id))
    delete_sessions_for_user(conn, target_id)


def enable_user(conn, target_id: int) -> None:
    """重新启用。不恢复旧会话，对方要用密码再登录一次。"""
    row = conn.execute("SELECT id FROM users WHERE id=?", (target_id,)).fetchone()
    if row is None:
        raise ValueError("没有这个用户")
    conn.execute("UPDATE users SET disabled_at=NULL WHERE id=?", (target_id,))
    conn.commit()


def set_password(conn, target_id: int, password: str) -> None:
    """重置密码并吊销该用户的全部会话。自己改密仍走保留当前会话的那条路径。"""
    row = conn.execute("SELECT id FROM users WHERE id=?", (target_id,)).fetchone()
    if row is None:
        raise ValueError("没有这个用户")
    update_password(conn, target_id, password)


def _active_user_for_transfer(conn, username: str):
    user = get_user_by_username(conn, username)
    if user is None:
        raise ValueError("没有这个用户")
    if user["disabled_at"]:
        raise ValueError("不能转给已停用的账号")
    return user


def _log_force_transfer(actor_id: int, kind: str, object_id: int, new_user_id: int) -> None:
    """不写 edit_log，也不记录 BOM 内容。"""
    _admin_log.info(
        "强制转让 actor=%s kind=%s object=%s new_user=%s",
        actor_id, kind, object_id, new_user_id,
    )


def force_transfer_board(conn, actor_id: int, board_id: int, new_username: str) -> None:
    """管理员把单板转给另一名未停用用户。新用户成为 owner，原 owner 降为 editor。

    不读取 changeset，不返回 BOM。新用户原先不是成员时补一行 owner。
    """
    _enabled_admin(conn, actor_id)
    if get_board(conn, board_id) is None:
        raise ValueError("单板不存在")
    new_user = _active_user_for_transfer(conn, new_username)
    owner = conn.execute(
        "SELECT user_id FROM board_members WHERE board_id=? AND role='owner'",
        (board_id,),
    ).fetchone()
    if owner is None:
        raise ValueError("这块单板没有所有者")
    if owner["user_id"] == new_user["id"]:
        raise ValueError("对方已经是所有者")
    try:
        # 先降级再升级，部分唯一索引才不会在中间态看到两名 owner。
        conn.execute(
            "UPDATE board_members SET role='editor' WHERE board_id=? AND user_id=? AND role='owner'",
            (board_id, owner["user_id"]),
        )
        if load_membership(conn, new_user["id"], board_id):
            conn.execute(
                "UPDATE board_members SET role='owner' WHERE board_id=? AND user_id=?",
                (board_id, new_user["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO board_members(board_id,user_id,role,created_at) VALUES(?,?,?,?)",
                (board_id, new_user["id"], "owner", _now()),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    _log_force_transfer(actor_id, "单板", board_id, new_user["id"])


def force_transfer_bom_version(conn, actor_id: int, version_id: int, new_username: str) -> None:
    """只改 bom_versions.owner_user_id，不动各板成员。"""
    _enabled_admin(conn, actor_id)
    version = conn.execute(
        "SELECT id, owner_user_id FROM bom_versions WHERE id=?", (version_id,)
    ).fetchone()
    if version is None:
        raise ValueError("BOM 版本不存在")
    new_user = _active_user_for_transfer(conn, new_username)
    if version["owner_user_id"] == new_user["id"]:
        raise ValueError("对方已经是负责人")
    conn.execute(
        "UPDATE bom_versions SET owner_user_id=? WHERE id=?",
        (new_user["id"], version_id),
    )
    conn.commit()
    _log_force_transfer(actor_id, "BOM 版本", version_id, new_user["id"])
