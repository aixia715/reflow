"""issue #108：工作区从 CSV 导入修改项 —— 路由层。"""
import json
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import models
from app.main import get_conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_board(client, uid="SN1"):
    """建一块单板，初始 BOM 为 R1=10k、C1=100nF。返回 board_id。"""
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": uid},
                    files={"file": ("bom.csv",
                                    b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
                    follow_redirects=False)
    return int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _workspace_id(board_id):
    return models.workspace_node(get_conn(), board_id)["id"]


def _changeset(node_id):
    """草稿 changeset：{reference: (op, part)}。"""
    return {c["reference"]: (c["op"], c["part"])
            for c in models.get_changeset(get_conn(), node_id)}


def _preview(client, board_id, node_id, csv_bytes):
    return client.post(f"/board/{board_id}/node/{node_id}/import/preview",
                       files={"file": ("changes.csv", csv_bytes, "text/csv")})


def _changes_json(html):
    """从预览片段的 hx-vals 里抠出 changes JSON 字符串。"""
    import re
    m = re.search(r"hx-vals='(.*?)'", html)
    assert m, f"预览片段里没有 hx-vals：{html}"
    return json.loads(m.group(1))["changes"]


# ── 预览 ────────────────────────────────────────────────────────

def test_preview_shows_counts_and_does_not_write_db(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws,
                 b"Reference,PART,OP\nR1,47k,\nR9,1uF,add\nC1,,remove\n")
    assert r.status_code == 200
    assert "新增 1" in r.text and "修改 1" in r.text and "不贴 1" in r.text
    assert _changeset(ws) == {}  # 预览不写库


def test_preview_lists_problems_and_omits_apply_button(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR1,22k,add\n")
    assert r.status_code == 200
    assert "已存在" in r.text
    assert "hx-vals" not in r.text  # 有问题行 → 不给应用按钮


def test_preview_rejects_csv_without_required_columns(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Value\nR1,10k\n")
    assert r.status_code == 200
    assert "必须包含 Reference 和 Part 两列" in r.text


def test_preview_rejects_non_utf8_file(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    # "电" 的 GBK 编码字节 \xb5\xe7 恰好不是合法的 UTF-8 序列，可用来触发解码失败；
    # brief 原文用「十」，其 GBK 字节 \xca\xae 巧合能被解码成合法 UTF-8（U+02AE），测不出问题，故换字。
    r = _preview(client, board_id, ws, "Reference,Part\nR1,电k\n".encode("gbk"))
    assert r.status_code == 200
    assert "UTF-8" in r.text


def test_preview_rejected_on_committed_node(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"},
                follow_redirects=False)
    committed = [n for n in models.list_nodes(get_conn(), board_id)
                 if n["is_committed"] and n["parent_id"] is not None][-1]["id"]
    r = _preview(client, board_id, committed, b"Reference,Part\nR1,22k\n")
    assert r.status_code == 400
    assert "只有工作区草稿" in r.text


# ── 应用 ────────────────────────────────────────────────────────

def test_apply_writes_all_changes(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws,
                 b"Reference,PART,OP\nR1,47k,\nR9,1uF,add\nC1,,remove\n")
    r2 = client.post(f"/board/{board_id}/node/{ws}/import",
                     data={"changes": _changes_json(r.text)})
    assert r2.status_code == 204
    # flash 在响应头里是 URL 编码的（响应头须 latin-1）
    assert "已导入 3 条修改" in unquote(r2.headers["HX-Redirect"])
    assert _changeset(ws) == {"R1": ("modify", "47k"),
                              "R9": ("add", "1uF"),
                              "C1": ("remove", None)}


def test_apply_overwrites_existing_draft_change(client):
    """撞车：草稿已把 R1 改成 10k 之外的值，CSV 覆盖之；草稿独有的 C1 保留。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "1k"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C1", "op": "modify", "part": "10nF"})
    r = _preview(client, board_id, ws, b"Reference,Part\nR1,47k\n")
    client.post(f"/board/{board_id}/node/{ws}/import",
                data={"changes": _changes_json(r.text)})
    assert _changeset(ws) == {"R1": ("modify", "47k"), "C1": ("modify", "10nF")}


def test_apply_revalidates_and_writes_nothing_when_stale(client):
    """预览之后草稿变了（R9 已被手工加上），此时再应用 add R9 必须整体拒绝。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part,OP\nR2,1k,add\nR9,1uF,add\n")
    payload = _changes_json(r.text)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R9", "op": "add", "part": "2uF"})
    r2 = client.post(f"/board/{board_id}/node/{ws}/import", data={"changes": payload})
    assert r2.status_code == 200
    assert "已存在" in r2.text
    assert r2.headers["HX-Retarget"] == "#import-error"
    # 整体拒绝：R2 没有被写进去，R9 保持手工填的值
    assert _changeset(ws) == {"R9": ("add", "2uF")}


def test_apply_rejects_empty_payload(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/import", data={"changes": "[]"})
    assert r.status_code == 200
    assert "没有可导入的修改" in r.text


def test_apply_rejected_on_committed_node(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"},
                follow_redirects=False)
    committed = [n for n in models.list_nodes(get_conn(), board_id)
                 if n["is_committed"] and n["parent_id"] is not None][-1]["id"]
    payload = json.dumps([{"reference": "C1", "op": "modify", "part": "1nF"}])
    r = client.post(f"/board/{board_id}/node/{committed}/import",
                    data={"changes": payload})
    assert r.status_code == 400
    assert "只有工作区草稿" in r.text


@pytest.mark.parametrize("bad_changes", [
    '{"reference":"R1"}',   # JSON 对象而非数组
    "42",                   # JSON 标量
    '["R1"]',                # 数组但元素是字符串
    '[{"reference": 123, "op":"add", "part":"1k"}]',  # reference 是数字
    '[{"reference":"R1","op":"add","part":123}]',      # part 是数字
    '[{"reference":"R1","op":"add","part":["1k"]}]',   # part 是数组
    '[{"reference":"R1","op":"add","part":true}]',     # part 是布尔
    '[{"reference":"R1","op":"add","part":{"x":1}}]',  # part 是对象
    '[{"reference":"R1","op":"add","part":1.5}]',       # part 是浮点数
    '[{"reference":"R1","op":123,"part":"1k"}]',        # op 是数字
])
def test_apply_rejects_malformed_changes_shape(client, bad_changes):
    """形状畸形的 changes（合法 JSON 但不是 list[dict{reference: str}]）应被优雅拒绝，而不是 500。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.post(f"/board/{board_id}/node/{ws}/import",
                    data={"changes": bad_changes})
    assert r.status_code == 200
    assert r.headers.get("HX-Retarget") == "#import-error"
    # 拒绝原因是中文提示（措辞与既有的「导入被拒绝：…」风格一致），且没有写库
    assert "格式不正确" in r.text
    assert _changeset(ws) == {}


def test_apply_rejects_deeply_nested_json(client):
    """深嵌套 JSON 导致 RecursionError 应被优雅拒绝，而不是 500。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    # 10000 层嵌套数组会触发 RecursionError
    deeply_nested = '[' * 10000 + ']' * 10000
    r = client.post(f"/board/{board_id}/node/{ws}/import",
                    data={"changes": deeply_nested})
    assert r.status_code == 200
    assert r.headers.get("HX-Retarget") == "#import-error"
    # 归因为格式问题，而不是「没有可导入的修改」——后者是把解析失败降级成空数组的副作用
    assert "格式不正确" in r.text
    assert _changeset(ws) == {}


def test_apply_rejects_duplicate_reference_in_payload(client):
    """payload 里同一位号出现两次应被拒绝，而不是写两次、审计留下幻影编辑。

    parse_change_csv 保证 CSV 内位号唯一，plan_changes 因此对静态 BOM 逐条独立校验；
    手搓的 payload 绕过了这个不变量，两条 add 都会通过校验，set_change 的 upsert
    让后者覆盖前者，但审计日志会多出一条从未生效的记录。
    """
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    payload = json.dumps([{"reference": "R9", "op": "add", "part": "1uF"},
                          {"reference": " R9 ", "op": "add", "part": "2uF"}])
    r = client.post(f"/board/{board_id}/node/{ws}/import", data={"changes": payload})
    assert r.status_code == 200
    assert r.headers.get("HX-Retarget") == "#import-error"
    assert "位号重复" in r.text
    assert _changeset(ws) == {}
    rows = get_conn().execute(
        "SELECT reference FROM edit_log WHERE node_id=?", (ws,)).fetchall()
    assert rows == []


def test_apply_error_message_does_not_blame_stale_draft(client):
    """重校验失败的文案不应一律归因「草稿已变化」——非法 op 并非草稿变化所致。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    payload = json.dumps([{"reference": "R1", "op": "hack", "part": "1k"}])
    r = client.post(f"/board/{board_id}/node/{ws}/import", data={"changes": payload})
    assert r.status_code == 200
    assert "草稿已变化" not in r.text
    # 仍保留 validate_edit 给出的具体原因
    assert "未知操作类型" in r.text
    assert _changeset(ws) == {}


def test_apply_records_audit_log_per_change(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = _preview(client, board_id, ws, b"Reference,Part\nR1,47k\nR9,1uF\n")
    client.post(f"/board/{board_id}/node/{ws}/import",
                data={"changes": _changes_json(r.text)})
    # 审计表实际表名是 edit_log（非 audit_log），列名 node_id/reference/source 与 brief 一致，
    # 已按 app/db.py 的实际 schema 核对并修正表名。
    rows = get_conn().execute(
        "SELECT reference, source FROM edit_log WHERE node_id=?", (ws,)).fetchall()
    assert sorted(r["reference"] for r in rows) == ["R1", "R9"]
    assert {r["source"] for r in rows} == {"direct"}


# ── 下载模板 ────────────────────────────────────────────────────

def test_download_change_csv_template(client):
    """issue #112：GET 模板端点返回仅含三列表头的 CSV 文件。"""
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    r = client.get(f"/board/{board_id}/node/{ws}/import/template")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text == "Reference,Part,OP\n"


def test_draft_page_shows_template_download_link(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    html = client.get(f"/board/{board_id}/node/{ws}").text
    assert f'href="/board/{board_id}/node/{ws}/import/template"' in html
    assert "下载模板" in html


# ── 页面入口 ────────────────────────────────────────────────────

def test_draft_page_shows_import_panel(client):
    board_id = _setup_board(client)
    ws = _workspace_id(board_id)
    html = client.get(f"/board/{board_id}/node/{ws}").text
    assert "从 CSV 导入修改" in html
    assert f'hx-post="/board/{board_id}/node/{ws}/import/preview"' in html
    assert 'id="import-preview"' in html


def test_committed_page_has_no_import_panel(client):
    board_id = _setup_board(client)
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"},
                follow_redirects=False)
    committed = [n for n in models.list_nodes(get_conn(), board_id)
                 if n["is_committed"] and n["parent_id"] is not None][-1]["id"]
    html = client.get(f"/board/{board_id}/node/{committed}").text
    assert "从 CSV 导入修改" not in html
