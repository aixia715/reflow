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


# ---- scope=changes：只下载本节点相对父节点的修改项（issue #134）----

def _root_id(client, board_id):
    from app import models
    from app.main import get_conn
    return [n for n in models.list_nodes(get_conn(), board_id)
            if n["parent_id"] is None][0]["id"]


def _draft_with_three_ops(client, board_id):
    """工作区草稿里造出 add / modify / remove 三类修改，返回草稿节点 id。"""
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "U1", "op": "add", "part": "MCU"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C1", "op": "remove", "part": ""})
    return _workspace_id(client, board_id)


def test_download_changes_scope(client):
    board_id = _setup_board(client)
    ws = _draft_with_three_ops(client, board_id)
    r = client.get(f"/board/{board_id}/node/{ws}/download?scope=changes")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    lines = r.text.lstrip("﻿").splitlines()
    assert lines[0] == "Reference,Part,OP"
    assert lines[1:] == ["C1,,remove", "R1,47k,modify", "U1,MCU,add"]


def test_download_changes_excludes_inherited_rows(client):
    """只出本节点 changeset：没动过的位号（继承自上游）不在修改项里。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    ws = _workspace_id(client, board_id)
    text = client.get(f"/board/{board_id}/node/{ws}/download?scope=changes").text
    assert "R1,47k,modify" in text
    # C1 在折叠 BOM 里，但本节点没动过它
    assert "C1" not in text


def test_download_scope_full_is_default(client):
    """不带 scope（老链接/老书签）与 scope=full 都还是完整 BOM。"""
    board_id = _setup_board(client)
    ws = _draft_with_three_ops(client, board_id)
    default = client.get(f"/board/{board_id}/node/{ws}/download").text
    explicit = client.get(f"/board/{board_id}/node/{ws}/download?scope=full").text
    assert default == explicit
    assert default.lstrip("﻿").splitlines()[0] == "Reference,Part"


def test_download_changes_root_node_400(client):
    """根节点没有「相对父节点的修改」，不给空文件，直接报错。"""
    board_id = _setup_board(client)
    root = _root_id(client, board_id)
    r = client.get(f"/board/{board_id}/node/{root}/download?scope=changes")
    assert r.status_code == 400


def test_download_bad_scope_400(client):
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = client.get(f"/board/{board_id}/node/{ws}/download?scope=whatever")
    assert r.status_code == 400


def test_download_changes_filename_marked(client):
    from urllib.parse import unquote
    board_id = _setup_board(client)
    ws = _draft_with_three_ops(client, board_id)
    cd = client.get(f"/board/{board_id}/node/{ws}/download?scope=changes"
                    ).headers["content-disposition"]
    name = unquote(cd.split("filename*=UTF-8''", 1)[1])
    assert name.endswith("_修改项.csv")


def test_download_changes_roundtrips_into_change_import(client):
    """导出的修改项 CSV 能被「从 CSV 导入修改」读回（在别处重放同一批修改）。"""
    from app.csv_import import parse_change_csv
    board_id = _setup_board(client)
    ws = _draft_with_three_ops(client, board_id)
    text = client.get(f"/board/{board_id}/node/{ws}/download?scope=changes").text
    entries, problems = parse_change_csv(text)
    assert problems == []
    assert [(e.reference, e.op) for e in entries] == [
        ("C1", "remove"), ("R1", "modify"), ("U1", "add"),
    ]


def test_node_page_has_both_download_links(client):
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    page = client.get(f"/board/{board_id}/node/{ws}").text
    assert f"/board/{board_id}/node/{ws}/download?scope=full" in page
    assert f"/board/{board_id}/node/{ws}/download?scope=changes" in page


def test_root_node_page_has_no_changes_link(client):
    board_id = _setup_board(client)
    root = _root_id(client, board_id)
    page = client.get(f"/board/{board_id}/node/{root}").text
    assert f"/board/{board_id}/node/{root}/download?scope=full" in page
    assert "scope=changes" not in page
