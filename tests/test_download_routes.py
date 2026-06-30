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
                    files={"file": ("bom.csv",
                                    "Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _workspace_id(client, board_id):
    from app import models
    from app.main import get_conn
    return models.workspace_node(get_conn(), board_id)["id"]


def test_download_returns_csv_attachment(client):
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = client.get(f"/board/{board_id}/node/{ws}/download")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]


def test_download_body_has_header_and_rows(client):
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    text = client.get(f"/board/{board_id}/node/{ws}/download").text
    # 响应带 UTF-8 BOM（Excel 兼容），消费端剥掉后首行是表头
    lines = text.lstrip("﻿").splitlines()
    assert lines[0] == "Reference,Part"
    assert "R1,10k" in lines
    assert "C1,100nF" in lines


def test_download_draft_reflects_edits(client):
    board_id = _setup_board(client)
    # 工作区草稿里改 R1、不贴 C1
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C1", "op": "remove", "part": ""})
    ws = _workspace_id(client, board_id)
    text = client.get(f"/board/{board_id}/node/{ws}/download").text
    assert "R1,47k" in text
    # 不贴的 C1 不应出现
    assert "C1" not in text


def test_download_committed_node(client):
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "U1", "op": "add", "part": "MCU"})
    client.post(f"/board/{board_id}/commit", data={"message": "加 U1"})
    from app import models
    from app.main import get_conn
    node = [n for n in models.list_nodes(get_conn(), board_id)
            if n["is_committed"] and n["parent_id"] is not None][0]
    text = client.get(f"/board/{board_id}/node/{node['id']}/download").text
    assert "U1,MCU" in text


def test_download_bad_node_404(client):
    board_id = _setup_board(client)
    r = client.get(f"/board/{board_id}/node/999999/download")
    assert r.status_code == 404


def test_download_root_node(client):
    board_id = _setup_board(client)
    from app import models
    from app.main import get_conn
    root = [n for n in models.list_nodes(get_conn(), board_id)
            if n["parent_id"] is None][0]
    r = client.get(f"/board/{board_id}/node/{root['id']}/download")
    assert r.status_code == 200
    assert "R1,10k" in r.text


def test_download_filename_present(client):
    from urllib.parse import unquote
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    cd = client.get(f"/board/{board_id}/node/{ws}/download").headers["content-disposition"]
    # ASCII 回退 + RFC 5987 两段都在，且 filename* 用 UTF-8 编码、解码后以 .csv 结尾
    assert "filename=" in cd
    assert "filename*=UTF-8''" in cd
    star = cd.split("filename*=UTF-8''", 1)[1]
    assert unquote(star).endswith(".csv")


def test_download_filename_sanitizes_unsafe_chars():
    from app.routes.board import _download_filename
    board = {"board_name": "主/板", "pcb_version": 'v"1', "bom_version": "B\x01"}
    node = {"is_committed": 1, "message": "改 R1", "id": 5}
    name = _download_filename(board, node)
    # 路径分隔符/引号/控制字符不应出现，且以 .csv 结尾
    for bad in '/\\:*?"<>|\x01':
        assert bad not in name
    assert name.endswith(".csv")


def test_download_filename_empty_fallback():
    from app.routes.board import _download_filename
    board = {"board_name": "", "pcb_version": "", "bom_version": ""}
    node = {"is_committed": 1, "message": "", "id": 7}
    # 全空时退回节点标签，至少不为空 .csv
    name = _download_filename(board, node)
    assert name.endswith(".csv")
    assert name != ".csv"


def test_node_page_has_download_button(client):
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    page = client.get(f"/board/{board_id}/node/{ws}").text
    assert f"/board/{board_id}/node/{ws}/download" in page
