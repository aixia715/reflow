"""工作区草稿里把值改回上游原样 = 这条修改等于没发生。

插入页早就是这个行为（`/insert/check` 的 action=drop），工作区一直没有：改成
47k 再改回 10k，「本节点修改」面板上会留一条改前改后相同的假修改，提交后节点
也带着一条空内容的 changeset。这里把 drop 补给草稿，但**审计日志照记**——
append-only 记的是发生过什么，不是最后长什么样。

只对未提交草稿生效：已提交节点的显式 op 还承担屏蔽上游修正的作用（下游显式
操作过同一位号才算冲突），值和父节点相同也不能删。
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _board(client, csv="Reference,Part\nR1,10k\nC1,100nF\n"):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "N1"},
                    files={"file": ("bom.csv", csv, "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _conn():
    from app.main import get_conn
    return get_conn()


def _draft(board_id):
    from app import models
    return models.workspace_node(_conn(), board_id)


def _changeset(node_id):
    from app import models
    return {c["reference"]: c for c in models.get_changeset(_conn(), node_id)}


def _log_refs(node_id, reference):
    from app import audit
    return [dict(r) for r in audit.list_log(_conn(), node_id)
            if r["reference"] == reference]


def test_draft_edit_back_to_parent_value_drops_the_change_row(client):
    """改成 47k 再改回 10k（父节点原值）→ changeset 里不该留这条。"""
    bid = _board(client)
    draft = _draft(bid)["id"]

    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    assert "R1" in _changeset(draft)          # 前置：确实记下了

    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "10k"})
    assert "R1" not in _changeset(draft)


def test_draft_edit_back_to_parent_value_still_records_audit(client):
    """changeset 行删了，但两次编辑的审计记录都要在。"""
    bid = _board(client)
    draft = _draft(bid)["id"]

    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "10k"})

    rows = _log_refs(draft, "R1")
    assert len(rows) == 2
    assert [r["old_part"] for r in rows] == ["10k", "47k"]
    assert [r["new_part"] for r in rows] == ["47k", "10k"]


def test_draft_add_then_remove_of_upstream_absent_ref_drops_the_row(client):
    """草稿里新增 D9 又设为不贴 → 两步抵消，上游本来就没有 D9，不该留痕。"""
    bid = _board(client)
    draft = _draft(bid)["id"]

    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "D9", "op": "add", "part": "1uF"})
    assert "D9" in _changeset(draft)

    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "D9", "op": "remove"})
    assert "D9" not in _changeset(draft)
    assert len(_log_refs(draft, "D9")) == 2


def test_draft_edit_to_a_different_value_still_records_the_row(client):
    """对照组：值和父节点不同，照常留 changeset 行。"""
    bid = _board(client)
    draft = _draft(bid)["id"]

    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    assert _changeset(draft)["R1"]["part"] == "47k"


def test_committed_node_keeps_explicit_change_matching_parent(client):
    """已提交节点修订成与父节点相同的值 → changeset 行必须保留。

    它不只是「一条修改」，还屏蔽上游修正：父节点日后改成 47k 时，这个节点应当
    仍解析为 10k 并触发冲突确认，而不是悄悄跟着变。
    """
    from app import models
    bid = _board(client)
    draft = _draft(bid)["id"]
    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{bid}/commit", data={"message": "改 R1"},
                follow_redirects=False)
    committed = [n for n in models.list_nodes(_conn(), bid)
                 if n["is_committed"] and n["parent_id"] is not None][0]

    # 把这个已提交节点的 R1 修订回 10k（＝其父节点/根节点的值）
    client.post(f"/board/{bid}/node/{committed['id']}/edit",
                data={"reference": "R1", "op": "modify", "part": "10k"})

    cs = _changeset(committed["id"])
    assert "R1" in cs and cs["R1"]["part"] == "10k"
