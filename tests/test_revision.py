"""节点乐观锁：版本不符不覆盖，冲突确认和插入在同一事务里核对。"""
import json
from urllib.parse import unquote

from app import models
from app.db import connect, init_db
from app.main import get_conn


def _board(client, uid="1"):
    response = client.post(
        "/board/new",
        data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA", "board_uid": uid},
        files={"file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return int(response.headers["location"].split("?")[0].rsplit("/", 1)[-1])


def _workspace(board_id):
    return models.workspace_node(get_conn(), board_id)


def test_migrate_nodes_revision_is_idempotent(tmp_path):
    """老库补列后旧行还在；再启动一次不把已经涨过的版本号打回 0。"""
    db = tmp_path / "old.sqlite"
    conn = connect(str(db))
    conn.execute(
        "CREATE TABLE nodes ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " board_id INTEGER NOT NULL, parent_id INTEGER,"
        " message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,"
        " is_committed INTEGER NOT NULL DEFAULT 0, committed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO nodes(board_id, message, created_at, is_committed) VALUES(1,'旧节点','t',1)"
    )
    conn.commit()
    init_db(conn)
    row = conn.execute(
        "SELECT message, revision, revised_by FROM nodes WHERE message='旧节点'"
    ).fetchone()
    assert row["message"] == "旧节点"
    assert row["revision"] == 0
    assert row["revised_by"] is None
    conn.execute(
        "UPDATE nodes SET revision=4, revised_by=NULL WHERE message='旧节点'"
    )
    conn.commit()
    init_db(conn)
    again = conn.execute(
        "SELECT message, revision FROM nodes WHERE message='旧节点'"
    ).fetchone()
    assert again["message"] == "旧节点"
    assert again["revision"] == 4


def test_stale_draft_edit_keeps_first_value(tmp_path, monkeypatch):
    """后一个用户带着打开页面时的 revision，changeset 仍是前一个写入的值。"""
    from tests.helpers import auth_client
    first = auth_client(tmp_path, monkeypatch, username="ada")
    second = auth_client(tmp_path, monkeypatch, username="bao", is_admin=False)
    bid = _board(first)
    first.post(f"/board/{bid}/members", data={"username": "bao", "role": "editor"})
    ws = _workspace(bid)
    opened = int(ws["revision"])
    saved = first.post(
        f"/board/{bid}/node/{ws['id']}/edit",
        data={"reference": "R1", "op": "modify", "part": "47k", "revision": str(opened)},
    )
    assert saved.status_code == 200, saved.text
    assert models.get_change(get_conn(), ws["id"], "R1")["part"] == "47k"

    stale = second.post(
        f"/board/{bid}/node/{ws['id']}/edit",
        data={"reference": "R1", "op": "modify", "part": "22k", "revision": str(opened)},
    )
    assert stale.status_code == 200, stale.text
    assert stale.headers.get("HX-Reswap") == "none"
    assert "已被 ada 修改，请刷新" in stale.text
    assert models.get_change(get_conn(), ws["id"], "R1")["part"] == "47k"
    assert int(models.get_node(get_conn(), ws["id"])["revision"]) == opened + 1


def test_conflict_confirm_rolls_back_when_downstream_revision_is_stale(tmp_path, monkeypatch):
    """下游版本过期时，被编辑节点的 changeset 也不变。"""
    from tests.helpers import auth_client
    owner = auth_client(tmp_path, monkeypatch, username="ada")
    other = auth_client(tmp_path, monkeypatch, username="bao", is_admin=False)
    bid = _board(owner)
    owner.post(f"/board/{bid}/members", data={"username": "bao", "role": "editor"})
    owner.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R1", "op": "modify", "part": "47k"},
        follow_redirects=False,
    )
    owner.post(f"/board/{bid}/commit", data={"message": "改成 47k"}, follow_redirects=False)
    owner.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R1", "op": "modify", "part": "22k"},
        follow_redirects=False,
    )
    owner.post(f"/board/{bid}/commit", data={"message": "改成 22k"}, follow_redirects=False)
    nodes = [n for n in models.list_nodes(get_conn(), bid)
             if n["is_committed"] and n["parent_id"] is not None]
    first, second = nodes[0], nodes[1]
    opened = int(first["revision"])
    modal = owner.post(
        f"/board/{bid}/node/{first['id']}/edit",
        data={"reference": "R1", "op": "modify", "part": "5k", "revision": str(opened)},
    )
    assert "采用修正值" in modal.text
    assert models.get_change(get_conn(), first["id"], "R1")["part"] == "47k"

    conn = get_conn()
    bao = models.get_user_by_username(conn, "bao")
    conn.execute(
        "UPDATE nodes SET revision = revision + 1, revised_by = ? WHERE id = ?",
        (bao["id"], second["id"]),
    )
    conn.commit()
    seen = int(second["revision"])
    resolved = owner.post(
        f"/board/{bid}/node/{first['id']}/resolve",
        data={
            "edit_reference": "R1", "op": "modify", "part": "5k",
            "revision": str(opened),
            "downstream_node_id": str(second["id"]),
            "downstream_revision": str(seen),
            "reference": "R1", "choice": "take",
        },
        follow_redirects=False,
    )
    assert resolved.status_code == 303, resolved.text
    assert "已被 bao 修改，请刷新" in unquote(resolved.headers["location"])
    assert models.get_change(get_conn(), first["id"], "R1")["part"] == "47k"
    assert models.get_change(get_conn(), second["id"], "R1")["part"] == "22k"


def test_stale_parent_revision_does_not_insert(tmp_path, monkeypatch):
    from tests.helpers import auth_client
    owner = auth_client(tmp_path, monkeypatch, username="ada")
    bid = _board(owner, uid="ins")
    owner.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "C9", "op": "add", "part": "100nF"},
        follow_redirects=False,
    )
    owner.post(f"/board/{bid}/commit", data={"message": "c1"}, follow_redirects=False)
    owner.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R1", "op": "modify", "part": "22k"},
        follow_redirects=False,
    )
    owner.post(f"/board/{bid}/commit", data={"message": "c2"}, follow_redirects=False)
    conn = get_conn()
    committed = [n for n in models.list_nodes(conn, bid)
                 if n["is_committed"] and n["parent_id"] is not None]
    parent, child = committed[0], committed[1]
    conn.execute(
        "UPDATE nodes SET committed_at=? WHERE id=?",
        ("2026-06-01T00:00:00+00:00", parent["id"]),
    )
    conn.execute(
        "UPDATE nodes SET committed_at=? WHERE id=?",
        ("2026-06-10T00:00:00+00:00", child["id"]),
    )
    conn.commit()
    opened = int(models.get_node(get_conn(), parent["id"])["revision"])
    ada = models.get_user_by_username(get_conn(), "ada")
    conn = get_conn()
    conn.execute(
        "UPDATE nodes SET revision = revision + 1, revised_by = ? WHERE id = ?",
        (ada["id"], parent["id"]),
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
    saved = owner.post(
        f"/board/{bid}/node/{parent['id']}/insert",
        data={
            "committed_at": "2026-06-05T00:00:00+00:00",
            "message": "ins",
            "revision": str(opened),
            "changes": json.dumps([{"reference": "D1", "op": "add", "part": "1uF"}]),
        },
    )
    assert saved.status_code == 200, saved.text
    assert "已被 ada 修改，请刷新" in saved.text
    assert get_conn().execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"] == before


def test_page_carries_revision_and_other_writes_reject_stale(tmp_path, monkeypatch):
    """撤销、清除、提交、复制、改说明、删除都带版本号，过期就不覆盖。"""
    from tests.helpers import auth_client
    owner = auth_client(tmp_path, monkeypatch, username="ada")
    bid = _board(owner, uid="more")
    ws = _workspace(bid)
    page = owner.get(f"/board/{bid}/node/{ws['id']}").text
    assert f'id="node-revision" value="{ws["revision"]}"' in page
    assert 'name="revision"' in page

    owner.post(
        f"/board/{bid}/node/{ws['id']}/edit",
        data={"reference": "R1", "op": "modify", "part": "47k", "revision": "0"},
    )
    opened = 0
    undone = owner.post(
        f"/board/{bid}/node/{ws['id']}/undo",
        data={"reference": "R1", "revision": str(opened)},
    )
    assert "已被 ada 修改，请刷新" in undone.text
    assert models.get_change(get_conn(), ws["id"], "R1")["part"] == "47k"

    current = int(models.get_node(get_conn(), ws["id"])["revision"])
    cleared = owner.post(
        f"/board/{bid}/node/{ws['id']}/undo-all",
        data={"revision": str(current)},
    )
    assert cleared.status_code == 200, cleared.text
    assert models.get_change(get_conn(), ws["id"], "R1") is None

    owner.post(
        f"/board/{bid}/node/{ws['id']}/edit",
        data={"reference": "R1", "op": "modify", "part": "33k"},
    )
    stale_commit = owner.post(
        f"/board/{bid}/commit",
        data={"message": "太晚了", "revision": "0"},
        follow_redirects=False,
    )
    assert stale_commit.status_code == 303
    assert "已被 ada 修改，请刷新" in unquote(stale_commit.headers["location"])
    assert models.workspace_node(get_conn(), bid)["id"] == ws["id"]

    committed = owner.post(
        f"/board/{bid}/commit",
        data={"message": "改 R1", "revision": str(models.get_node(get_conn(), ws["id"])["revision"])},
        follow_redirects=False,
    )
    assert committed.status_code == 303
    assert "已提交" in unquote(committed.headers["location"])

    nodes = models.list_nodes(get_conn(), bid)
    done = [n for n in nodes if n["is_committed"] and n["parent_id"] is not None][0]
    draft = models.workspace_node(get_conn(), bid)
    # 新草稿从 0 开始。用一个对不上的版本号，复制不能写进去。
    copied = owner.post(
        f"/board/{bid}/node/{done['id']}/copy-to-draft",
        data={"revision": "9"},
        follow_redirects=False,
    )
    assert "已被" in unquote(copied.headers["location"])
    assert models.get_changeset(get_conn(), draft["id"]) == []

    fresh = int(models.get_node(get_conn(), draft["id"])["revision"])
    copied = owner.post(
        f"/board/{bid}/node/{done['id']}/copy-to-draft",
        data={"revision": str(fresh)},
        follow_redirects=False,
    )
    assert copied.headers["HX-Redirect"].startswith(f"/board/{bid}/node/{draft['id']}")
    assert models.get_change(get_conn(), draft["id"], "R1")["part"] == "33k"

    info = owner.post(
        f"/board/{bid}/node/{done['id']}/edit-info",
        data={"message": "新标题", "revision": str(int(done["revision"]) + 5)},
        follow_redirects=False,
    )
    assert "已被" in unquote(info.headers["location"])
    assert models.get_node(get_conn(), done["id"])["message"] == "改 R1"

    removed = owner.post(
        f"/board/{bid}/node/{done['id']}/delete",
        data={"revision": str(int(done["revision"]) + 5)},
        follow_redirects=False,
    )
    loc = unquote(removed.headers.get("location", ""))
    assert "已被" in (removed.text + loc)
    assert models.get_node(get_conn(), done["id"]) is not None
