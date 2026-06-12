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


def _setup_board(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0]      # /board/{id}


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


def test_node_detail_shows_changes_panel_and_badges(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    ws = _workspace_id(client, board_id)
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "本节点修改" in r.text
    assert "撤销" in r.text
    assert "47k" in r.text and "10k" in r.text   # 新值 + 划线旧值


def test_state_graph_shows_summary(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    r = client.get(f"/board/{board_id}")
    assert "工作区草稿" in r.text
    assert "R1" in r.text and "修改" in r.text
    assert "初始状态" in r.text


def test_committed_node_shows_history_warning(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    ws = _workspace_id(client, board_id)
    client.post(f"/board/{board_id}/commit", data={"message": "S1"})
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "修正历史记录" in r.text
    assert "撤销" not in r.text            # 已提交节点无撤销入口


def test_home_groups_by_board_name(client):
    loc = _setup_board(client)
    r = client.get("/")
    assert "新建单板" in r.text           # 唯一新建入口
    assert "板 3" in r.text               # 单板芯片
    assert "1 个位号" in r.text           # 版本卡片统计


def test_home_empty_state(client):
    r = client.get("/")
    assert "还没有" in r.text


def test_create_board_with_new_version(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/board/")


def test_create_second_board_on_existing_version_without_csv(client):
    _setup_board(client)
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "4"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_preview_existing_version_needs_no_csv(client):
    _setup_board(client)
    r = client.post("/board/new/preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"})
    assert "已有版本" in r.text
    assert "disabled" not in r.text


def test_preview_blocks_on_csv_problems(client):
    csv = 'Reference,Part\n"R1,R2",10k\nR1,22k\n'
    r = client.post("/board/new/preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"},
                    files={"file": ("bom.csv", csv, "text/csv")})
    assert "校验问题" in r.text
    assert "disabled" in r.text


def test_preview_new_version_without_csv_warns(client):
    r = client.post("/board/new/preview",
                    data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA"})
    assert "请选择" in r.text
    assert "disabled" in r.text


def test_create_rejects_csv_with_problems(client):
    csv = 'Reference,Part\n"R1,R2",10k\nR1,22k\n'
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", csv, "text/csv")})
    assert r.status_code == 400


def test_create_rejects_new_version_without_csv(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"})
    assert r.status_code == 400


def test_board_new_page_loads(client):
    r = client.get("/board/new")
    assert r.status_code == 200
    assert "新建单板" in r.text


def test_log_page_filter_by_reference(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    r = client.get(f"/board/{board_id}/log?reference=R9")
    assert "R1" not in r.text.split("</form>")[-1]   # 表格区不含 R1
    r2 = client.get(f"/board/{board_id}/log?reference=R1")
    assert "47k" in r2.text


def test_log_page_ignores_invalid_node_param(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.get(f"/board/{board_id}/log?node=abc")
    assert r.status_code == 200


def test_create_rejects_blank_board_uid(client):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "   "},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")})
    assert r.status_code == 400


def test_create_rejects_duplicate_board_uid(client):
    _setup_board(client)   # 已建 B/v1/bomA 下 board_uid=3
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    follow_redirects=False)
    assert r.status_code == 400


def test_create_allows_same_uid_in_different_bom_version(client):
    _setup_board(client)   # B/v1/bomA 下 board_uid=3
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomB", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303


def test_delete_board(client):
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    r = client.delete(f"/board/{board_id}")
    assert r.status_code == 200
    assert r.headers.get("hx-redirect") == "/"
    assert client.get(f"/board/{board_id}").status_code == 404


def test_delete_board_404_if_missing(client):
    r = client.delete("/board/99999")
    assert r.status_code == 404


def test_delete_bom_version(client):
    _setup_board(client)
    r = client.delete("/bom-version?board_name=B&pcb_version=v1&bom_version=bomA")
    assert r.status_code == 200
    assert r.headers.get("hx-redirect") == "/"
    assert client.get("/").status_code == 200
    assert "bomA" not in client.get("/").text


def test_delete_bom_version_404_if_missing(client):
    r = client.delete("/bom-version?board_name=X&pcb_version=v1&bom_version=bomX")
    assert r.status_code == 404


def test_delete_board_group(client):
    _setup_board(client)
    r = client.delete("/board-group?board_name=B")
    assert r.status_code == 200
    assert r.headers.get("hx-redirect") == "/"
    home = client.get("/").text
    assert "bomA" not in home


# ── 补充删除功能边界测试 ──────────────────────────────────────────────

def test_home_shows_delete_buttons(client):
    """首页有数据时应渲染三层删除按钮。"""
    _setup_board(client)
    html = client.get("/").text
    assert "hx-delete" in html
    assert "del-icon" in html
    assert "hx-confirm" in html
    assert "chip-del" in html


def test_delete_board_with_committed_history(client):
    """有 commit 节点的单板删除后，单板页和节点页均 404。"""
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "22k"})
    commit_r = client.post(f"/board/{board_id}/workspace/commit",
                           data={"message": "测试提交"}, follow_redirects=False)
    node_url = commit_r.headers.get("location", "")

    client.delete(f"/board/{board_id}")

    assert client.get(f"/board/{board_id}").status_code == 404
    if node_url and "/node/" in node_url:
        assert client.get(node_url.split("?")[0]).status_code == 404


def test_delete_bom_version_cascades_multiple(client):
    """BOM 版本下有 2 块板，删版本后两块板均 404。"""
    loc1 = _setup_board(client)
    board_id1 = loc1.rsplit("/", 1)[-1]
    r2 = client.post("/board/new",
                     data={"board_name": "B", "pcb_version": "v1",
                           "bom_version": "bomA", "board_uid": "4"},
                     follow_redirects=False)
    board_id2 = r2.headers["location"].split("?")[0].rsplit("/", 1)[-1]

    client.delete("/bom-version?board_name=B&pcb_version=v1&bom_version=bomA")

    assert client.get(f"/board/{board_id1}").status_code == 404
    assert client.get(f"/board/{board_id2}").status_code == 404


def test_delete_board_group_nonexistent(client):
    """删除不存在的单板组静默成功（200 + HX-Redirect）。"""
    r = client.delete("/board-group?board_name=不存在的名字")
    assert r.status_code == 200
    assert r.headers.get("hx-redirect") == "/"


def test_can_recreate_after_board_delete(client):
    """删除单板后，同名 board_uid 可在同 BOM 版本下重新创建。"""
    loc = _setup_board(client)
    board_id = loc.rsplit("/", 1)[-1]
    client.delete(f"/board/{board_id}")

    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    follow_redirects=False)
    assert r.status_code == 303


def test_can_recreate_after_bom_version_delete(client):
    """删 BOM 版本后，可重新用同版本号上传 CSV 创建。"""
    _setup_board(client)
    client.delete("/bom-version?board_name=B&pcb_version=v1&bom_version=bomA")

    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "3"},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303


def test_delete_board_group_clears_home(client):
    """删组后首页不再包含该 BOM 版本数据。"""
    _setup_board(client)
    client.delete("/board-group?board_name=B")
    home = client.get("/").text
    assert "bomA" not in home
