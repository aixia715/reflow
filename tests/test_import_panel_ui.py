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


def test_lint_icons_use_data_tip_not_native_title(client):
    """提示走 data-tip + CSS 气泡：原生 title 要停悬约 1 秒才出、几秒后自动消失、
    且不移出元素就不再复现，扫视列表时基本等于不出现。两者并存还会叠出两个气泡。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR9,1000pF,add\nR8,230R,add\n")
    assert 'data-tip="修正: 1000pF → 1nF"' in r.text
    assert 'data-tip="警告: ' in r.text
    assert "lint-note" in r.text and 'title="修正' not in r.text
    # 键盘/触屏也要能唤出，故图标可聚焦；role 让读屏器播报 aria-label
    assert 'class="lint-note lint-fix" tabindex="0" role="img"' in r.text


def test_checking_placeholder_also_uses_data_tip(client):
    """面板首屏那个「检查中」占位圆点也是 .lint-note，提示同样走 data-tip。

    它跟异步补上来的 ⓘ/⚠ 在同一个位置前后脚出现，命中区和气泡必须是同一套：
    留着原生 title 的话，一个要停悬 1 秒、一个即时，且两者宽度不一致会让行抖。
    """
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "230R"})
    html = client.get(f"/board/{board_id}/node/{ws}").text
    assert "lint-checking" in html
    assert 'data-tip="正在检查元器件值' in html
    assert 'title="正在检查' not in html


def test_changes_panel_lint_icon_also_uses_data_tip(client):
    """面板行的 lint 结果由 /lint-changes 异步补上，那条路径也必须是 data-tip。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "230R"})
    html = client.post(f"/board/{board_id}/node/{ws}/lint-changes").text
    assert 'data-tip="警告: ' in html
    assert "title=\"警告" not in html


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
    assert "查看全部（还有 1 条修改）" in r.text
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


# ── 4. 取消全部 / 清除全部修改 ──────────────────────────────────

def test_preview_offers_a_red_cancel_all_button(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR9,1uF,add\n")
    assert "取消全部" in r.text
    assert "btn-outline danger" in r.text


def test_preview_with_problems_still_offers_cancel_without_apply(client):
    """有问题行时预览照样占着一屏，必须给清除入口；但不能给应用按钮。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR1,22k,add\n")
    assert "取消全部" in r.text
    assert "应用这" not in r.text and "hx-vals" not in r.text


def test_unreadable_file_message_also_offers_cancel(client):
    """文件被拒时最需要这个按钮：改好同名文件再选一次不触发 change 事件。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, "Reference,Part\nR1,电阻\n".encode("gbk"))
    assert "UTF-8" in r.text
    assert "取消全部" in r.text


def test_empty_preview_has_no_buttons(client):
    """一条修改都没有、也没问题的 CSV：没什么可取消的，不摆按钮。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\n")
    assert "没有可导入的修改" in r.text
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


def test_workspace_draft_is_always_the_chain_tail(client):
    """undo-all 不做传播的前提：草稿永远挂在链末、没有下游节点。

    一次删光整个 changeset 会同时改动 N 个位号的解析值；只有「草稿无下游」这条
    结构不变量成立，才轮不到冲突检测。这里守住它，别让日后新增的挂接路径打破。
    """
    board_id = _setup_board(client)
    conn = get_conn()
    ws = _workspace_id(board_id)
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE parent_id=?",
                        (ws,)).fetchone()[0] == 0

    # 提交后：旧草稿转正、新开的空草稿仍在链末
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "m"},
                follow_redirects=False)
    ws2 = _workspace_id(board_id)
    assert ws2 != ws
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE parent_id=?",
                        (ws2,)).fetchone()[0] == 0

    # 插入节点也不会给草稿挂下游：父子两端都必须是已提交节点
    r = client.post(f"/board/{board_id}/node/{ws}/insert",
                    data={"changes": '[{"reference":"R1","op":"modify","part":"1k"}]',
                          "message": "x", "committed_at": "2026-01-01T00:00:00+00:00"})
    assert "不可插入" in r.text
    assert conn.execute("SELECT COUNT(*) FROM nodes WHERE parent_id=?",
                        (ws2,)).fetchone()[0] == 0


def test_changes_panel_shows_clear_all_only_when_there_are_changes(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    assert "清除全部修改" not in client.get(f"/board/{board_id}/node/{ws}").text
    client.post(f"/board/{board_id}/node/{ws}/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    html = client.get(f"/board/{board_id}/node/{ws}").text
    assert "清除全部修改" in html
    assert "hx-confirm" in html
