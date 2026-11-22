"""按单板角色允许与拒绝。阶段 2 的矩阵抽查。"""
import json

from fastapi.testclient import TestClient

from app import hashing, models
from app.main import create_app, get_conn
from tests.helpers import auth_client


def _people(tmp_path, monkeypatch):
    upload = tmp_path / "uploads"
    owner = auth_client(
        tmp_path, monkeypatch, username="owner", password="test-password",
        is_admin=True, upload_dir=upload,
    )
    editor = auth_client(
        tmp_path, monkeypatch, username="editor", password="test-password",
        is_admin=False,
    )
    viewer = auth_client(
        tmp_path, monkeypatch, username="viewer", password="test-password",
        is_admin=False,
    )
    stranger = auth_client(
        tmp_path, monkeypatch, username="stranger", password="test-password",
        is_admin=False,
    )
    return owner, editor, viewer, stranger


def _new_board(client, name="B", pcb="v1", bom="bomA", uid="1",
               csv="Reference,Part\nR1,10k\nC1,100nF\n"):
    response = client.post(
        "/board/new",
        data={"board_name": name, "pcb_version": pcb, "bom_version": bom, "board_uid": uid},
        files={"file": ("bom.csv", csv, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def _ids(board_id):
    conn = get_conn()
    nodes = models.list_nodes(conn, int(board_id))
    root = next(n["id"] for n in nodes if n["parent_id"] is None)
    draft = models.workspace_node(conn, int(board_id))["id"]
    return root, draft


def _bom(name="B", pcb="v1", bom="bomA"):
    return dict(models.get_initial_bom(get_conn(), name, pcb, bom))


def _board_count():
    return get_conn().execute("SELECT COUNT(*) AS n FROM boards_hierarchy").fetchone()["n"]


def test_viewer_can_read_but_not_edit(tmp_path, monkeypatch):
    owner, editor, viewer, stranger = _people(tmp_path, monkeypatch)
    bid = _new_board(owner)
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    owner.post(f"/board/{bid}/members", data={"username": "viewer", "role": "viewer"})
    root, draft = _ids(bid)

    page = viewer.get(f"/board/{bid}")
    assert page.status_code == 200
    node = viewer.get(f"/board/{bid}/node/{draft}")
    assert node.status_code == 200
    assert "你对这块单板是只读" in node.text
    assert f'action="/board/{bid}/commit"' not in node.text

    edited = viewer.post(
        f"/board/{bid}/node/{draft}/edit",
        data={"reference": "R9", "op": "add", "part": "10k"},
        headers={"HX-Request": "true"},
    )
    assert edited.status_code == 200
    assert edited.headers["HX-Reswap"] == "none"
    assert json.loads(edited.headers["HX-Trigger"])["showToast"] == "没有权限做这个操作"
    assert models.get_changeset(get_conn(), draft) == []

    committed = editor.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R9", "op": "add", "part": "10k"},
    )
    assert committed.status_code == 200
    done = editor.post(f"/board/{bid}/commit", data={"message": "加 R9"}, follow_redirects=False)
    assert done.status_code == 303

    denied = editor.delete(f"/board/{bid}")
    assert denied.status_code == 403
    assert models.get_board(get_conn(), int(bid)) is not None

    hidden = stranger.get(f"/board/{bid}/node/{root}")
    assert hidden.status_code == 403
    assert "owner" in hidden.text
    assert models.get_board(get_conn(), 999999) is None
    assert owner.get("/board/999999").status_code == 404


def test_members_and_transfer(tmp_path, monkeypatch):
    owner, editor, viewer, _stranger = _people(tmp_path, monkeypatch)
    bid = _new_board(owner, uid="M1")
    added = owner.post(f"/board/{bid}/members", data={"username": "viewer", "role": "viewer"})
    assert added.status_code == 200
    assert viewer.get(f"/board/{bid}").status_code == 200
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    assert editor.get(f"/board/{bid}/members").status_code == 403

    conn = get_conn()
    owner_id = models.get_user_by_username(conn, "owner")["id"]
    editor_id = models.get_user_by_username(conn, "editor")["id"]
    rejected = owner.post(f"/board/{bid}/members/{owner_id}", data={"role": "viewer"})
    assert rejected.status_code == 200
    assert "不能修改所有者" in rejected.text
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM board_members WHERE board_id=? AND role='owner'",
        (int(bid),),
    ).fetchone()["n"] == 1

    transferred = owner.post(f"/board/{bid}/transfer", data={"user_id": editor_id})
    assert transferred.status_code == 200
    assert models.load_membership(get_conn(), owner_id, int(bid)) == "editor"
    assert models.load_membership(get_conn(), editor_id, int(bid)) == "owner"
    assert owner.delete(f"/board/{bid}").status_code == 403
    assert models.get_board(get_conn(), int(bid)) is not None
    removed = editor.delete(f"/board/{bid}")
    assert removed.status_code == 200
    assert models.get_board(get_conn(), int(bid)) is None


def test_home_hides_other_peoples_boards(tmp_path, monkeypatch):
    owner, _editor, _viewer, stranger = _people(tmp_path, monkeypatch)
    _new_board(owner, uid="ONLY-OWNER")
    page = stranger.get("/")
    assert "ONLY-OWNER" not in page.text
    assert "你还没有被加入任何单板" in page.text


def test_root_edit_does_not_touch_initial_bom(tmp_path, monkeypatch):
    owner, editor, viewer, stranger = _people(tmp_path, monkeypatch)
    bid = _new_board(owner, uid="R1")
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    owner.post(f"/board/{bid}/members", data={"username": "viewer", "role": "viewer"})
    _new_board(stranger, name="Other", bom="bomB", uid="S1",
               csv="Reference,Part\nR2,22k\n")
    root, _draft = _ids(bid)
    before = _bom()
    for client in (owner, editor, viewer, stranger):
        response = client.post(
            f"/board/{bid}/node/{root}/edit",
            data={"reference": "R1", "op": "modify", "part": "47k"},
        )
        assert response.status_code == 403
        assert _bom() == before
    html = owner.get(f"/board/{bid}/node/{root}").text
    assert f"/node/{root}/edit" not in html
    assert "初始清单在 BOM 版本页维护" in html
    assert 'href="/bom-version/' in html
    denied = owner.post(
        f"/board/{bid}/node/{root}/edit",
        data={"reference": "R1", "op": "modify", "part": "47k"},
    )
    assert "请到 BOM 版本页修正" in denied.text
    assert "/bom-version/" in denied.text


def test_only_version_owner_can_add_board(tmp_path, monkeypatch):
    owner, editor, _viewer, stranger = _people(tmp_path, monkeypatch)
    bid = _new_board(owner, uid="A1")
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    before = _board_count()
    denied = editor.post(
        "/board/new",
        data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA", "board_uid": "A2"},
        follow_redirects=False,
    )
    assert denied.status_code == 403
    assert _board_count() == before
    created = owner.post(
        "/board/new",
        data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA", "board_uid": "A2"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    new_id = int(created.headers["location"].split("?")[0].rsplit("/", 1)[-1])
    owner_id = models.get_user_by_username(get_conn(), "owner")["id"]
    assert models.load_membership(get_conn(), owner_id, new_id) == "owner"

    fresh = stranger.post(
        "/board/new",
        data={"board_name": "Fresh", "pcb_version": "v1", "bom_version": "bomZ", "board_uid": "Z1"},
        files={"file": ("bom.csv", "Reference,Part\nR1,10k\n", "text/csv")},
        follow_redirects=False,
    )
    assert fresh.status_code == 303
    stranger_id = models.get_user_by_username(get_conn(), "stranger")["id"]
    version = models.get_bom_version_row(get_conn(), "Fresh", "v1", "bomZ")
    assert version["owner_user_id"] == stranger_id


def test_scope_delete_requires_every_board_and_typed_name(tmp_path, monkeypatch):
    owner, _editor, _viewer, stranger = _people(tmp_path, monkeypatch)
    mine = _new_board(owner, name="Group", bom="bomA", uid="MINE")
    _new_board(stranger, name="Group", bom="bomB", uid="THEIRS",
               csv="Reference,Part\nR1,10k\n")
    denied = owner.delete("/board-group?board_name=Group")
    assert denied.status_code == 403
    assert "THEIRS" not in denied.text
    assert models.get_board(get_conn(), int(mine)) is not None

    _new_board(owner, name="Owned", bom="bomA", uid="ONE")
    _new_board(owner, name="Owned", bom="bomA", uid="TWO")
    preview = owner.delete("/board-group?board_name=Owned")
    assert preview.status_code == 200
    assert "ONE" in preview.text and "TWO" in preview.text
    assert models.get_board(get_conn(), int(mine)) is not None
    wrong = owner.delete("/board-group?board_name=Owned&confirm_name=nope")
    assert wrong.status_code == 400
    assert get_conn().execute(
        "SELECT COUNT(*) AS n FROM boards_hierarchy WHERE board_name='Owned'"
    ).fetchone()["n"] == 2
    gone = owner.delete("/board-group?board_name=Owned&confirm_name=Owned")
    assert gone.status_code == 200
    assert get_conn().execute(
        "SELECT COUNT(*) AS n FROM boards_hierarchy WHERE board_name='Owned'"
    ).fetchone()["n"] == 0


def test_hash_miss_on_invisible_board_matches_not_found(tmp_path, monkeypatch):
    owner, _editor, _viewer, stranger = _people(tmp_path, monkeypatch)
    bid = _new_board(owner, uid="H1")
    owner.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R9", "op": "add", "part": "10k"},
    )
    owner.post(f"/board/{bid}/commit", data={"message": "c"}, follow_redirects=False)
    node_id = next(
        n["id"] for n in models.list_nodes(get_conn(), int(bid))
        if n["is_committed"] and n["parent_id"] is not None
    )
    prefix = hashing.node_hash(node_id)[:8]
    hidden = stranger.get(f"/hash-lookup?q={prefix}")
    missing = stranger.get("/hash-lookup?q=dead")
    assert json.loads(hidden.headers["HX-Trigger"])["showToast"] == \
        json.loads(missing.headers["HX-Trigger"])["showToast"]
    assert "未找到" in json.loads(hidden.headers["HX-Trigger"])["showToast"]


def test_compare_download_hides_invisible_board(tmp_path, monkeypatch):
    owner, _editor, viewer, _stranger = _people(tmp_path, monkeypatch)
    left_id = _new_board(owner, name="Cmp", bom="bomA", uid="LEFT")
    right_id = _new_board(owner, name="Cmp", bom="bomB", uid="RIGHT",
                          csv="Reference,Part\nR1,22k\n")
    owner.post(f"/board/{left_id}/members", data={"username": "viewer", "role": "viewer"})
    left_node = _ids(left_id)[0]
    right_node = _ids(right_id)[0]
    hidden = viewer.get(
        f"/board/{left_id}/compare/download?left={left_node}&right={right_node}")
    assert hidden.status_code == 404
    assert "RIGHT" not in hidden.text
    both = owner.get(
        f"/board/{left_id}/compare/download?left={left_node}&right={right_node}")
    assert both.status_code == 200
    assert "text/csv" in both.headers["content-type"]


def test_image_and_attachment_downloads_are_authorized(tmp_path, monkeypatch):
    owner, editor, viewer, stranger = _people(tmp_path, monkeypatch)
    bid = _new_board(owner, uid="F1")
    owner.post(f"/board/{bid}/members", data={"username": "viewer", "role": "viewer"})
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    created = owner.post(
        f"/board/{bid}/hard-change",
        data={"title": "飞线", "occurred_at": "2026-06-01T10:30", "description": ""},
        files=[("files", ("p.png", b"\x89PNG\r\n", "image/png"))],
        follow_redirects=False,
    )
    assert created.status_code == 303
    hc_id = get_conn().execute(
        "SELECT id FROM hard_changes WHERE board_id=?", (int(bid),)
    ).fetchone()["id"]
    image = models.list_hard_change_images(get_conn(), hc_id)[0]
    url = f"/board/{bid}/hard-change/{hc_id}/images/{image['id']}"
    assert viewer.get(url).content == b"\x89PNG\r\n"
    assert stranger.get(url).status_code == 403
    anon = TestClient(create_app())
    login = anon.get(url, follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"].startswith("/login")

    detail = owner.get(f"/board/{bid}/hard-change/{hc_id}").text
    form = owner.get(f"/board/{bid}/hard-change/{hc_id}/edit").text
    assert "/uploads/" not in detail
    assert "/uploads/" not in form
    assert url in detail

    _root, draft = _ids(bid)
    owner.post(
        f"/board/{bid}/node/{draft}/attachments",
        files=[("files", ("note.txt", b"BYTES", "text/plain"))],
    )
    attachment = models.list_node_attachments(get_conn(), draft)[0]
    downloaded = viewer.get(
        f"/board/{bid}/node/{draft}/attachments/{attachment['id']}/download")
    assert downloaded.content == b"BYTES"
    assert viewer.get("/uploads/" + attachment["storage_path"]).status_code == 404
    assert viewer.get("/uploads/" + image["filename"]).status_code == 404

    assert viewer.get(f"/board/{bid}/node/{draft}/download?scope=full").status_code == 200
    owner.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R9", "op": "add", "part": "10k"},
    )
    assert viewer.get(f"/board/{bid}/node/{draft}/download?scope=changes").status_code == 200
    assert stranger.get(f"/board/{bid}/node/{_root}/download?scope=full").status_code == 403
    assert viewer.get(f"/board/{bid}/node/{draft}/import/template").status_code == 403
    assert editor.get(f"/board/{bid}/node/{draft}/import/template").status_code == 200
