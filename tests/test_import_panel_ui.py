"""导入预览面板瘦身 + 一键清除修改的界面行为。

覆盖：lint 提示合并进变更行（ⓘ/⚠ 图标）、超过 10 行折叠、lint 中的状态指示、
预览态「取消全部」与草稿态「清除全部修改」。
"""
import pytest
from fastapi.testclient import TestClient

from app import models
from app.main import get_conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client, bom=b"Reference,Part\nR1,10k\nC1,100nF\n"):
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "SN1"},
                    files={"file": ("bom.csv", bom, "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _workspace_id(board_id):
    return models.workspace_node(get_conn(), board_id)["id"]


def _preview(client, board_id, node_id, csv_bytes, mode="diff"):
    return client.post(f"/board/{board_id}/node/{node_id}/import/preview",
                       data={"mode": mode},
                       files={"file": ("changes.csv", csv_bytes, "text/csv")})


def _changeset(node_id):
    return {c["reference"]: (c["op"], c["part"])
            for c in models.get_changeset(get_conn(), node_id)}


# ── 1. lint 提示并入变更行 ──────────────────────────────────────

def test_fix_note_renders_as_inline_info_icon_not_a_separate_list(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR9,1000pF,add\n")
    assert "icon-info" in r.text
    assert "修正: 1000pF → 1nF" in r.text
    # 不再有「已自动修正 N 处」汇总条和独立的问题清单 <ul>
    assert "已自动修正" not in r.text
    assert "problem-list" not in r.text


def test_warning_note_renders_as_inline_warn_icon(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR9,230R,add\n")
    assert "icon-warn" in r.text
    assert "不是标准" in r.text
    assert "不是标准件" not in r.text  # 旧的独立汇总条文案已移除


def test_problem_row_is_inline_and_still_blocks_apply(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR1,22k,add\n")
    assert "已存在" in r.text
    assert "row-problem" in r.text
    assert "hx-vals" not in r.text  # 有问题行 → 不给应用按钮


# ── 2. 默认只展示前 10 条 ───────────────────────────────────────

_ELEVEN = b"Reference,Part,OP\n" + b"".join(
    f"X{i},1k,add\n".encode() for i in range(11))


def test_rows_beyond_ten_are_collapsed_behind_show_all(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, _ELEVEN)
    assert "查看全部" in r.text
    assert r.text.count("x-show=\"showAll\"") == 1  # 第 11 行被折叠


def test_ten_rows_need_no_show_all_button(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    csv = b"Reference,Part,OP\n" + b"".join(
        f"X{i},1k,add\n".encode() for i in range(10))
    r = _preview(client, board_id, ws, csv)
    assert "查看全部" not in r.text


def test_problem_rows_are_never_collapsed(client):
    """问题行排在最前且不计入折叠，否则用户看不到「为什么不能应用」。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    csv = b"Reference,Part,OP\n" + b"".join(
        f"X{i},1k,add\n".encode() for i in range(11)) + b"R1,22k,add\n"
    r = _preview(client, board_id, ws, csv)
    problem_pos = r.text.index("已存在")
    assert problem_pos < r.text.index("X0")


# ── 3. lint 中的状态指示 ────────────────────────────────────────

def test_import_form_has_a_checking_indicator(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "hx-indicator" in r.text
    assert "正在检查" in r.text


def test_edit_form_part_input_has_a_lint_indicator(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.get(f"/board/{board_id}/node/{ws}")
    assert "lint-indicator" in r.text


def test_lint_part_route_reports_the_fix_message(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/lint-part",
                    data={"reference": "R9", "part": "1000pF"})
    import json as _json
    trigger = _json.loads(r.headers["HX-Trigger"])["partLinted"]
    assert trigger["part"] == "1nF"
    assert "1000pF → 1nF" in trigger["fix"]


def test_lint_part_route_reports_empty_fix_when_value_is_clean(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/lint-part",
                    data={"reference": "R9", "part": "1nF"})
    import json as _json
    assert _json.loads(r.headers["HX-Trigger"])["partLinted"]["fix"] == ""


# ── 4. 取消全部 / 清除全部修改 ──────────────────────────────────

def test_preview_offers_a_red_cancel_all_button(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR9,1uF,add\n")
    assert "取消全部" in r.text
    assert "btn-outline danger" in r.text


def test_preview_with_problems_has_no_cancel_button(client):
    """没有可应用的修改时不需要「取消全部」——只有一个文件选择框要清。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR1,22k,add\n")
    assert "取消全部" not in r.text


def test_undo_all_clears_the_whole_draft_changeset(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R9", "op": "add", "part": "1uF"})
    assert len(_changeset(ws)) == 2

    r = client.post(f"/board/{board_id}/node/{ws}/undo-all")
    assert r.status_code == 200
    assert _changeset(ws) == {}
    import json as _json
    assert "已清除全部 2 条修改" in _json.loads(r.headers["HX-Trigger"])["showToast"]


def test_undo_all_writes_no_audit_log(client):
    """与单条撤销一致：草稿撤销不记审计日志。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    before = len(models.list_board_log(get_conn(), board_id))
    client.post(f"/board/{board_id}/node/{ws}/undo-all")
    assert len(models.list_board_log(get_conn(), board_id)) == before


def test_undo_all_rejected_on_committed_node(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "m"},
                follow_redirects=False)
    committed = ws
    r = client.post(f"/board/{board_id}/node/{committed}/undo-all")
    assert "已提交" in r.text
    assert len(_changeset(committed)) == 1  # 未被清空


def test_changes_panel_shows_clear_all_only_when_there_are_changes(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    assert "清除全部修改" not in client.get(f"/board/{board_id}/node/{ws}").text
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    html = client.get(f"/board/{board_id}/node/{ws}").text
    assert "清除全部修改" in html
    assert "hx-confirm" in html
