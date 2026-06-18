import pytest
from fastapi.testclient import TestClient

from app import hashing


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path / "up"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _committed_node(client, board_id):
    from app import models
    from app.main import get_conn
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"})
    return [n for n in models.list_nodes(get_conn(), board_id)
            if n["is_committed"] and n["parent_id"] is not None][0]["id"]


def test_hash_route_redirects_node_by_full_hash(client):
    board_id = _setup_board(client)
    node_id = _committed_node(client, board_id)
    full = hashing.node_hash(node_id)
    r = client.get(f"/hash/{full}", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"/board/{board_id}/node/{node_id}"


def test_hash_route_redirects_node_by_short_hash(client):
    board_id = _setup_board(client)
    node_id = _committed_node(client, board_id)
    short = hashing.node_short(node_id)
    r = client.get(f"/hash/{short}", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"/board/{board_id}/node/{node_id}"


def test_hash_route_is_case_insensitive(client):
    board_id = _setup_board(client)
    node_id = _committed_node(client, board_id)
    short = hashing.node_short(node_id).upper()
    r = client.get(f"/hash/{short}", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"/board/{board_id}/node/{node_id}"


def test_hash_route_redirects_hard_change(client):
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/hard-change",
                data={"title": "返修 U1", "occurred_at": "2026-06-17T10:00",
                      "description": ""})
    from app import models
    from app.main import get_conn
    hc_id = models.list_hard_changes(get_conn(), board_id)[0]["id"]
    full = hashing.hard_change_hash(hc_id)
    r = client.get(f"/hash/{full}", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"] == f"/board/{board_id}/hard-change/{hc_id}"


def test_hash_route_unknown_returns_404(client):
    _setup_board(client)
    r = client.get("/hash/deadbeef", follow_redirects=False)
    assert r.status_code == 404


def test_hash_route_too_short_returns_404(client):
    _setup_board(client)
    r = client.get("/hash/ab", follow_redirects=False)
    assert r.status_code == 404


def test_node_detail_shows_short_hash(client):
    board_id = _setup_board(client)
    node_id = _committed_node(client, board_id)
    short = hashing.node_short(node_id)
    page = client.get(f"/board/{board_id}/node/{node_id}").text
    assert short in page


def test_state_graph_shows_short_hash(client):
    board_id = _setup_board(client)
    node_id = _committed_node(client, board_id)
    short = hashing.node_short(node_id)
    page = client.get(f"/board/{board_id}").text
    assert short in page
