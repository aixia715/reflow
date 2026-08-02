import json
import pytest
from fastapi.testclient import TestClient
from app import models
from app.bom_engine import resolve_reference


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _resolved(conn, node_id, ref):
    initial, chain = models.get_chain(conn, node_id)
    return resolve_reference(initial, chain, ref)


def _setup_chain(client, uid="3"):
    """建 root -> c1 -> c2 -> 草稿，c1/c2 的 committed_at 间隔开。返回 (board_id, root, c1, c2)。"""
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": uid},
                    files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
                    follow_redirects=False)
    board_id = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "100nF"})
    client.post(f"/board/{board_id}/commit", data={"message": "c1"})
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "22k"})
    client.post(f"/board/{board_id}/commit", data={"message": "c2"})
    from app.main import get_conn
    conn = get_conn()
    nodes = models.list_nodes(conn, board_id)
    root = nodes[0]["id"]
    committed = [n for n in nodes if n["is_committed"] and n["parent_id"] is not None]
    c1, c2 = committed[0]["id"], committed[1]["id"]
    conn.execute("UPDATE nodes SET committed_at=? WHERE id=?",
                 ("2026-06-01T00:00:00+00:00", c1))
    conn.execute("UPDATE nodes SET committed_at=? WHERE id=?",
                 ("2026-06-10T00:00:00+00:00", c2))
    conn.commit()
    return board_id, root, c1, c2


def test_insert_page_loads_for_middle_node(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.get(f"/board/{board_id}/node/{c1}/insert")
    assert r.status_code == 200
    assert "插入" in r.text


def test_insert_rejected_at_last_committed(client):
    board_id, root, c1, c2 = _setup_chain(client)
    # c2 是最后一个已提交节点，子节点是草稿 → 不可插入
    r = client.get(f"/board/{board_id}/node/{c2}/insert", follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    loc = unquote(r.headers["location"])
    assert "工作区" in loc or "不可插入" in loc


def test_insert_creates_node_and_relinks(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "ins",
                          "changes": json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])},
                    follow_redirects=False)
    assert r.status_code == 303
    from app.main import get_conn
    conn = get_conn()
    new = conn.execute("SELECT * FROM nodes WHERE parent_id=?", (c1,)).fetchone()
    assert new["id"] != c2
    assert new["committed_at"] == "2026-06-05T00:00:00+00:00"
    assert conn.execute("SELECT parent_id FROM nodes WHERE id=?",
                        (c2,)).fetchone()["parent_id"] == new["id"]
    assert _resolved(conn, c2, "D1") == "1uF"


def test_insert_lints_part_value_server_side(client):
    # changes 是客户端提交的 JSON，服务端必须自己再 lint 一遍，不能只信前端传回来的值。
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "ins",
                          "changes": json.dumps([{"reference": "C2", "op": "add", "part": "1000pF"}])},
                    follow_redirects=False)
    assert r.status_code == 303
    from app.main import get_conn
    conn = get_conn()
    assert _resolved(conn, c2, "C2") == "1nF"


def test_insert_success_flash_keeps_node_number(client):
    from urllib.parse import urlparse, parse_qs, unquote
    board_id, root, c1, c2 = _setup_chain(client)
    changes = json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])
    # 非 HX：Location 头里的 flash 应保留节点号（# 不能被当作 fragment 截断）
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00",
                          "message": "ins", "changes": changes},
                    follow_redirects=False)
    flash = unquote(parse_qs(urlparse(r.headers["location"]).query)["flash"][0])
    assert "已插入节点" in flash and "#" in flash
    # HX：HX-Redirect 头同样保留节点号
    board_id, root, c1, c2 = _setup_chain(client, uid="4")
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00",
                          "message": "ins", "changes": changes},
                    headers={"HX-Request": "true"})
    flash = unquote(parse_qs(urlparse(r.headers["HX-Redirect"]).query)["flash"][0])
    assert "已插入节点" in flash and "#" in flash


def test_insert_empty_changes_rejected(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "",
                          "changes": json.dumps([])})
    assert r.status_code == 200
    assert "至少" in r.text
    from app.main import get_conn
    conn = get_conn()
    assert conn.execute("SELECT parent_id FROM nodes WHERE id=?",
                        (c2,)).fetchone()["parent_id"] == c1


def _node_count(board_id):
    from app.main import get_conn
    return len(models.list_nodes(get_conn(), board_id))


@pytest.mark.parametrize("bad_changes", [
    '["R1"]',                                        # 数组元素不是对象
    '[123]',                                         # 数组元素是数字
    '42',                                            # 顶层是标量
    '{"a":1}',                                       # 顶层是对象（遍历拿到 key 字符串）
    '[{"reference":123,"op":"add","part":"1k"}]',    # reference 不是字符串
    '[{"reference":"R9","op":"add","part":123}]',    # part 不是字符串（validate_edit 会 .strip()）
    '[{"reference":"R9","op":123,"part":"1k"}]',     # op 不是字符串
])
def test_insert_rejects_malformed_changes_shape(client, bad_changes):
    """形状畸形的 changes（合法 JSON 但不是 list[dict{reference: str}]）应被优雅拒绝，而不是 500。"""
    board_id, root, c1, c2 = _setup_chain(client)
    before = _node_count(board_id)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "m",
                          "changes": bad_changes})
    assert r.status_code == 200
    assert r.headers.get("HX-Retarget") == "#form-error"
    assert "格式不正确" in r.text
    assert _node_count(board_id) == before      # 没有建出半个节点


def test_insert_rejects_deeply_nested_json(client):
    """深嵌套 JSON 触发 RecursionError（RuntimeError 子类，不被 ValueError/TypeError 捕获）。"""
    board_id, root, c1, c2 = _setup_chain(client)
    before = _node_count(board_id)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "m",
                          "changes": '[' * 10000 + ']' * 10000})
    assert r.status_code == 200
    assert "格式不正确" in r.text
    assert _node_count(board_id) == before


def test_insert_rejects_duplicate_reference_in_payload(client):
    """同一位号在 payload 里出现两次应被拒绝。

    前端 pending 以位号为 key、pendingList() 由 Object.keys 生成，位号天然唯一；
    手搓的 payload 绕过这个契约时，apply_node_edit 会写两次，set_change 的 upsert
    让后者覆盖前者，审计日志却多出一条从未生效的记录。
    """
    board_id, root, c1, c2 = _setup_chain(client)
    before = _node_count(board_id)
    payload = json.dumps([{"reference": "R9", "op": "add", "part": "1uF"},
                          {"reference": " R9 ", "op": "add", "part": "2uF"}])
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "m",
                          "changes": payload})
    assert r.status_code == 200
    assert "位号重复" in r.text
    assert _node_count(board_id) == before


def test_insert_time_out_of_range_rejected(client):
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-07-01T00:00:00+00:00", "message": "",
                          "changes": json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])})
    assert r.status_code == 200
    assert "下一个" in r.text
    from app.main import get_conn
    conn = get_conn()
    assert conn.execute("SELECT parent_id FROM nodes WHERE id=?",
                        (c2,)).fetchone()["parent_id"] == c1


def test_insert_conflict_when_downstream_explicit(client):
    board_id, root, c1, c2 = _setup_chain(client)
    # c2 显式改了 R1=22k；在 c1 后插入也改 R1=47k → 与下游 c2 冲突
    r = client.post(f"/board/{board_id}/node/{c1}/insert",
                    data={"committed_at": "2026-06-05T00:00:00+00:00", "message": "ins",
                          "changes": json.dumps([{"reference": "R1", "op": "modify", "part": "47k"}])})
    assert r.status_code == 200
    assert "确认" in r.text
    from app.main import get_conn
    conn = get_conn()
    new = conn.execute("SELECT * FROM nodes WHERE parent_id=?", (c1,)).fetchone()
    assert new["id"] != c2


# ---- /insert/check：插入页「添加这条」的服务端校验 + lint 归一（校验单一来源） ----
#
# 插入页曾在 JS 里手抄一份 validate_edit（含错误文案）和一份 op 推断，两份规则
# 各改各的必然漂移；且客户端完全没有 lint，服务端在保存时静默归一，用户看不到
# 自己敲的 3.9Nf 被改成了 3.9nF。本端点把这三件事收回服务端：客户端只负责显示。


def _check(client, board_id, parent_id, reference, op, part="", changes="[]"):
    r = client.post(f"/board/{board_id}/node/{parent_id}/insert/check",
                    data={"reference": reference, "op": op,
                          "part": part, "changes": changes})
    assert r.status_code == 204
    return json.loads(r.headers["HX-Trigger"])["insertChecked"]


def test_check_rejects_add_of_existing_reference(client):
    """错误文案与 validate_edit 同源，不再由 JS 复述。"""
    board_id, root, c1, c2 = _setup_chain(client)
    got = _check(client, board_id, c1, "R1", "add", "1k")
    assert got["ok"] is False
    assert "已存在" in got["error"]


def test_check_rejects_modify_of_unknown_reference(client):
    board_id, root, c1, c2 = _setup_chain(client)
    got = _check(client, board_id, c1, "R404", "modify", "1k")
    assert got["ok"] is False
    assert "不存在" in got["error"]


def test_check_normalizes_part_and_reports_fix(client):
    """lint 归一发生在服务端，并把一次性的 fix 文案带回给界面显示。"""
    board_id, root, c1, c2 = _setup_chain(client)
    got = _check(client, board_id, c1, "C5", "add", "3.9Nf")
    assert got["ok"] is True
    assert got["part"] == "3.9nF"
    assert got["fix"]


def test_check_folds_pending_changes_into_the_baseline(client):
    """基准 BOM = 父节点折叠结果 + 本页暂存的改动，后一条能依赖前一条。"""
    board_id, root, c1, c2 = _setup_chain(client)
    pending = json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])
    got = _check(client, board_id, c1, "D1", "modify", "2uF", changes=pending)
    assert got["ok"] is True


def test_check_returns_parent_relative_op(client):
    """暂存里已 add 过的位号再改，落库 op 仍须是相对父节点的 add（op 推断收归服务端）。"""
    board_id, root, c1, c2 = _setup_chain(client)
    pending = json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])
    got = _check(client, board_id, c1, "D1", "modify", "2uF", changes=pending)
    assert got["action"] == "set"
    assert got["op"] == "add"


def test_check_drops_entry_when_value_returns_to_upstream(client):
    """改回父节点原值 = 无操作，暂存里这条应当消失，而不是留一条空转的修改。"""
    board_id, root, c1, c2 = _setup_chain(client)
    pending = json.dumps([{"reference": "R1", "op": "modify", "part": "47k"}])
    got = _check(client, board_id, c1, "R1", "modify", "10k", changes=pending)
    assert got["ok"] is True
    assert got["action"] == "drop"


def test_check_drops_entry_when_removing_a_pending_add(client):
    board_id, root, c1, c2 = _setup_chain(client)
    pending = json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}])
    got = _check(client, board_id, c1, "D1", "remove", changes=pending)
    assert got["ok"] is True
    assert got["action"] == "drop"


def test_check_marks_remove_of_upstream_reference_as_set(client):
    board_id, root, c1, c2 = _setup_chain(client)
    got = _check(client, board_id, c1, "C9", "remove")
    assert got["action"] == "set"
    assert got["op"] == "remove"
    assert got["part"] is None


def test_check_rejects_malformed_pending_payload(client):
    board_id, root, c1, c2 = _setup_chain(client)
    got = _check(client, board_id, c1, "D1", "add", "1uF", changes="{oops")
    assert got["ok"] is False
    assert "格式" in got["error"]


def test_check_refuses_uninsertable_position(client):
    """链末不可插入，校验端点也要挡住，别让界面以为可以攒改动。"""
    board_id, root, c1, c2 = _setup_chain(client)
    got = _check(client, board_id, c2, "D1", "add", "1uF")
    assert got["ok"] is False


def test_insert_page_bounds_exclude_the_endpoints(client):
    """日期控件的 min/max 由 insert_time_bounds 算好后下发，不再在 JS 里截断秒。"""
    board_id, root, c1, c2 = _setup_chain(client)
    r = client.get(f"/board/{board_id}/node/{c1}/insert")
    assert "2026-06-01T00:01:00+00:00" in r.text   # 上一节点 00:00:00 → 下界进位一分钟
    assert "2026-06-09T23:59:00+00:00" in r.text   # 下一节点 00:00:00 → 上界退一分钟
