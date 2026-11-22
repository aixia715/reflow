"""BOM 版本页：影响清单、逐板确认、单事务回滚、审计前缀、负责人转让。"""
import json

import pytest

from app.bom_engine import fold_bom
from app.csv_import import CsvEntry
from app.db import connect, init_db
from app.main import get_conn
from app import models
from tests.helpers import auth_client


def _conn(tmp_path):
    conn = connect(tmp_path / "t.sqlite")
    init_db(conn)
    return conn


def _chain(conn, board_id, part="47k"):
    """在工作区改 R1 并提交，使根节点的下游出现一条显式修改。"""
    draft = models.workspace_node(conn, board_id)
    models.set_change(conn, draft["id"], "R1", "modify", part)
    models.commit_workspace(conn, board_id, "改 R1")


def _two_boards(conn):
    """两块板共用一个 BOM 版本，负责人同时是两块板的所有者。每块都有下游冲突。"""
    owner = models.create_user(conn, "owner", "test-password", is_admin=True)
    models.create_bom_version(
        conn, "B", "v1", "bomA",
        [CsvEntry("R1", "10k"), CsvEntry("C1", "100nF")],
        owner_user_id=owner,
    )
    board_a = models.create_board(conn, "B", "v1", "bomA", "BOARD-A", owner_user_id=owner)
    board_b = models.create_board(conn, "B", "v1", "bomA", "BOARD-B", owner_user_id=owner)
    _chain(conn, board_a)
    _chain(conn, board_b)
    version = models.get_bom_version_row(conn, "B", "v1", "bomA")
    return owner, version, board_a, board_b


def _downstream(conn, board_id) -> int:
    root = next(n for n in models.list_nodes(conn, board_id) if n["parent_id"] is None)
    return conn.execute(
        "SELECT id FROM nodes WHERE parent_id=?", (root["id"],)
    ).fetchone()["id"]


def _root(conn, board_id) -> int:
    return next(n["id"] for n in models.list_nodes(conn, board_id) if n["parent_id"] is None)


def _fold(conn, node_id) -> dict:
    initial, chain = models.get_chain(conn, node_id)
    return fold_bom(initial, chain)


def _logs(conn, node_id, source=None):
    sql = "SELECT * FROM edit_log WHERE node_id=?"
    args = [node_id]
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " ORDER BY id"
    return conn.execute(sql, args).fetchall()


def test_preview_lists_both_boards_and_conflicts_without_writing(tmp_path):
    conn = _conn(tmp_path)
    _owner, version, board_a, board_b = _two_boards(conn)
    before = dict(models.get_initial_bom(conn, "B", "v1", "bomA"))
    items = models.preview_bom_version_change(conn, version["id"], "R1", "modify", "22k")
    assert [item["board_uid"] for item in items] == ["BOARD-A", "BOARD-B"]
    by_uid = {item["board_uid"]: item for item in items}
    assert by_uid["BOARD-A"]["conflicts"][0].downstream_node_id == _downstream(conn, board_a)
    assert by_uid["BOARD-B"]["conflicts"][0].corrected_value == "22k"
    assert models.get_initial_bom(conn, "B", "v1", "bomA") == before
    assert models.get_bom_version(conn, version["id"])["revision"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM edit_log").fetchone()["n"] == 0


def test_missing_choice_does_not_change_initial_bom(tmp_path):
    conn = _conn(tmp_path)
    owner, version, board_a, board_b = _two_boards(conn)
    before = dict(models.get_initial_bom(conn, "B", "v1", "bomA"))
    with pytest.raises(models.BomVersionError):
        models.apply_bom_version_change(
            conn, version["id"], owner, 0, "R1", "modify", "22k",
            {str(_downstream(conn, board_a)): "take"},
        )
    assert models.get_initial_bom(conn, "B", "v1", "bomA") == before
    assert _fold(conn, _root(conn, board_b))["R1"] == "10k"
    assert models.get_bom_version(conn, version["id"])["revision"] == 0


def test_confirmed_choices_update_both_boards_and_audit(tmp_path):
    conn = _conn(tmp_path)
    owner, version, board_a, board_b = _two_boards(conn)
    down_a = _downstream(conn, board_a)
    down_b = _downstream(conn, board_b)
    models.apply_bom_version_change(
        conn, version["id"], owner, 0, "R1", "modify", "22k",
        {str(down_a): "take", str(down_b): "keep"},
    )
    assert models.get_initial_bom(conn, "B", "v1", "bomA")["R1"] == "22k"
    assert _fold(conn, _root(conn, board_a))["R1"] == "22k"
    assert _fold(conn, _root(conn, board_b))["R1"] == "22k"
    # 采用修正值：下游显式 op 删掉，折叠跟着初始清单走
    assert _fold(conn, down_a)["R1"] == "22k"
    # 保留下游值：这块板的下游仍是改之前的值
    assert _fold(conn, down_b)["R1"] == "47k"
    updated = models.get_bom_version(conn, version["id"])
    assert updated["revision"] == 1
    assert updated["revised_by"] == owner
    for board_id in (board_a, board_b):
        direct = _logs(conn, _root(conn, board_id), "direct")
        assert len(direct) == 1
        assert direct[0]["note"].startswith("BOM 版本修正")
        assert direct[0]["actor_user_id"] == owner
    propagated = _logs(conn, down_a, "propagated")
    assert len(propagated) == 1
    assert propagated[0]["note"].startswith("BOM 版本修正")
    assert propagated[0]["actor_user_id"] == owner
    assert _logs(conn, down_b, "propagated") == []


def test_second_board_failure_rolls_back_the_first(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    owner, version, board_a, board_b = _two_boards(conn)
    before = dict(models.get_initial_bom(conn, "B", "v1", "bomA"))
    real = models._apply_board_correction
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("第二块写入失败")
        return real(*args, **kwargs)

    monkeypatch.setattr(models, "_apply_board_correction", boom)
    down_a = _downstream(conn, board_a)
    down_b = _downstream(conn, board_b)
    with pytest.raises(RuntimeError):
        models.apply_bom_version_change(
            conn, version["id"], owner, 0, "R1", "modify", "22k",
            {str(down_a): "take", str(down_b): "take"},
        )
    assert models.get_initial_bom(conn, "B", "v1", "bomA") == before
    assert _fold(conn, _root(conn, board_a))["R1"] == "10k"
    assert _fold(conn, _downstream(conn, board_a))["R1"] == "47k"
    assert models.get_bom_version(conn, version["id"])["revision"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM edit_log").fetchone()["n"] == 0


def test_stale_revision_does_not_write(tmp_path):
    conn = _conn(tmp_path)
    owner, version, board_a, board_b = _two_boards(conn)
    down_a = _downstream(conn, board_a)
    down_b = _downstream(conn, board_b)
    choices = {str(down_a): "take", str(down_b): "take"}
    models.apply_bom_version_change(
        conn, version["id"], owner, 0, "R1", "modify", "22k", choices)
    with pytest.raises(models.BomRevisionError) as caught:
        models.apply_bom_version_change(
            conn, version["id"], owner, 0, "R1", "modify", "33k", choices)
    assert str(caught.value) == "已被 owner 修改，请刷新"
    assert models.get_initial_bom(conn, "B", "v1", "bomA")["R1"] == "22k"
    assert models.get_bom_version(conn, version["id"])["revision"] == 1


def test_can_correct_requires_owner_and_editor_on_every_board(tmp_path):
    conn = _conn(tmp_path)
    owner, version, board_a, _board_b = _two_boards(conn)
    other = models.create_user(conn, "next", "test-password", is_admin=False)
    from app.authz import can_correct_bom_version
    assert can_correct_bom_version(conn, owner, version["id"]) is True
    assert can_correct_bom_version(conn, other, version["id"]) is False
    # 先把这块板转让出去，原负责人变成 editor，再降成 viewer。
    models.add_member(conn, board_a, "next", "editor")
    models.transfer_ownership(conn, board_a, owner, other)
    models.set_member_role(conn, board_a, owner, "viewer")
    assert can_correct_bom_version(conn, owner, version["id"]) is False


def test_init_db_again_keeps_existing_rows(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = connect(path)
    init_db(conn)
    owner, version, board_a, _board_b = _two_boards(conn)
    down = _downstream(conn, board_a)
    models.apply_bom_version_change(
        conn, version["id"], owner, 0, "R1", "modify", "22k",
        {str(down): "keep", str(_downstream(conn, _board_b)): "keep"},
    )
    bom = dict(models.get_initial_bom(conn, "B", "v1", "bomA"))
    logs = conn.execute("SELECT note, actor_user_id FROM edit_log ORDER BY id").fetchall()
    rev = models.get_bom_version(conn, version["id"])
    conn.close()

    again = connect(path)
    init_db(again)
    assert dict(models.get_initial_bom(again, "B", "v1", "bomA")) == bom
    assert models.get_bom_version(again, version["id"])["owner_user_id"] == owner
    assert models.get_bom_version(again, version["id"])["revision"] == rev["revision"]
    assert models.get_bom_version(again, version["id"])["revised_by"] == owner
    kept = again.execute("SELECT note, actor_user_id FROM edit_log ORDER BY id").fetchall()
    assert [(r["note"], r["actor_user_id"]) for r in kept] == [
        (r["note"], r["actor_user_id"]) for r in logs
    ]
    assert models.get_board(again, board_a)["board_uid"] == "BOARD-A"


def _route_world(tmp_path, monkeypatch):
    owner = auth_client(tmp_path, monkeypatch, username="owner", password="test-password")
    other = auth_client(tmp_path, monkeypatch, username="next", password="test-password", is_admin=False)
    first = owner.post(
        "/board/new",
        data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA", "board_uid": "BOARD-A"},
        files={"file": ("bom.csv", "Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
        follow_redirects=False,
    )
    assert first.status_code == 303, first.text
    board_a = int(first.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    second = owner.post(
        "/board/new",
        data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA", "board_uid": "BOARD-B"},
        follow_redirects=False,
    )
    assert second.status_code == 303, second.text
    board_b = int(second.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    version = models.get_bom_version_row(get_conn(), "B", "v1", "bomA")
    return owner, other, board_a, board_b, version


def _conflict(client, board_id):
    client.post(f"/board/{board_id}/workspace/edit",
                data={"reference": "R1", "op": "modify", "part": "47k"})
    client.post(f"/board/{board_id}/commit", data={"message": "改 R1"})


def test_page_preview_and_stale_revision_message(tmp_path, monkeypatch):
    owner, _other, board_a, board_b, version = _route_world(tmp_path, monkeypatch)
    _conflict(owner, board_a)
    _conflict(owner, board_b)
    page = owner.get(f"/bom-version/{version['id']}")
    assert page.status_code == 200
    assert "BOARD-A" not in page.text
    assert "BOARD-B" not in page.text
    assert "查看影响" in page.text
    assert "10k" in page.text

    before = dict(models.get_initial_bom(get_conn(), "B", "v1", "bomA"))
    preview = owner.post(
        f"/bom-version/{version['id']}/preview",
        data={"revision": "0", "reference": "R1", "op": "modify", "part": "22k"},
    )
    assert preview.status_code == 200
    assert "将影响以下 2 块板" in preview.text
    assert "BOARD-A" in preview.text and "BOARD-B" in preview.text
    assert "保留下游值" in preview.text and "采用修正值" in preview.text
    assert models.get_initial_bom(get_conn(), "B", "v1", "bomA") == before

    conn = get_conn()
    down_a = _downstream(conn, board_a)
    down_b = _downstream(conn, board_b)
    missing = owner.post(
        f"/bom-version/{version['id']}",
        data={"revision": "0", "reference": "R1", "op": "modify", "part": "22k",
              "choices": json.dumps({str(down_a): "take"})},
    )
    assert missing.status_code == 400
    assert models.get_initial_bom(get_conn(), "B", "v1", "bomA") == before

    applied = owner.post(
        f"/bom-version/{version['id']}",
        data={"revision": "0", "reference": "R1", "op": "modify", "part": "22k",
              "choices": json.dumps({str(down_a): "take", str(down_b): "take"})},
        follow_redirects=False,
    )
    assert applied.status_code == 303
    assert models.get_initial_bom(get_conn(), "B", "v1", "bomA")["R1"] == "22k"
    assert _fold(get_conn(), _downstream(get_conn(), board_a))["R1"] == "22k"
    assert _fold(get_conn(), _downstream(get_conn(), board_b))["R1"] == "22k"

    stale = owner.post(
        f"/bom-version/{version['id']}",
        data={"revision": "0", "reference": "R1", "op": "modify", "part": "33k",
              "choices": "{}"},
        headers={"HX-Request": "true"},
    )
    assert stale.status_code == 200
    assert "已被 owner 修改，请刷新" in stale.text
    assert stale.headers.get("HX-Reswap") == "none"
    assert models.get_initial_bom(get_conn(), "B", "v1", "bomA")["R1"] == "22k"

    root = _root(get_conn(), board_a)
    html = owner.get(f"/board/{board_a}/node/{root}").text
    assert "初始清单在 BOM 版本页维护" in html
    assert f'href="/bom-version/{version["id"]}"' in html
    assert "原始清单在" in html and "被 owner 修正" in html
    denied = owner.post(
        f"/board/{board_a}/node/{root}/edit",
        data={"reference": "R1", "op": "modify", "part": "33k"},
    )
    assert denied.status_code == 403
    assert "根节点是初始 BOM，请到 BOM 版本页修正。" in denied.text
    assert f"/bom-version/{version['id']}" in denied.text


def test_member_sees_initial_list_without_other_boards(tmp_path, monkeypatch):
    owner, other, board_a, _board_b, version = _route_world(tmp_path, monkeypatch)
    owner.post(f"/board/{board_a}/members", data={"username": "next", "role": "editor"})
    page = other.get(f"/bom-version/{version['id']}")
    assert page.status_code == 200
    assert "10k" in page.text
    assert "你还不能修正这份初始 BOM" in page.text
    assert "查看影响" not in page.text
    assert "BOARD-A" not in page.text
    assert "BOARD-B" not in page.text
    missing = owner.get("/bom-version/999999")
    assert missing.status_code == 404


def test_demoted_owner_sees_no_correct_form_and_post_hides_board_uid(tmp_path, monkeypatch):
    owner, other, board_a, board_b, version = _route_world(tmp_path, monkeypatch)
    owner.post(f"/board/{board_b}/members", data={"username": "next", "role": "editor"})
    owner_id = models.get_user_by_username(get_conn(), "owner")["id"]
    other_id = models.get_user_by_username(get_conn(), "next")["id"]
    owner.post(f"/board/{board_b}/transfer", data={"user_id": str(other_id)})
    demoted = other.post(
        f"/board/{board_b}/members/{owner_id}", data={"role": "viewer"})
    assert demoted.status_code == 200, demoted.text

    page = owner.get(f"/bom-version/{version['id']}")
    assert page.status_code == 200
    assert "你还不能修正这份初始 BOM" in page.text
    assert "查看影响" not in page.text
    assert "BOARD-B" not in page.text
    denied = owner.post(
        f"/bom-version/{version['id']}",
        data={"revision": "0", "reference": "R1", "op": "modify", "part": "22k", "choices": "{}"},
    )
    assert denied.status_code == 403
    assert "你还不能修正这份初始 BOM" in denied.text
    assert "BOARD-B" not in denied.text
    assert models.get_initial_bom(get_conn(), "B", "v1", "bomA")["R1"] == "10k"


def test_transfer_owner_then_only_editors_can_correct(tmp_path, monkeypatch):
    owner, other, board_a, board_b, version = _route_world(tmp_path, monkeypatch)
    owner.post(f"/board/{board_a}/members", data={"username": "next", "role": "editor"})
    owner.post(f"/board/{board_b}/members", data={"username": "next", "role": "editor"})
    members_before = [
        (row["board_id"], row["user_id"], row["role"])
        for row in get_conn().execute("SELECT board_id, user_id, role FROM board_members ORDER BY board_id, user_id")
    ]
    moved = owner.post(
        f"/bom-version/{version['id']}/transfer",
        data={"username": "next"},
        follow_redirects=False,
    )
    assert moved.status_code == 303, moved.text
    assert models.get_bom_version(get_conn(), version["id"])["owner_user_id"] == (
        models.get_user_by_username(get_conn(), "next")["id"]
    )
    members_after = [
        (row["board_id"], row["user_id"], row["role"])
        for row in get_conn().execute("SELECT board_id, user_id, role FROM board_members ORDER BY board_id, user_id")
    ]
    assert members_after == members_before
    blocked = owner.post(
        f"/bom-version/{version['id']}",
        data={"revision": "0", "reference": "R1", "op": "modify", "part": "22k", "choices": "{}"},
    )
    assert blocked.status_code == 403
    assert models.get_initial_bom(get_conn(), "B", "v1", "bomA")["R1"] == "10k"

    applied = other.post(
        f"/bom-version/{version['id']}",
        data={"revision": "0", "reference": "C1", "op": "modify", "part": "1000pF", "choices": "{}"},
        follow_redirects=False,
    )
    assert applied.status_code == 303, applied.text
    # 写入路径要走元器件 lint：1000pF 归一成 1nF
    assert models.get_initial_bom(get_conn(), "B", "v1", "bomA")["C1"] == "1nF"
    actor = models.get_user_by_username(get_conn(), "next")["id"]
    note = get_conn().execute(
        "SELECT note, actor_user_id FROM edit_log WHERE source='direct' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert note["note"].startswith("BOM 版本修正")
    assert note["actor_user_id"] == actor


def test_transfer_rejects_disabled_and_self(tmp_path, monkeypatch):
    owner, _other, _board_a, _board_b, version = _route_world(tmp_path, monkeypatch)
    self_transfer = owner.post(
        f"/bom-version/{version['id']}/transfer",
        data={"username": "owner"},
    )
    assert self_transfer.status_code == 200
    assert "不能转让给自己" in self_transfer.text
    owner_id = models.get_user_by_username(get_conn(), "owner")["id"]
    other_id = models.get_user_by_username(get_conn(), "next")["id"]
    models.disable_user(get_conn(), owner_id, other_id)
    denied = owner.post(
        f"/bom-version/{version['id']}/transfer",
        data={"username": "next"},
    )
    assert denied.status_code == 200
    assert "已停用" in denied.text
    assert models.get_bom_version(get_conn(), version["id"])["owner_user_id"] == owner_id
