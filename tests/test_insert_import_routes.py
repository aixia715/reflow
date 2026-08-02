"""插入节点页的 CSV 导入（PR B）：服务端只算不写库，结果交回浏览器合进暂存。

与节点页导入的两点不同：

· **基准是「父节点折叠 BOM + 本页已暂存的改动」**——预览要按用户眼前看到的表算，
  否则「先手工加一条、再导入」会算错。
· **回给前端的 op 一律相对父节点**，与 `/insert/check` 同一口径（暂存里 add 过
  的位号再改，落库仍是一条 add）；值回到父节点原样的标成 drop。
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import models


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("REFLOW_DB", str(tmp_path / "t.sqlite"))
    from app.main import create_app
    return TestClient(create_app())


def _setup_chain(client, csv="Reference,Part\nR1,10k\nC1,100nF\n"):
    """建 root -> c1 -> c2 -> 草稿；返回 (board_id, c1, c2)。c1 之后可插入。"""
    r = client.post("/board/new",
                    data={"board_name": "B", "pcb_version": "v1",
                          "bom_version": "bomA", "board_uid": "IM1"},
                    files={"file": ("bom.csv", csv, "text/csv")},
                    follow_redirects=False)
    bid = int(r.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "C9", "op": "add", "part": "1uF"})
    client.post(f"/board/{bid}/commit", data={"message": "c1"})
    client.post(f"/board/{bid}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "22k"})
    client.post(f"/board/{bid}/commit", data={"message": "c2"})
    from app.main import get_conn
    conn = get_conn()
    committed = [n for n in models.list_nodes(conn, bid)
                 if n["is_committed"] and n["parent_id"] is not None]
    c1, c2 = committed[0]["id"], committed[1]["id"]
    for nid, ts in ((c1, "2026-06-01T00:00:00+00:00"),
                    (c2, "2026-06-10T00:00:00+00:00")):
        conn.execute("UPDATE nodes SET committed_at=? WHERE id=?", (ts, nid))
    conn.commit()
    return bid, c1, c2


def _preview(client, bid, pid, csv, mode="diff", changes="[]"):
    return client.post(
        f"/board/{bid}/node/{pid}/insert/import/preview",
        data={"mode": mode, "changes": changes},
        files={"file": ("x.csv", csv, "text/csv")})


def _staged(resp):
    """从预览响应里取出要合进暂存的载荷。"""
    import re
    m = re.search(r"data-staged='([^']*)'", resp.text)
    assert m, "预览里没有 data-staged"
    return json.loads(m.group(1).replace("&#34;", '"').replace("&amp;", "&"))


# ------------------------------------------------------------------ 基本换算

def test_diff_import_plans_modify_against_parent(client):
    bid, c1, c2 = _setup_chain(client)
    r = _preview(client, bid, c1, "Reference,Part\nR1,47k\n")
    assert r.status_code == 200
    assert _staged(r) == [{"reference": "R1", "op": "modify",
                           "part": "47k", "action": "set"}]


def test_new_reference_is_add(client):
    bid, c1, c2 = _setup_chain(client)
    r = _preview(client, bid, c1, "Reference,Part\nD9,1uF\n")
    assert _staged(r) == [{"reference": "D9", "op": "add",
                           "part": "1uF", "action": "set"}]


def test_value_equal_to_parent_is_dropped(client):
    """CSV 写回父节点原样 → 这条等于没改，标成 drop 让前端把它从暂存里删掉。"""
    bid, c1, c2 = _setup_chain(client)
    r = _preview(client, bid, c1, "Reference,Part\nR1,10k\n",
                 changes=json.dumps([{"reference": "R1", "op": "modify",
                                      "part": "47k"}]))
    assert _staged(r) == [{"reference": "R1", "op": "modify",
                           "part": "10k", "action": "drop"}]


# -------------------------------------------------- 基准要含本页已暂存的改动

def test_preview_uses_pending_changes_as_base(client):
    """先手工 add D9，再导入把 D9 改成别的值：相对屏幕是 modify，
    但落库仍是相对父节点的 add。"""
    bid, c1, c2 = _setup_chain(client)
    pending = json.dumps([{"reference": "D9", "op": "add", "part": "1uF"}])
    r = _preview(client, bid, c1, "Reference,Part\nD9,2uF\n", changes=pending)
    assert _staged(r) == [{"reference": "D9", "op": "add",
                           "part": "2uF", "action": "set"}]


def test_add_of_reference_already_pending_is_not_a_problem(client):
    """暂存里已 add 过的位号，CSV 不写 OP 时应推断成 modify 而不是报「已存在」。"""
    bid, c1, c2 = _setup_chain(client)
    pending = json.dumps([{"reference": "D9", "op": "add", "part": "1uF"}])
    r = _preview(client, bid, c1, "Reference,Part\nD9,2uF\n", changes=pending)
    assert "已存在" not in r.text


# ------------------------------------------------------------------ 全量模式

def test_full_mode_diffs_against_pending_view(client):
    """全量模式：CSV 是插入后的完整目标 BOM，缺席的位号算不贴。"""
    bid, c1, c2 = _setup_chain(client)
    # 父节点（c1）折叠后是 R1=10k, C1=100nF, C9=1uF；目标里去掉 C1
    r = _preview(client, bid, c1, "Reference,Part\nR1,10k\nC9,1uF\n", mode="full")
    assert _staged(r) == [{"reference": "C1", "op": "remove",
                           "part": None, "action": "set"}]


# ------------------------------------------------------------------ 守卫

def test_rejected_when_position_not_insertable(client):
    bid, c1, c2 = _setup_chain(client)
    # c2 是最后一个已提交节点，子节点是草稿 → 不可插入
    r = _preview(client, bid, c2, "Reference,Part\nR1,47k\n")
    assert "不可插入" in r.text


def test_problem_rows_block_apply(client):
    bid, c1, c2 = _setup_chain(client)
    r = _preview(client, bid, c1, "Reference,Part\nR1,\n")   # 空 Part
    assert "data-staged" not in r.text          # 有问题就不给应用入口
    assert "问题" in r.text


def test_malformed_pending_payload_is_rejected(client):
    bid, c1, c2 = _setup_chain(client)
    r = _preview(client, bid, c1, "Reference,Part\nR1,47k\n", changes="{oops")
    assert "数据格式不正确" in r.text


def test_missing_file_reports_error(client):
    bid, c1, c2 = _setup_chain(client)
    r = client.post(f"/board/{bid}/node/{c1}/insert/import/preview",
                    data={"mode": "diff", "changes": "[]"})
    assert "请选择 CSV 文件" in r.text


def test_import_does_not_touch_the_database(client):
    """只算不写：预览完链上不该多出节点，父节点 changeset 也不该变。"""
    bid, c1, c2 = _setup_chain(client)
    from app.main import get_conn
    conn = get_conn()
    before_nodes = len(models.list_nodes(conn, bid))
    before_cs = len(models.get_changeset(conn, c1))

    _preview(client, bid, c1, "Reference,Part\nR1,47k\nD9,1uF\n")

    assert len(models.list_nodes(conn, bid)) == before_nodes
    assert len(models.get_changeset(conn, c1)) == before_cs


def test_values_are_linted_before_staging(client):
    """写进暂存的是归一后的值——与「添加这条」同样的契约。"""
    bid, c1, c2 = _setup_chain(client)
    r = _preview(client, bid, c1, "Reference,Part\nC1,0.1uF\n")
    staged = _staged(r)
    assert staged[0]["part"] == "100nF"
    # 0.1uF 归一成 100nF，恰好等于父节点原值 → 这条其实没改
    assert staged[0]["action"] == "drop"
