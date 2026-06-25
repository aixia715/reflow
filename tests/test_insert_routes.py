import json
import pytest
from fastapi.testclient import TestClient
from app import models
from app.bom_engine import resolve_reference


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _resolved(conn, node_id, ref):
    initial, chain = models.get_chain(conn, node_id)
    return resolve_reference(initial, chain, ref)


def _setup_chain(client, uid="3"):
    """建 root -> c1 -> c2 -> 草稿，c1/c2 的 committed_at 间隔开。返回 (board_id, root, c1, c2)。"""
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": uid},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    board_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "100nF"})
    client.post(f"/board/{board_id}/commit", data={"message": "c1"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "22k"})
    client.post(f"/board/{board_id}/commit", data={"message": "c2"})
    from app.main import get_conn
    conn = get_conn()
    nodes = models.list_nodes(conn, board_id)
    root = nodes[0]["id"]
    committed = [n for n in nodes if n["is_committed"] and n["parent_id"] is not None]
    c1, c2 = committed[0]["id"], committed[1]["id"]
    conn.execute("UPDATE nodes SET committed_at=? WHERE id=?",
                 ("2026-06-01T00:00:00+00:00", c1))
    conn.execute("UPDATE nodes SET committed_at=? WHERE id=?",
                 ("2026-06-10T00:00:00+00:00", c2))
    conn.commit()
    return board_id, root, c1, c2


def test_insert_page_loads_for_middle_node(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.get(f"/board/{board_id}/node/{c1}/insert")
    assert r.status_code == 200
    assert "插入" in r.text


def test_insert_rejected_at_last_committed(client):
    board_id, root, c1, c2 = _setup_chain(client)
    # c2 是最后一个已提交节点，子节点是草稿 → 不可插入
    r = client.get(f"/board/{board_id}/node/{c2}/insert", follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    loc = unquote(r.headers["location"])
    assert "工作区" in loc or "不可插入" in loc


def test_insert_creates_node_and_relinks(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "ins",
                          "changes": json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])},
                    follow_redirects=False)
    assert r.status_code == 303
    from app.main import get_conn
    conn = get_conn()
    new = conn.execute("SELECT * FROM nodes WHERE parent_id=?", (c1,)).fetchone()
    assert new["id"] != c2
    assert new["committed_at"] == "2026-06-05T00:00:00+00:00"
    assert conn.execute("SELECT parent_id FROM nodes WHERE id=?",
                        (c2,)).fetchone()["parent_id"] == new["id"]
    assert _resolved(conn, c2, "D1") == "1uF"


def test_insert_success_flash_keeps_node_number(client):
    from urllib.parse import urlparse, parse_qs, unquote
    board_id, root, c1, c2 = _setup_chain(client)
    changes = json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])
    # 非 HX：Location 头里的 flash 应保留节点号（# 不能被当作 fragment 截断）
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00",
                          "message": "ins", "changes": changes},
                    follow_redirects=False)
    flash = unquote(parse_qs(urlparse(r.headers["location"]).query)["flash"][0])
    assert "已插入节点" in flash and "#" in flash
    # HX：HX-Redirect 头同样保留节点号
    board_id, root, c1, c2 = _setup_chain(client, uid="4")
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00",
                          "message": "ins", "changes": changes},
                    headers={"HX-Request": "true"})
    flash = unquote(parse_qs(urlparse(r.headers["HX-Redirect"]).query)["flash"][0])
    assert "已插入节点" in flash and "#" in flash


def test_insert_empty_changes_rejected(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "",
                          "changes": json.dumps([])})
    assert r.status_code == 200
    assert "至少" in r.text
    from app.main import get_conn
    conn = get_conn()
    assert conn.execute("SELECT parent_id FROM nodes WHERE id=?",
                        (c2,)).fetchone()["parent_id"] == c1


def test_insert_time_out_of_range_rejected(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-07-01T00:00:00+00:00", "message": "",
                          "changes": json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])})
    assert r.status_code == 200
    assert "下一个" in r.text
    from app.main import get_conn
    conn = get_conn()
    assert conn.execute("SELECT parent_id FROM nodes WHERE id=?",
                        (c2,)).fetchone()["parent_id"] == c1


def test_insert_conflict_when_downstream_explicit(client):
    board_id, root, c1, c2 = _setup_chain(client)
    # c2 显式改了 R1=22k；在 c1 后插入也改 R1=47k → 与下游 c2 冲突
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "ins",
                          "changes": json.dumps([{"reference": "R1", "op": "modify", "part": "47k"}])})
    assert r.status_code == 200
    assert "确认" in r.text
    from app.main import get_conn
    conn = get_conn()
    new = conn.execute("SELECT * FROM nodes WHERE parent_id=?", (c1,)).fetchone()
    assert new["id"] != c2
