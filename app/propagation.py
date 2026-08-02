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


def apply_node_edit(conn, node_id, reference, op, part,
                    drop_noop: bool = False) -> list[Conflict]:
    """编辑某节点某位号（修正记录），落库 + 记 direct 日志，返回冲突列表（最多一个）。

    op: 'add'|'modify'|'remove'；remove 时 part 传 None。

    drop_noop 只给**交互式单条编辑**用（工作区编辑、已提交节点的修订走同一路由）：
    草稿里把值改回父节点原样时不留 changeset 行，见 `is_noop_against_parent`——
    该函数对已提交节点恒返回 False，所以修订历史节点即便传了 True 也永远不会丢，
    不必在调用点分情况。批量写入路径
    （copy-to-draft、插入节点保存）必须保持 False——那里每一条都是用户明确要求
    写进去的，从紧邻父节点复制时逐条都等于父节点原值，开了会被全部吃掉，界面
    上就成了「提示复制了 N 条、面板里一条没有」。

    默认 False 是刻意的：漏传只会退回旧行为（多留一条无害的空转记录），传反了
    才会静默丢数据。
    """
    old_value = _resolved_value(conn, node_id, reference)
    new_value = None if op == "remove" else part
    node = models.get_node(conn, node_id)
    if drop_noop and is_noop_against_parent(conn, node, new_value, reference):
        models.delete_change(conn, node_id, reference)
    else:
        models.set_change(conn, node_id, reference, op, part)
    # 日志无论如何都记：append-only 记的是发生过什么，不是最后长什么样，
    # 「改成 47k 又改回 10k」是确实发生过的两次编辑。
    audit.record_edit(conn, node_id, reference, old_value, new_value, op, "direct")

    # changeset 已是链末显式值（或已删除回落到继承），解析值即 new_value
    node = models.get_node(conn, node_id)
    return _detect_downstream_conflicts(conn, node, reference, new_value)


def is_noop_against_parent(conn, node, new_value, reference) -> bool:
    """这次编辑是否把草稿的该位号改回了父节点原样（＝这条修改等于没发生）。

    留着它只会在「本节点修改」面板上显示成一条改前改后相同的假修改，提交后
    节点还带一条空内容的 changeset。插入页早就是这个行为（/insert/check 的
    action=drop），这里把它补给草稿。

    **只对未提交草稿成立**：已提交节点的显式 op 不只是「一条修改」，它还屏蔽
    上游修正——判定冲突只看下游 changeset 里有没有这条 reference，与值无关。
    删掉一条值恰好等于父节点的显式 op，会让该节点日后悄悄跟着上游变，而不是
    停在原值并触发冲突确认。
    """
    if node["is_committed"] or node["parent_id"] is None:
        return False
    return _resolved_value(conn, node["parent_id"], reference) == new_value


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
