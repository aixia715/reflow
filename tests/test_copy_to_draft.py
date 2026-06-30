"""issue #57：一键将已有节点的修改添加到工作区草稿 + 草稿修改行补齐「修改」「不贴」按钮。"""
import pytest
from fastapi.testclient import TestClient
from app import models
from app.main import get_conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client, uid="3"):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": uid},
                    files={"file": ("bom.csv",
                            b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _commit(client, board_id, message, description=""):
    """提交当前草稿，返回新提交节点 id。调用方应已通过 workspace/edit 添加修改。"""
    client.post(f"/board/{board_id}/commit",
                data={"message": message, "description": description},
                follow_redirects=False)
    conn = get_conn()
    committed = [n for n in models.list_nodes(conn, board_id)
                 if n["is_committed"] and n["parent_id"] is not None]
    return committed[-1]["id"]


def _workspace_id(board_id):
    return models.workspace_node(get_conn(), board_id)["id"]


# ── 一键复制到草稿 ──────────────────────────────────────────────────

def test_copy_to_draft_copies_changeset(client):
    """复制源节点全部 changeset（位号+op+part）到草稿。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "1uF"})
    src = _commit(client, board_id, "改 R1 加 C9")
    r = client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    assert r.status_code == 200
    conn = get_conn()
    ws = _workspace_id(board_id)
    assert models.get_change(conn, ws, "R1") == {
        "reference": "R1", "op": "modify", "part": "47k"}
    assert models.get_change(conn, ws, "C9") == {
        "reference": "C9", "op": "add", "part": "1uF"}


def test_copy_to_draft_includes_remove_op(client):
    """remove 操作也一并复制（草稿显式设为不贴）。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C1", "op": "remove"})
    src = _commit(client, board_id, "不贴 C1")
    client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    conn = get_conn()
    ws = _workspace_id(board_id)
    ch = models.get_change(conn, ws, "C1")
    assert ch["op"] == "remove"


def test_copy_to_draft_overwrites_same_reference(client):
    """覆盖策略：草稿已有同位号 op 时，以复制值覆盖。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    src = _commit(client, board_id, "改 R1 47k")
    # 草稿预先有 R1=22k
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "22k"})
    client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    conn = get_conn()
    ws = _workspace_id(board_id)
    assert models.get_change(conn, ws, "R1")["part"] == "47k"


def test_copy_to_draft_preserves_draft_unique_changes(client):
    """草稿独有的其他修改保留。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    src = _commit(client, board_id, "改 R1")
    # 草稿有 C1=10nF（源节点没动过 C1）
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C1", "op": "modify", "part": "10nF"})
    client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    conn = get_conn()
    ws = _workspace_id(board_id)
    assert models.get_change(conn, ws, "R1")["part"] == "47k"
    assert models.get_change(conn, ws, "C1")["part"] == "10nF"


def test_copy_to_draft_fills_title_when_empty(client):
    """标题/说明：仅当草稿为空时填入。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    src = _commit(client, board_id, "改 R1", "详细说明")
    client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    conn = get_conn()
    ws = models.workspace_node(conn, board_id)
    assert ws["message"] == "改 R1"
    assert ws["description"] == "详细说明"


def test_copy_to_draft_does_not_overwrite_title(client):
    """草稿已有标题时不覆盖。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    src = _commit(client, board_id, "源标题", "源说明")
    ws = _workspace_id(board_id)
    models.update_node_info(get_conn(), ws, "草稿标题", "草稿说明")
    client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    conn = get_conn()
    ws_row = models.workspace_node(conn, board_id)
    assert ws_row["message"] == "草稿标题"
    assert ws_row["description"] == "草稿说明"


def test_copy_to_draft_partial_title_fill(client):
    """草稿有标题但无说明时，仅填入说明，不动标题。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    src = _commit(client, board_id, "源标题", "源说明")
    ws = _workspace_id(board_id)
    models.update_node_info(get_conn(), ws, "已有标题", "")
    client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    conn = get_conn()
    ws_row = models.workspace_node(conn, board_id)
    assert ws_row["message"] == "已有标题"
    assert ws_row["description"] == "源说明"


def test_copy_to_draft_redirects_to_draft_page(client):
    """复制后 HX-Redirect 跳转到草稿节点页并带 flash。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    src = _commit(client, board_id, "改 R1")
    r = client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    ws = _workspace_id(board_id)
    redirect = r.headers.get("HX-Redirect", "")
    assert redirect.startswith(f"/board/{board_id}/node/{ws}?flash=")
    from urllib.parse import unquote
    assert "已复制" in unquote(redirect)


def test_copy_to_draft_rejects_root(client):
    """根节点没有修改可复制。"""
    board_id = _setup_board(client)
    root = models.list_nodes(get_conn(), board_id)[0]["id"]
    r = client.post(f"/board/{board_id}/node/{root}/copy-to-draft")
    assert r.status_code == 400


def test_copy_to_draft_rejects_draft_itself(client):
    """不能复制草稿到自身。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/copy-to-draft")
    assert r.status_code == 400


def test_copy_to_draft_unknown_node_404(client):
    board_id = _setup_board(client)
    r = client.post(f"/board/{board_id}/node/9999/copy-to-draft")
    assert r.status_code == 404


def test_copy_to_draft_unknown_board_404(client):
    r = client.post("/board/9999/node/1/copy-to-draft")
    assert r.status_code == 404


def test_copy_to_draft_records_audit_log(client):
    """复制操作对每条修改记一条 direct 审计日志。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "1uF"})
    src = _commit(client, board_id, "改 R1 加 C9")
    client.post(f"/board/{board_id}/node/{src}/copy-to-draft")
    conn = get_conn()
    ws = _workspace_id(board_id)
    from app.audit import list_log
    logs = list_log(conn, ws)
    refs_in_log = {r["reference"] for r in logs}
    assert "R1" in refs_in_log
    assert "C9" in refs_in_log


def test_state_graph_has_copy_to_draft_button(client):
    """状态图 ⋯ 菜单里有「添加到草稿」按钮（非根已提交节点）。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    _commit(client, board_id, "改 R1")
    page = client.get(f"/board/{board_id}").text
    assert "copy-to-draft" in page
    assert "添加到草稿" in page


def test_state_graph_no_copy_button_on_root(client):
    """根节点不出现「添加到草稿」入口（根无修改可复制）。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    _commit(client, board_id, "改 R1")
    conn = get_conn()
    root = models.list_nodes(conn, board_id)[0]["id"]
    page = client.get(f"/board/{board_id}").text
    assert f"/board/{board_id}/node/{root}/copy-to-draft" not in page


def test_node_detail_has_copy_to_draft_button(client):
    """已提交非根节点详情页有「添加到草稿」按钮。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    src = _commit(client, board_id, "改 R1")
    page = client.get(f"/board/{board_id}/node/{src}").text
    assert "copy-to-draft" in page
    assert "添加到草稿" in page


def test_node_detail_no_copy_button_on_draft(client):
    """草稿节点详情页没有「添加到草稿」按钮。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    page = client.get(f"/board/{board_id}/node/{ws}").text
    assert "copy-to-draft" not in page


# ── 草稿修改行：笔图标回填编辑表单 + 撤销纯图标（issue 92）────────────

def test_draft_mine_modify_row_has_pencil_and_undo_icons(client):
    """草稿修改行（mine+modify）：笔图标回填编辑表单（合并原「修改」「不贴」、默认 modify），
    撤销改为纯箭头图标、去掉「撤销」二字，两个图标按钮都带 title。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    ws = _workspace_id(board_id)
    page = client.get(f"/board/{board_id}/node/{ws}").text
    # 笔图标：点击回填，默认 modify；不再有独立「不贴」回填按钮
    assert 'fill("R1", "modify"' in page
    assert 'fill("R1", "remove"' not in page
    assert "#icon-pencil" in page
    assert 'title="修改"' in page
    # 撤销改为纯箭头图标，去掉「撤销」二字
    assert "↩ 撤销" not in page
    assert "#icon-undo" in page
    assert 'title="撤销"' in page


def test_draft_mine_add_row_has_pencil_icon(client):
    """草稿新增行（mine+add）：笔图标回填编辑表单（默认 modify）。"""
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "1uF"})
    ws = _workspace_id(board_id)
    page = client.get(f"/board/{board_id}/node/{ws}").text
    assert 'fill("C9", "modify"' in page
    assert 'fill("C9", "remove"' not in page
    assert "#icon-pencil" in page
