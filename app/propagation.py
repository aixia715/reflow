import sqlite3
from typing import NamedTuple

from app import models, audit, storage
from app.bom_engine import resolve_reference


class Conflict(NamedTuple):
    downstream_node_id: int
    reference: str
    downstream_value: str | None
    corrected_value: str | None


def _children_in_order(conn, board_id, start_node_id) -> list[sqlite3.Row]:
    """返回 start_node_id 之后（不含）沿子链的节点行，按链顺序。

    沿 parent_id 链向后游走，而非依赖 id 顺序——这样无论节点的创建/提交
    顺序如何，结果都严格等于链顺序（线性链中每个节点至多一个子节点），
    且自然包含挂在末端的工作区草稿。
    """
    child_of = {
        n["parent_id"]: n
        for n in models.list_nodes(conn, board_id)
        if n["parent_id"] is not None
    }
    after = []
    cur = child_of.get(start_node_id)
    while cur is not None:
        after.append(cur)
        cur = child_of.get(cur["id"])
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
            # 下游显式值已等于修正值 → 整条下游本就解析为修正值，无需确认
            if downstream_value == corrected_part:
                return []
            return [Conflict(child["id"], reference, downstream_value, corrected_part)]
    return []


def apply_node_edit(conn, node_id, reference, op, part) -> list[Conflict]:
    """编辑某节点某位号（修正记录），落库 + 记 direct 日志，返回冲突列表（最多一个）。
    op: 'add'|'modify'|'remove'；remove 时 part 传 None。"""
    old_value = _resolved_value(conn, node_id, reference)
    models.set_change(conn, node_id, reference, op, part)
    new_value = None if op == "remove" else part
    audit.record_edit(conn, node_id, reference, old_value, new_value, op, "direct")

    # 刚写入的 changeset 是链末显式值，被编辑节点对该位号的解析值即 new_value
    node = models.get_node(conn, node_id)
    return _detect_downstream_conflicts(conn, node, reference, new_value)


def detect_delete_conflicts(conn, node_id) -> list[Conflict]:
    """删除某节点前，检测其下游因失去该节点 changeset 而解析值会变化的位号（1-A）。

    线性链中被删节点至多一个直接子节点 D。仅被删节点 changeset 里的位号可能变化：
    若 D 对该位号有显式 op → 被屏蔽，不变；否则 D 由「经被删节点」改为「继承父节点」，
    解析值有变即为一个冲突。返回 Conflict(D, ref, 删前值, 删后继承值)。
    """
    node = models.get_node(conn, node_id)
    downstream = _children_in_order(conn, node["board_id"], node_id)
    if not downstream:
        return []
    child = downstream[0]
    parent_id = node["parent_id"]
    conflicts = []
    for ch in models.get_changeset(conn, node_id):
        ref = ch["reference"]
        if models.get_change(conn, child["id"], ref) is not None:
            continue  # 子节点显式覆盖 → 屏蔽
        old_val = _resolved_value(conn, child["id"], ref)          # 删前（经被删节点）
        new_val = _resolved_value(conn, parent_id, ref)            # 删后（继承父节点）
        if old_val != new_val:
            conflicts.append(Conflict(child["id"], ref, old_val, new_val))
    return conflicts


def delete_node(conn, node_id, choices: dict | None = None) -> None:
    """删除节点：记删除事件 → 物理删除+下游重接 → 按 choices 处理受影响位号（1-A/3-B）。

    choices: {reference: 'keep'|'take'}，缺省 'take'（采用删后的新继承值）。
      · take：D 重新继承新值，记一条 propagated 日志（3-B）；
      · keep：把删前值固化成 D 的显式 op 以冻结，值未变不记日志。
    删除事件挂在父节点上（被删节点行将不存在），op='delete_node'（3-B）。
    """
    choices = choices or {}
    node = models.get_node(conn, node_id)
    parent_id = node["parent_id"]
    assert parent_id is not None, "不能删除根节点"
    conflicts = detect_delete_conflicts(conn, node_id)

    audit.record_edit(
        conn, parent_id, "", None, None, "delete_node", "direct",
        note=f"删除节点 #{node_id}「{node['message'] or '无说明'}」",
    )
    paths = models.delete_node(conn, node_id)

    for cf in conflicts:
        old, new = cf.downstream_value, cf.corrected_value
        if choices.get(cf.reference, "take") == "keep":
            # 冻结：把删前值固化成子节点显式 op。值未变但这是一次直接数据变异，
            # 记一条 direct 日志说明来历（append-only），避免日后无从追溯这条 op。
            op = "remove" if old is None else ("add" if new is None else "modify")
            models.set_change(conn, cf.downstream_node_id, cf.reference, op, old)
            audit.record_edit(
                conn, cf.downstream_node_id, cf.reference, old, old, op, "direct",
                note=f"因删除节点 #{node_id} 固化保留原继承值",
            )
        else:  # take：解析值变化，记 propagated。op 按下游视角判定（None→值=add）
            op = "remove" if new is None else ("add" if old is None else "modify")
            audit.record_edit(
                conn, cf.downstream_node_id, cf.reference, old, new, op, "propagated",
                note=f"因删除节点 #{node_id} 重新继承",
            )

    # 文件清理放最后：DB 侧的冲突固化/传播已全部落定，磁盘文件删除失败
    # （权限、磁盘故障等）只影响附件残留，不应阻断上面的核心传播逻辑。
    if paths:
        storage.delete_files(paths)


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
