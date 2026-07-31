"""元器件值 lint 异步化：批量端点、面板三态、一次性 ⓘ。"""
import pytest
from fastapi.testclient import TestClient


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
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def _workspace_id(client, board_id):
    from app import models
    from app.main import get_conn
    return models.workspace_node(get_conn(), int(board_id))["id"]


def _add(client, board_id, ws, reference, part, op="add"):
    return client.post(f"/board/{board_id}/node/{ws}/edit",
                       data={"reference": reference, "op": op, "part": part})


def test_lint_changes_reports_non_standard_value(client):
    """批量端点对面板里的非标准值报 ⚠。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert r.status_code == 200
    assert "不是标准" in r.text


def test_lint_changes_is_clean_for_standard_value(client):
    """标准值不产生 ⚠。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "10k")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert "不是标准" not in r.text


def test_lint_changes_skips_removed_rows(client):
    """op=remove 的行 part 为 None，批量 lint 必须跳过而不是抛错。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "remove", "part": ""})
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert r.status_code == 200
    assert "R1" in r.text


def test_lint_changes_response_does_not_retrigger_itself(client):
    """批量端点的响应不能再挂 load 触发器，否则自我无限触发。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert 'hx-trigger="load"' not in r.text


def test_lint_changes_rejects_foreign_node(client):
    """节点不属于该单板时 404，与其余节点路由一致。"""
    board_id = _setup_board(client)
    r = client.post(f"/board/{board_id}/node/99999/lint-changes")
    assert r.status_code == 404
