import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def test_home_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Reflow" in r.text


def test_import_preview_then_create_bom_version(client):
    csv = 'Reference,Part\n"R1,R2",10k\nR1,22k\n'
    r = client.post("/bom-version/import-preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"},
                    files={"file": ("bom.csv", csv, "text/csv")})
    assert r.status_code == 200
    assert "重复" in r.text or "duplicate" in r.text

    r2 = client.post("/bom-version",
                     data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                           "csv_text": csv})
    assert r2.status_code in (200, 303)


def test_create_board_then_state_graph(client):
    csv = "Reference,Part\nR1,10k\n"
    client.post("/bom-version",
                data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                      "csv_text": csv})
    r = client.post("/board",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                          "board_uid": "3"}, follow_redirects=False)
    assert r.status_code in (200, 303)


def _setup_board(client):
    client.post("/bom-version",
                data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                      "csv_text": "Reference,Part\nR1,10k\n"})
    r = client.post("/board",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA",
                          "board_uid": "3"}, follow_redirects=False)
    return r.headers["location"]            # /board/{id}


def test_node_detail_shows_full_bom(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.get(loc)
    assert r.status_code == 200
    rg = client.get(f"/board/{board_id}")
    assert "R1" in rg.text or "node" in rg.text


def test_commit_workspace_creates_node(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "100nF"})
    r = client.post(f"/board/{board_id}/commit", data={"message": "加 C9"},
                    follow_redirects=False)
    assert r.status_code in (200, 303)


def test_edit_history_node_returns_conflict_fragment(client):
    loc = _setup_board(client)
    board_id = int(loc.rsplit("/", 1)[-1])
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "S1"})
    from app import models
    from app.main import get_conn
    conn = get_conn()
    root = models.list_nodes(conn, board_id)[0]["id"]
    r = client.post(f"/board/{board_id}/node/{root}/edit",
                    data={"reference": "R1", "op": "modify", "part": "22k"})
    assert "冲突" in r.text or "采用修正值" in r.text


def test_log_page_lists_edits(client):
    loc = _setup_board(client)
    board_id = int(loc.rsplit("/", 1)[-1])
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    r = client.get(f"/board/{board_id}/log")
    assert r.status_code == 200
    assert "R1" in r.text
    assert "direct" in r.text or "直接" in r.text


def _workspace_id(client, board_id):
    from app import models
    from app.main import get_conn
    return models.workspace_node(get_conn(), int(board_id))["id"]


def test_edit_rejects_unknown_reference(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/edit",
                    data={"reference": "R99", "op": "modify", "part": "1k"})
    assert r.status_code == 200
    assert r.headers.get("HX-Retarget") == "#form-error"
    assert "不存在" in r.text


def test_edit_rejects_add_existing(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/edit",
                    data={"reference": "R1", "op": "add", "part": "1k"})
    assert r.headers.get("HX-Retarget") == "#form-error"
    assert "已存在" in r.text


def test_workspace_edit_rejects_invalid(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.post(f"/board/{board_id}/workspace/edit",
                    data={"reference": "R99", "op": "modify", "part": "1k"})
    assert r.status_code == 400
    assert "不存在" in r.text


def test_undo_draft_change(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/undo", data={"reference": "R1"})
    assert r.status_code == 200
    from app import models
    from app.main import get_conn
    assert models.get_change(get_conn(), ws, "R1") is None


def test_undo_rejected_on_committed_node(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    committed = _workspace_id(client, board_id)
    client.post(f"/board/{board_id}/commit", data={"message": "S1"})
    r = client.post(f"/board/{board_id}/node/{committed}/undo",
                    data={"reference": "R1"})
    assert "不能撤销" in r.text
    from app import models
    from app.main import get_conn
    assert models.get_change(get_conn(), committed, "R1") is not None


def test_undo_unknown_node_404(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.post(f"/board/{board_id}/node/9999/undo", data={"reference": "R1"})
    assert r.status_code == 404


def test_unknown_board_returns_chinese_404(client):
    r = client.get("/board/9999")
    assert r.status_code == 404
    assert "未找到" in r.text


def test_unknown_node_returns_chinese_404(client):
    loc = _setup_board(client)
    r = client.get(f"{loc}/node/9999")
    assert r.status_code == 404
    assert "未找到" in r.text


def test_resolve_unknown_node_404(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.post(f"/board/{board_id}/node/9999/resolve",
                    data={"downstream_node_id": "1", "reference": "R1",
                          "choice": "keep"})
    assert r.status_code == 404


def test_workspace_edit_unknown_board_404(client):
    r = client.post("/board/9999/workspace/edit",
                    data={"reference": "R1", "op": "modify", "part": "1k"})
    assert r.status_code == 404
