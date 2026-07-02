import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("REFLOW_UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.main import create_app
    return TestClient(create_app())


def _new_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "SN1"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def _workspace_node(client, bid):
    r = client.get(f"/board/{bid}")
    import re
    m = re.search(rf"/board/{bid}/node/(\d+)", r.text)
    # 取状态图里出现的草稿节点链接
    return m.group(1)


def test_node_detail_has_attachments_region(client):
    bid = _new_board(client)
    r = client.get(f"/board/{bid}")
    import re
    m = re.search(rf"/board/{bid}/node/(\d+)", r.text)
    nid = m.group(1)
    page = client.get(f"/board/{bid}/node/{nid}")
    assert page.status_code == 200
    assert "附件" in page.text
    assert "id=\"attachments\"" in page.text


def test_upload_attachment_appears_in_list(client):
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    r = client.post(f"/board/{bid}/node/{nid}/attachments",
                    files=[("files", ("schematic.sch", b"SCHBINARY", "application/octet-stream"))])
    assert r.status_code == 200
    assert "schematic.sch" in r.text
    assert r.headers.get("HX-Trigger") is not None   # toast


def test_upload_any_file_type_allowed(client):
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    r = client.post(f"/board/{bid}/node/{nid}/attachments",
                    files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 200
    assert "notes.txt" in r.text


def test_upload_no_files_rerenders_empty(client):
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    r = client.post(f"/board/{bid}/node/{nid}/attachments", files=[])
    assert r.status_code == 200


def test_download_attachment_returns_content(client):
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    client.post(f"/board/{bid}/node/{nid}/attachments",
                files=[("files", ("r.sch", b"DOWNSRC", "application/octet-stream"))])
    from app import models
    from app.main import get_conn
    rows = models.list_node_attachments(get_conn(), int(nid))
    aid = rows[0]["id"]
    r = client.get(f"/board/{bid}/node/{nid}/attachments/{aid}/download")
    assert r.status_code == 200
    assert r.content == b"DOWNSRC"
    assert "r.sch" in r.headers["content-disposition"]


def test_delete_attachment_removes_from_list(client):
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    client.post(f"/board/{bid}/node/{nid}/attachments",
                files=[("files", ("gone.pdf", b"%PDF-", "application/pdf"))])
    from app import models
    from app.main import get_conn
    rows = models.list_node_attachments(get_conn(), int(nid))
    aid = rows[0]["id"]
    r = client.post(f"/board/{bid}/node/{nid}/attachments/{aid}/delete")
    assert r.status_code == 200
    assert "gone.pdf" not in r.text
    assert models.list_node_attachments(get_conn(), int(nid)) == []


def test_download_404_for_unknown_attachment(client):
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    r = client.get(f"/board/{bid}/node/{nid}/attachments/99999/download")
    assert r.status_code == 404


def test_attachment_files_dropped_when_board_deleted(client, tmp_path):
    import os
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    client.post(f"/board/{bid}/node/{nid}/attachments",
                files=[("files", ("a.sch", b"X", "application/octet-stream"))])
    from app import models, storage
    from app.main import get_conn
    rows = models.list_node_attachments(get_conn(), int(nid))
    # 文件确实落盘
    p = os.path.join(tmp_path / "uploads", rows[0]["storage_path"])
    assert os.path.exists(p)
    client.delete(f"/board/{bid}")
    assert not os.path.exists(p)


def test_state_graph_shows_paperclip_for_nodes_with_attachments(client):
    """issue #104：含附件的节点卡片上显示回形针标志，无附件的节点不显示。"""
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    # 无附件时不应出现回形针标记（sprite 定义不算使用）
    r0 = client.get(f"/board/{bid}")
    assert r0.status_code == 200
    assert 'href="#icon-paperclip"' not in r0.text
    # 给草稿节点上传一个附件
    client.post(f"/board/{bid}/node/{nid}/attachments",
                files=[("files", ("schematic.sch", b"SCH", "application/octet-stream"))])
    r = client.get(f"/board/{bid}")
    assert r.status_code == 200
    # 恰好一处使用回形针图标（只有带附件的草稿节点）
    assert r.text.count('href="#icon-paperclip"') == 1
    assert "含附件" in r.text


def test_upload_oversized_file_rejected(client, monkeypatch):
    from app import attachments
    monkeypatch.setattr(attachments, "MAX_ATTACHMENT_BYTES", 4)
    bid = _new_board(client)
    nid = _workspace_node(client, bid)
    r = client.post(f"/board/{bid}/node/{nid}/attachments",
                    files=[("files", ("big.bin", b"12345", "application/octet-stream"))])
    assert r.status_code == 200
    import json
    toast = json.loads(r.headers["HX-Trigger"])["showToast"]
    assert "超过" in toast and "big.bin" in toast
    from app import models
    from app.main import get_conn
    assert models.list_node_attachments(get_conn(), int(nid)) == []  # 未写入 DB