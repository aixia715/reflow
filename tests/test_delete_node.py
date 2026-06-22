"""删除 BOM 节点功能（1-A 物理删除+自动重接+冲突确认 / 2-B 正式节点不可撤销 /
3-B 删除事件+逐条 propagated 日志 / 4-A 工作区草稿随链重建）。

纯逻辑测试在 propagation / models 层；路由测试用 TestClient。
"""
import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db
from app import models, propagation, audit
from app.main import get_conn
from app.bom_engine import resolve_reference
from app.csv_import import CsvEntry


def _append_committed(conn, board_id, parent_id, message) -> int:
    now = models._now()
    return conn.execute(
        "INSERT INTO nodes(board_id,parent_id,message,created_at,is_committed,committed_at)"
        " VALUES(?,?,?,?,1,?)",
        (board_id, parent_id, message, now, now),
    ).lastrowid


def _resolved(conn, node_id, ref):
    initial, chain = models.get_chain(conn, node_id)
    return resolve_reference(initial, chain, ref)


@pytest.fixture
def chain():
    """初始 R1=10k；纯线性历史链 根 -> S1 -> S2 -> S3；S2 显式 R1=47k。
    （删掉 create_board 自带的工作区草稿，构造纯历史链）"""
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    nodes = models.list_nodes(c, bid)
    root_id, draft_id = nodes[0]["id"], nodes[1]["id"]
    c.execute("DELETE FROM nodes WHERE id=?", (draft_id,))
    c.commit()
    s1 = _append_committed(c, bid, root_id, "S1")
    s2 = _append_committed(c, bid, s1, "S2")
    s3 = _append_committed(c, bid, s2, "S3")
    models.set_change(c, s2, "R1", "modify", "47k")
    return c, bid, {"root": root_id, "s1": s1, "s2": s2, "s3": s3}


# ── 冲突检测 ─────────────────────────────────────────────────────────

def test_detect_conflict_when_downstream_inherits(chain):
    c, bid, n = chain
    conflicts = propagation.detect_delete_conflicts(c, n["s2"])
    assert len(conflicts) == 1
    cf = conflicts[0]
    assert cf.downstream_node_id == n["s3"]
    assert cf.reference == "R1"
    assert cf.downstream_value == "47k"   # 删前（经 S2）
    assert cf.corrected_value == "10k"    # 删后（继承 S1）


def test_no_conflict_when_downstream_overrides(chain):
    c, bid, n = chain
    models.set_change(c, n["s3"], "R1", "modify", "22k")  # S3 显式覆盖，屏蔽
    assert propagation.detect_delete_conflicts(c, n["s2"]) == []


def test_no_conflict_when_value_unchanged(chain):
    c, bid, n = chain
    # S2 把 R1 改成和上游相同的 10k → 删除后解析值不变 → 无冲突
    models.set_change(c, n["s2"], "R1", "modify", "10k")
    assert propagation.detect_delete_conflicts(c, n["s2"]) == []


# ── 删除 + 重接 ─────────────────────────────────────────────────────

def test_delete_reattaches_child_to_parent(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"])
    assert models.get_node(c, n["s2"]) is None
    assert models.get_node(c, n["s3"])["parent_id"] == n["s1"]


def test_delete_removes_changeset_rows(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"])
    assert models.get_changeset(c, n["s2"]) == []


def test_take_new_inherited_value(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"], {"R1": "take"})
    assert _resolved(c, n["s3"], "R1") == "10k"        # 重新继承 S1
    assert models.get_change(c, n["s3"], "R1") is None  # 无显式 op


def test_keep_downstream_value_materializes(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"], {"R1": "keep"})
    assert _resolved(c, n["s3"], "R1") == "47k"          # 冻结删前值
    assert models.get_change(c, n["s3"], "R1") is not None  # 固化为显式 op


def test_default_choice_is_take(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"])  # 不传 choices
    assert _resolved(c, n["s3"], "R1") == "10k"


# ── 审计日志（3-B）────────────────────────────────────────────────────

def test_delete_records_delete_event_on_parent(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"], {"R1": "take"})
    logs = audit.list_log(c)
    deletes = [r for r in logs if r["op"] == "delete_node"]
    assert len(deletes) == 1
    assert deletes[0]["node_id"] == n["s1"]   # 挂在父节点上
    assert deletes[0]["source"] == "direct"


def test_take_records_propagated_log(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"], {"R1": "take"})
    props = [r for r in audit.list_log(c)
             if r["source"] == "propagated" and r["node_id"] == n["s3"]]
    assert len(props) == 1
    assert props[0]["reference"] == "R1"
    assert props[0]["old_part"] == "47k"
    assert props[0]["new_part"] == "10k"


def test_keep_records_no_propagated_log(chain):
    c, bid, n = chain
    propagation.delete_node(c, n["s2"], {"R1": "keep"})
    props = [r for r in audit.list_log(c) if r["source"] == "propagated"]
    assert props == []


def test_deleted_node_edit_logs_removed(chain):
    c, bid, n = chain
    audit.record_edit(c, n["s2"], "R1", "10k", "47k", "modify", "direct")
    propagation.delete_node(c, n["s2"], {"R1": "take"})
    assert audit.list_log(c, n["s2"]) == []  # 被删节点自身日志清掉（FK）


# ── 4-A：工作区草稿随链重建 ──────────────────────────────────────────

@pytest.fixture
def chain_with_draft():
    """根 -> S1 -> 工作区草稿；S1 显式 R1=47k；草稿显式 C1=不贴。"""
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA",
                              [CsvEntry("R1", "10k"), CsvEntry("C1", "100nF")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    nodes = models.list_nodes(c, bid)
    root_id, draft_id = nodes[0]["id"], nodes[1]["id"]
    s1 = _append_committed(c, bid, root_id, "S1")
    # 把草稿重挂到 S1 之后
    c.execute("UPDATE nodes SET parent_id=? WHERE id=?", (s1, draft_id))
    c.commit()
    models.set_change(c, s1, "R1", "modify", "47k")
    models.set_change(c, draft_id, "C1", "remove", None)
    return c, bid, {"root": root_id, "s1": s1, "draft": draft_id}


def test_delete_reattaches_draft_and_keeps_its_ops(chain_with_draft):
    c, bid, n = chain_with_draft
    propagation.delete_node(c, n["s1"], {"R1": "take"})
    draft = models.get_node(c, n["draft"])
    assert draft["parent_id"] == n["root"]           # 草稿重挂到根
    assert draft["is_committed"] == 0                 # 仍是草稿
    assert models.get_change(c, n["draft"], "C1") is not None  # 草稿显式 op 保留
    assert _resolved(c, n["draft"], "R1") == "10k"    # 受影响位号重新继承


# ── 路由层 ───────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]  # board_id


def _commit_node(client, bid, ref, op, part, msg):
    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": ref, "op": op, "part": part})
    client.post(f"/board/{bid}/commit", data={"message": msg}, follow_redirects=False)


def test_route_delete_no_conflict_redirects(client):
    bid = _setup_board(client)
    # 提交一个空 changeset 的节点：删除它不影响任何下游位号 → 无冲突
    client.post(f"/board/{bid}/commit", data={"message": "空节点"},
                follow_redirects=False)
    nodes = models.list_nodes(get_conn(), int(bid))
    target = [x for x in nodes if x["message"] == "空节点"][0]["id"]
    r = client.post(f"/board/{bid}/node/{target}/delete")
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect", "").startswith(f"/board/{bid}")
    assert models.get_node(get_conn(), target) is None


def test_route_cannot_delete_root(client):
    bid = _setup_board(client)
    root = models.list_nodes(
        get_conn(), int(bid))[0]["id"]
    r = client.post(f"/board/{bid}/node/{root}/delete")
    assert r.status_code == 400


def test_route_cannot_delete_draft(client):
    bid = _setup_board(client)
    conn = get_conn()
    draft = models.workspace_node(conn, int(bid))["id"]
    r = client.post(f"/board/{bid}/node/{draft}/delete")
    assert r.status_code == 400


def test_route_delete_with_conflict_shows_modal(client):
    bid = _setup_board(client)
    _commit_node(client, bid, "R1", "modify", "47k", "改 R1")   # S1
    _commit_node(client, bid, "C2", "add", "1uF", "加 C2")       # S2 继承 R1=47k
    conn = get_conn()
    s1 = [x for x in models.list_nodes(conn, int(bid)) if x["message"] == "改 R1"][0]["id"]
    r = client.post(f"/board/{bid}/node/{s1}/delete")
    assert r.status_code == 200
    assert "modal" in r.text or "确认删除" in r.text
    assert r.headers.get("HX-Retarget") == "#modal"
