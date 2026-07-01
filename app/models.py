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
    """删除 BOM 版本下所有单板及初始 BOM（级联）。返回被删图片 filename。"""
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
    conn.commit()
    return filenames


def delete_board_name(conn, board_name) -> list[str]:
    """删除单板名称下所有 BOM 版本及其数据（级联）。返回被删图片 filename。"""
    versions = conn.execute(
        "SELECT DISTINCT pcb_version, bom_version FROM initial_bom WHERE board_name=?",
        (board_name,),
    ).fetchall()
    filenames: list[str] = []
    for v in versions:
        filenames += delete_bom_version(conn, board_name, v["pcb_version"], v["bom_version"])
    conn.commit()
    return filenames


def rename_board_name(conn, old, new) -> None:
    """重命名单板名称：级联更新两表所有匹配行。同名冲突抛 ValueError。"""
    if new == old:
        return
    exists = (
        conn.execute("SELECT 1 FROM boards_hierarchy WHERE board_name=? LIMIT 1",
                     (new,)).fetchone()
        or conn.execute("SELECT 1 FROM initial_bom WHERE board_name=? LIMIT 1",
                        (new,)).fetchone()
    )
    if exists:
        raise ValueError(f"单板名称「{new}」已存在")
    conn.execute("UPDATE boards_hierarchy SET board_name=? WHERE board_name=?", (new, old))
    conn.execute("UPDATE initial_bom SET board_name=? WHERE board_name=?", (new, old))
    conn.commit()


def rename_pcb_version(conn, board_name, old, new) -> None:
    """重命名 PCB 版本：级联该 board_name 下该 PCB 的所有行。冲突抛 ValueError。"""
    if new == old:
        return
    exists = (
        conn.execute("SELECT 1 FROM boards_hierarchy WHERE board_name=? AND pcb_version=? LIMIT 1",
                     (board_name, new)).fetchone()
        or conn.execute("SELECT 1 FROM initial_bom WHERE board_name=? AND pcb_version=? LIMIT 1",
                        (board_name, new)).fetchone()
    )
    if exists:
        raise ValueError(f"PCB 版本「{new}」已存在")
    conn.execute("UPDATE boards_hierarchy SET pcb_version=? WHERE board_name=? AND pcb_version=?",
                 (new, board_name, old))
    conn.execute("UPDATE initial_bom SET pcb_version=? WHERE board_name=? AND pcb_version=?",
                 (new, board_name, old))
    conn.commit()


def rename_bom_version(conn, board_name, pcb_version, old, new) -> None:
    """重命名 BOM 版本：只动 (board_name, pcb_version, old) 三元组。冲突抛 ValueError。"""
    if new == old:
        return
    exists = (
        conn.execute("SELECT 1 FROM boards_hierarchy"
                     " WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
                     (board_name, pcb_version, new)).fetchone()
        or conn.execute("SELECT 1 FROM initial_bom"
                        " WHERE board_name=? AND pcb_version=? AND bom_version=? LIMIT 1",
                        (board_name, pcb_version, new)).fetchone()
    )
    if exists:
        raise ValueError(f"BOM 版本「{new}」已存在")
    conn.execute("UPDATE boards_hierarchy SET bom_version=?"
                 " WHERE board_name=? AND pcb_version=? AND bom_version=?",
                 (new, board_name, pcb_version, old))
    conn.execute("UPDATE initial_bom SET bom_version=?"
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
