import sqlite3
from typing import NamedTuple

from app import models, audit
from app.bom_engine import resolve_reference


class Conflict(NamedTuple):
    downstream_node_id: int
    reference: str
    downstream_value: str | None
    corrected_value: str | None


def _append_committed_node(conn, board_id, parent_id, message) -> int:
    """测试/内部辅助：在 parent 之后追加一个已提交节点，返回其 id。"""
    now = models._now()
    return conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,1,?)",
        (board_id, parent_id, message, now, now),
    ).lastrowid


def _children_in_order(conn, board_id, start_node_id) -> list[sqlite3.Row]:
    """返回 start_node_id 之后（不含）沿子链的节点行，按链顺序。"""
    nodes = models.list_nodes(conn, board_id)        # 按 id 升序 = 链顺序
    after = []
    seen_start = False
    for n in nodes:
        if seen_start:
            after.append(n)
        if n["id"] == start_node_id:
            seen_start = True
    return after


def _resolved_value(conn, node_id, reference) -> str | None:
    initial, chain = models.get_chain(conn, node_id)
    return resolve_reference(initial, chain, reference)


def _detect_downstream_conflicts(conn, node, reference, corrected_part) -> list[Conflict]:
    """检测某节点修正后，下游第一个显式节点是否冲突（不写当前节点 changeset）。
    corrected_part 是本次修正后该位号在被编辑节点的解析值（remove 时为 None）。"""
    for child in _children_in_order(conn, node["board_id"], node["id"]):
        if models.get_change(conn, child["id"], reference) is not None:
            downstream_value = _resolved_value(conn, child["id"], reference)
            return [Conflict(child["id"], reference, downstream_value, corrected_part)]
    return []


def apply_node_edit(conn, node_id, reference, op, part) -> list[Conflict]:
    """编辑某节点某位号（修正记录），落库 + 记 direct 日志，返回冲突列表（最多一个）。
    op: 'add'|'modify'|'remove'；remove 时 part 传 None。"""
    old_value = _resolved_value(conn, node_id, reference)
    models.set_change(conn, node_id, reference, op, part)
    new_value = None if op == "remove" else part
    audit.record_edit(conn, node_id, reference, old_value, new_value, op, "direct")

    node = models.get_node(conn, node_id)
    corrected = _resolved_value(conn, node_id, reference)
    return _detect_downstream_conflicts(conn, node, reference, corrected)


def resolve_conflict(conn, conflict: Conflict, choice: str) -> None:
    """choice='keep' 保留下游值（什么都不做）；'take' 采用修正值并向后传播。"""
    if choice == "take":
        old_value = conflict.downstream_value
        models.delete_change(conn, conflict.downstream_node_id, conflict.reference)
        new_value = conflict.corrected_value
        op = "remove" if new_value is None else "modify"
        audit.record_edit(
            conn, conflict.downstream_node_id, conflict.reference,
            old_value, new_value, op, "propagated",
        )
    # 'keep'：不动
