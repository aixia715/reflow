"""issue #116：同一 PCB 版本下不同单板的跨板 BOM 比较。"""
import re
import pytest
from fastapi.testclient import TestClient

from app.db import connect, init_db
from app import models
from app.csv_import import CsvEntry


# ---------- models.list_sibling_boards ----------

@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    return c


def test_list_sibling_boards_scope(conn):
    """兄弟单板 = 同「单板名称 + PCB版本」下全部单板（含自身、跨 BOM 版本）。"""
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    models.create_bom_version(conn, "B", "v1", "bomB", [CsvEntry("R1", "22k")])
    models.create_bom_version(conn, "B", "v2", "bomA", [CsvEntry("R1", "10k")])
    models.create_bom_version(conn, "C", "v1", "bomA", [CsvEntry("R1", "10k")])
    b1 = models.create_board(conn, "B", "v1", "bomA", "3")
    b2 = models.create_board(conn, "B", "v1", "bomB", "5")
    models.create_board(conn, "B", "v2", "bomA", "7")   # 不同 PCB 版本，排除
    models.create_board(conn, "C", "v1", "bomA", "9")   # 不同名称，排除
    sibs = models.list_sibling_boards(conn, b1)
    assert [s["id"] for s in sibs] == [b1, b2]


def test_list_sibling_boards_orders_by_bom_version_then_uid(conn):
    models.create_bom_version(conn, "B", "v1", "bomB", [CsvEntry("R1", "10k")])
    models.create_bom_version(conn, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    b_late = models.create_board(conn, "B", "v1", "bomB", "1")
    b_a2 = models.create_board(conn, "B", "v1", "bomA", "2")
    b_a1 = models.create_board(conn, "B", "v1", "bomA", "1")
    sibs = models.list_sibling_boards(conn, b_late)
    assert [s["id"] for s in sibs] == [b_a1, b_a2, b_late]


# ---------- 路由 ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _new_board(client, bom_version, board_uid, csv, pcb_version="v1"):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": pcb_version,
                          "bom_version": bom_version, "board_uid": board_uid},
                    files={"file": ("bom.csv", csv, "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _commit_edit(client, board_id, ref, op, part, msg):
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": ref, "op": op, "part": part})
    client.post(f"/board/{board_id}/commit", data={"message": msg},
                follow_redirects=False)


def _node_ids(client, board_id):
    r = client.get(f"/board/{board_id}")
    return sorted({int(x) for x in
                   re.findall(rf"/board/{board_id}/node/(\d+)", r.text)})


def test_state_graph_cross_button_hidden_without_sibling(client):
    bid = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    r = client.get(f"/board/{bid}")
    assert "cross-compare" not in r.text


def test_state_graph_cross_button_shown_with_sibling(client):
    bid = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    _new_board(client, "bomB", "5", "Reference,Part\nR1,22k\n")
    r = client.get(f"/board/{bid}")
    assert "cross-compare" in r.text


def test_compare_cross_board_renders_and_hides_hard_changes(client):
    ba = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    bb = _new_board(client, "bomB", "5", "Reference,Part\nR1,22k\n")
    # 双方都有硬更改，跨板对比时也不应显示硬更改区块
    client.post(f"/board/{ba}/hard-change",
                data={"title": "返修 U1", "occurred_at": "2026-06-17T10:00",
                      "description": ""})
    left = _node_ids(client, ba)[0]
    right = _node_ids(client, bb)[0]
    r = client.get(f"/board/{ba}/compare?left={left}&right={right}")
    assert r.status_code == 200
    assert "板 3" in r.text and "板 5" in r.text
    assert "22k" in r.text          # 差异行：R1 10k → 22k
    assert "这段时间内的硬更改" not in r.text


def test_compare_same_board_still_shows_hard_changes(client):
    ba = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    _commit_edit(client, ba, "C9", "add", "100nF", "加 C9")
    ids = _node_ids(client, ba)
    r = client.get(f"/board/{ba}/compare?left={ids[0]}&right={ids[-1]}")
    assert r.status_code == 200
    assert "这段时间内的硬更改" in r.text


def test_compare_node_from_other_pcb_version_404(client):
    ba = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    bc = _new_board(client, "bomA", "7", "Reference,Part\nR1,10k\n",
                    pcb_version="v2")
    left = _node_ids(client, ba)[0]
    right = _node_ids(client, bc)[0]
    r = client.get(f"/board/{ba}/compare?left={left}&right={right}")
    assert r.status_code == 404


def test_compare_right_default_uses_sibling_latest_committed(client):
    """只传 left 时，right 默认取第一块兄弟单板的最新已提交节点。"""
    ba = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    bb = _new_board(client, "bomB", "5", "Reference,Part\nR1,10k\n")
    _commit_edit(client, bb, "C9", "add", "100nF", "B板加C9")
    left = _node_ids(client, ba)[0]
    r = client.get(f"/board/{ba}/compare?left={left}")
    assert r.status_code == 200
    # C9 只存在于 B 板最新已提交节点，出现即证明默认没有取根节点
    assert "C9" in r.text
    assert "板 5" in r.text


def test_compare_right_missing_without_sibling_still_404(client):
    bid = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    left = _node_ids(client, bid)[0]
    r = client.get(f"/board/{bid}/compare?left={left}")
    assert r.status_code == 404


def test_compare_page_has_board_and_node_selects(client):
    ba = _new_board(client, "bomA", "3", "Reference,Part\nR1,10k\n")
    _new_board(client, "bomB", "5", "Reference,Part\nR1,22k\n")
    ids = _node_ids(client, ba)
    _commit_edit(client, ba, "C9", "add", "100nF", "加 C9")
    ids = _node_ids(client, ba)
    r = client.get(f"/board/{ba}/compare?left={ids[0]}&right={ids[-1]}")
    assert r.status_code == 200
    for tid in ("cmp-board-left", "cmp-node-left",
                "cmp-board-right", "cmp-node-right"):
        assert tid in r.text
