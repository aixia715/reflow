import pytest
from app.db import connect, init_db
from app import models, propagation
from app.bom_engine import resolve_reference
from app.csv_import import CsvEntry


@pytest.fixture
def chain_s1_s2_s3():
    """初始 R1=10k；链 根 -> S1 -> S2 -> S3；S2 显式 R1=47k。返回 (conn, bid, [节点id...])。"""
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    root_id = models.list_nodes(c, bid)[0]["id"]
    s1 = propagation._append_committed_node(c, bid, root_id, "S1")
    s2 = propagation._append_committed_node(c, bid, s1, "S2")
    s3 = propagation._append_committed_node(c, bid, s2, "S3")
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
