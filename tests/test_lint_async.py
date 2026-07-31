"""元器件值 lint 异步化：批量端点、面板三态、一次性 ⓘ。"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client, board_name="B", board_uid="3"):
    r = client.post("/board/new",
                    data={"board_name": board_name, "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": board_uid},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def _workspace_id(client, board_id):
    from app import models
    from app.main import get_conn
    return models.workspace_node(get_conn(), int(board_id))["id"]


def _root_node_id(client, board_id):
    from app import models
    from app.main import get_conn
    nodes = models.list_nodes(get_conn(), int(board_id))
    root = next(n for n in nodes if n["parent_id"] is None)
    return root["id"]


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
    assert r.status_code == 200
    assert "R7" in r.text
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
    assert r.status_code == 200
    assert 'id="changes-panel"' in r.text
    assert 'hx-trigger="load"' not in r.text


def test_lint_changes_rejects_foreign_node(client):
    """节点存在但属于另一块单板时 404（归属校验分支，不是路由缺失）。"""
    board_id = _setup_board(client, board_name="B1", board_uid="3")
    other_board_id = _setup_board(client, board_name="B2", board_uid="4")
    other_ws = _workspace_id(client, other_board_id)
    r = client.post(f"/board/{board_id}/node/{other_ws}/lint-changes")
    assert r.status_code == 404


def test_node_page_defers_lint_to_async_request(client):
    """节点页首次渲染不算 lint，只挂异步触发器 + 占位。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert r.status_code == 200
    assert "lint-changes" in r.text
    assert 'hx-trigger="load"' in r.text
    assert "lint-checking" in r.text
    assert "不是标准" not in r.text


def test_panel_shows_warning_once_lint_arrives(client):
    """异步结果回来后，占位换成 ⚠，且不再有占位。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "R7", "230R")
    r = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert r.status_code == 200
    assert "不是标准" in r.text
    assert "lint-checking" not in r.text


def test_edit_form_has_no_blur_lint(client):
    """输入路径上不能再有任何 lint 往返——这是本次改动的核心诉求。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "lint-part" not in r.text
    assert 'hx-trigger="blur"' not in r.text
    assert "lint-indicator" not in r.text


def test_lint_part_route_is_gone(client):
    """旧的单值 lint 路由已删除，不留死路由。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/lint-part",
                    data={"reference": "R1", "part": "1000pF"})
    assert r.status_code == 404


def test_edit_response_shows_the_fix_once(client):
    """写入响应里带 ⓘ 告诉用户值被改成了什么。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = _add(client, board_id, ws, "C3", "1000pF")
    assert "1000pF → 1nF" in r.text


def test_fix_note_is_not_persisted(client):
    """ⓘ 是一次性的：刷新页面后不再出现（原值在归一后不留存，无法重算）。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    _add(client, board_id, ws, "C3", "1000pF")
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert r.status_code == 200
    assert "1000pF → 1nF" not in r.text
    r2 = client.post(f"/board/{board_id}/node/{ws}/lint-changes")
    assert "1000pF → 1nF" not in r2.text
    assert "1nF" in r2.text          # 值本身归一成功了


def test_edit_response_carries_warning_and_does_not_retrigger(client):
    """写入响应自带完整 ⚠，且不挂 load——否则自动刷新会把一次性 ⓘ 冲掉。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = _add(client, board_id, ws, "R7", "230R")
    assert "不是标准" in r.text
    assert 'hx-trigger="load"' not in r.text


def test_root_edit_shows_orphan_warning(client):
    """根节点的改动写 initial_bom 不进 node_changes，面板恒空、没有行可挂 ⚠——
    靠表单下方的一次性提示兜底，否则这条反馈会完全消失（见修复报告第 1 项）。"""
    board_id = _setup_board(client)
    root = _root_node_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{root}/edit",
                    data={"reference": "R1", "op": "modify", "part": "230R"})
    assert r.status_code == 200
    assert "不是标准" in r.text


def test_root_edit_standard_value_has_no_warning(client):
    """负对照：标准值不应触发兜底警告。"""
    board_id = _setup_board(client)
    root = _root_node_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{root}/edit",
                    data={"reference": "R1", "op": "modify", "part": "10k"})
    assert r.status_code == 200
    assert "不是标准" not in r.text


def test_root_edit_shows_orphan_fix(client):
    """根节点提交会被归一的写法（1000pF → 1nF），兜底位也要带上 ⓘ 文案。"""
    r0 = client.post("/board/new",
                      data={"board_name": "RootFix", "pcb_version": "v1",
                            "bom_version": "bomA", "board_uid": "9"},
                      files={"file": ("bom.csv",
                                      "Reference,Part\nR1,10k\nC1,100pF\n", "text/csv")},
                      follow_redirects=False)
    board_id = r0.headers["location"].split("?")[0].rsplit("/", 1)[-1]
    root = _root_node_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{root}/edit",
                    data={"reference": "C1", "op": "modify", "part": "1000pF"})
    assert r.status_code == 200
    assert "1000pF → 1nF" in r.text


def test_workspace_edit_does_not_duplicate_orphan_warning(client):
    """普通工作区节点：兜底提示不出现（避免与面板行图标重复），但面板行的 ⚠ 仍在。"""
    board_id = _setup_board(client)
    ws = _workspace_id(client, board_id)
    r = _add(client, board_id, ws, "R7", "230R")
    assert r.status_code == 200
    assert "不是标准" in r.text          # 面板行的 ⚠ 仍在
    assert "flash flash-warn" not in r.text  # 但没有多出一份兜底 flash


def test_validation_failure_clears_lint_flash(client):
    """校验失败的响应只换 #form-error，必须顺带 OOB 清空 #lint-flash——否则上一次
    成功编辑留下的一次性提示（不带位号标签）会残留在表单下方，容易被误读成与
    这次失败的提交相关。"""
    board_id = _setup_board(client)
    root = _root_node_id(client, board_id)
    r = client.post(f"/board/{board_id}/node/{root}/edit",
                    data={"reference": "R99", "op": "modify", "part": "10k"})
    assert r.status_code == 200
    assert r.headers.get("HX-Retarget") == "#form-error"
    assert "不存在" in r.text
    assert '<div id="lint-flash" hx-swap-oob="true"></div>' in r.text
    assert "flash-info" not in r.text
