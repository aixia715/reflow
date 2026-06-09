import pytest
from app.db import connect, init_db
from app import models, propagation
from app.bom_engine import resolve_reference
from app.csv_import import CsvEntry


def _append_committed_node(conn, board_id, parent_id, message) -> int:
    """测试辅助：在 parent 之后追加一个已提交节点，返回其 id。"""
    now = models._now()
    return conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,1,?)",
        (board_id, parent_id, message, now, now),
    ).lastrowid


@pytest.fixture
def chain_s1_s2_s3():
    """初始 R1=10k；链 根 -> S1 -> S2 -> S3；S2 显式 R1=47k。返回 (conn, bid, [节点id...])。

    create_board 会在根之后挂一个工作区草稿，这里删掉它以构造一条纯线性的
    历史链（这些测试只关心历史节点编辑，不涉及工作区）。
    """
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    nodes = models.list_nodes(c, bid)
    root_id = nodes[0]["id"]
    draft_id = nodes[1]["id"]
    c.execute("DELETE FROM nodes WHERE id=?", (draft_id,))
    c.commit()
    s1 = _append_committed_node(c, bid, root_id, "S1")
    s2 = _append_committed_node(c, bid, s1, "S2")
    s3 = _append_committed_node(c, bid, s2, "S3")
    models.set_change(c, s2, "R1", "modify", "47k")
    return c, bid, [root_id, s1, s2, s3]


def _resolved(conn, node_id, ref):
    initial, chain = models.get_chain(conn, node_id)
    return resolve_reference(initial, chain, ref)


def test_edit_s1_detects_conflict_at_s2(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "22k")
    assert len(conflicts) == 1
    assert conflicts[0].downstream_node_id == s2
    assert conflicts[0].downstream_value == "47k"
    assert conflicts[0].corrected_value == "22k"
    assert _resolved(c, s1, "R1") == "22k"


def test_keep_downstream_value(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "22k")
    propagation.resolve_conflict(c, conflicts[0], "keep")
    assert _resolved(c, s1, "R1") == "22k"
    assert _resolved(c, s2, "R1") == "47k"
    assert _resolved(c, s3, "R1") == "47k"


def test_take_corrected_value_propagates(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "22k")
    propagation.resolve_conflict(c, conflicts[0], "take")
    assert _resolved(c, s1, "R1") == "22k"
    assert _resolved(c, s2, "R1") == "22k"
    assert _resolved(c, s3, "R1") == "22k"
    assert models.get_change(c, s2, "R1") is None


def test_no_downstream_explicit_is_zero_conflict(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "C9", "add", "100nF")
    assert conflicts == []
    assert _resolved(c, s3, "C9") == "100nF"


def test_remove_upstream_conflicts_with_downstream_modify(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "remove", None)
    assert len(conflicts) == 1
    assert conflicts[0].corrected_value is None
    propagation.resolve_conflict(c, conflicts[0], "take")
    assert _resolved(c, s3, "R1") is None


def test_edit_root_node_propagates(chain_s1_s2_s3):
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, root, "R1", "modify", "5k")
    assert len(conflicts) == 1 and conflicts[0].downstream_node_id == s2


def test_no_conflict_when_downstream_already_equals_correction(chain_s1_s2_s3):
    # S2 显式 R1=47k；把 S1 修正为 47k —— 下游显式值已等于修正值，不应弹冲突。
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "47k")
    assert conflicts == []
    assert _resolved(c, s1, "R1") == "47k"
    assert _resolved(c, s3, "R1") == "47k"


def test_workspace_draft_at_tail_participates_in_conflict(chain_s1_s2_s3):
    # 在 S3 之后挂一个工作区草稿并显式改 R1；编辑历史上游应能检出草稿冲突，
    # 验证 _children_in_order 沿 parent 链能正确纳入末端草稿。
    c, bid, (root, s1, s2, s3) = chain_s1_s2_s3
    models.delete_change(c, s2, "R1")          # 移除 S2 的显式值，让草稿成为唯一下游显式节点
    draft = _append_committed_node(c, bid, s3, "WS")
    c.execute("UPDATE nodes SET is_committed=0, committed_at=NULL WHERE id=?", (draft,))
    c.commit()
    models.set_change(c, draft, "R1", "modify", "99k")
    conflicts = propagation.apply_node_edit(c, s1, "R1", "modify", "22k")
    assert len(conflicts) == 1
    assert conflicts[0].downstream_node_id == draft
