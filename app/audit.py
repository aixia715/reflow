import sqlite3
from app.models import _now


def record_edit(
    conn: sqlite3.Connection, node_id: int, reference: str,
    old_part: str | None, new_part: str | None,
    op: str, source: str, note: str | None = None,
) -> None:
    """追加一条审计记录，永不覆盖。source: 'direct' | 'propagated'。"""
    conn.execute(
        "INSERT INTO edit_log(node_id,reference,old_part,new_part,op,source,created_at,note)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (node_id, reference, old_part, new_part, op, source, _now(), note),
    )
    conn.commit()


def list_log(conn, node_id: int | None = None) -> list[sqlite3.Row]:
    if node_id is None:
        return conn.execute("SELECT * FROM edit_log ORDER BY id").fetchall()
    return conn.execute(
        "SELECT * FROM edit_log WHERE node_id=? ORDER BY id", (node_id,)
    ).fetchall()
