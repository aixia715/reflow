import pytest
from app.db import connect, init_db
from app import models, audit
from app.csv_import import CsvEntry


@pytest.fixture
def conn():
    c = connect(":memory:")
    init_db(c)
    models.create_bom_version(c, "B", "v1", "bomA", [CsvEntry("R1", "10k")])
    bid = models.create_board(c, "B", "v1", "bomA", "3")
    return c, bid


def test_append_only_never_overwrites(conn):
    c, bid = conn
    node_id = models.list_nodes(c, bid)[1]["id"]
    for new in ("47k", "22k", "33k"):
        audit.record_edit(c, node_id, "R1", old_part="10k", new_part=new,
                          op="modify", source="direct")
    rows = audit.list_log(c, node_id)
    assert len(rows) == 3
    assert [r["new_part"] for r in rows] == ["47k", "22k", "33k"]


def test_source_marking(conn):
    c, bid = conn
    node_id = models.list_nodes(c, bid)[1]["id"]
    audit.record_edit(c, node_id, "R1", "10k", "47k", "modify", "direct")
    audit.record_edit(c, node_id, "R1", "47k", "22k", "modify", "propagated")
    rows = audit.list_log(c, node_id)
    assert {r["source"] for r in rows} == {"direct", "propagated"}


def test_actor_username_joined_and_null_when_omitted(conn):
    """带 actor_user_id 时日志查询能读到登录名；不传时该列为 NULL。"""
    c, bid = conn
    user_id = models.create_user(c, "huaqing", "test-password", is_admin=True)
    node_id = models.list_nodes(c, bid)[1]["id"]
    audit.record_edit(
        c, node_id, "R1", "10k", "47k", "modify", "direct", actor_user_id=user_id,
    )
    audit.record_edit(c, node_id, "C1", None, "1uF", "add", "direct")
    rows = {r["reference"]: r for r in models.list_board_log(c, bid)}
    assert rows["R1"]["actor_user_id"] == user_id
    assert rows["R1"]["actor_username"] == "huaqing"
    assert rows["C1"]["actor_user_id"] is None
    assert rows["C1"]["actor_username"] is None


def test_disabled_actor_still_shows_username(conn):
    """停用账号不改历史：日志仍是当时的登录名，不是「已删除」。"""
    c, bid = conn
    admin_id = models.create_user(c, "admin", "test-password", is_admin=True)
    user_id = models.create_user(c, "huaqing", "test-password", is_admin=False)
    node_id = models.list_nodes(c, bid)[1]["id"]
    audit.record_edit(
        c, node_id, "R1", "10k", "47k", "modify", "direct", actor_user_id=user_id,
    )
    models.disable_user(c, admin_id, user_id)
    row = models.list_board_log(c, bid)[0]
    assert row["actor_user_id"] == user_id
    assert row["actor_username"] == "huaqing"


def _new_board(client, uid="1"):
    response = client.post(
        "/board/new",
        data={"board_name": "B", "pcb_version": "v1", "bom_version": "bomA", "board_uid": uid},
        files={"file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def _table(html: str) -> str:
    return html.split("<table", 1)[1]


def test_log_page_marks_rows_without_actor(tmp_path, monkeypatch):
    """没有操作人的旧行，日志页显示「（升级前）」，表头在「来源」左侧。"""
    from app.main import get_conn
    from tests.helpers import auth_client
    client = auth_client(tmp_path, monkeypatch, username="owner")
    bid = _new_board(client)
    node_id = models.list_nodes(get_conn(), int(bid))[1]["id"]
    audit.record_edit(
        get_conn(), node_id, "C1", "100nF", "1uF", "modify", "direct", note="升级前的记录",
    )
    page = client.get(f"/board/{bid}/log")
    assert page.status_code == 200
    head = page.text.split("</thead>", 1)[0]
    assert head.index("操作人") < head.index("来源")
    assert "（升级前）" in _table(page.text)


def test_editor_edit_shows_username_on_log_page(tmp_path, monkeypatch):
    """editor 改一个位号后，日志页表格里是他的登录名，库里的 actor 也是他。"""
    from app.main import get_conn
    from tests.helpers import auth_client
    owner = auth_client(tmp_path, monkeypatch, username="owner")
    editor = auth_client(tmp_path, monkeypatch, username="editor", is_admin=False)
    bid = _new_board(owner, uid="2")
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    edited = editor.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R1", "op": "modify", "part": "47k"},
        follow_redirects=False,
    )
    assert edited.status_code == 303, edited.text
    page = editor.get(f"/board/{bid}/log")
    table = _table(page.text)
    assert "editor" in table
    assert "47k" in table
    row = get_conn().execute(
        "SELECT actor_user_id FROM edit_log WHERE reference='R1'"
    ).fetchone()
    editor_id = models.get_user_by_username(get_conn(), "editor")["id"]
    assert row["actor_user_id"] == editor_id


def test_disabled_editor_name_stays_on_log_page(tmp_path, monkeypatch):
    """账号停用后，日志页仍显示登录名，不改成「已删除」或「（升级前）」。"""
    from app.main import get_conn
    from tests.helpers import auth_client
    owner = auth_client(tmp_path, monkeypatch, username="owner")
    editor = auth_client(tmp_path, monkeypatch, username="editor", is_admin=False)
    bid = _new_board(owner, uid="3")
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    editor.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R1", "op": "modify", "part": "47k"},
        follow_redirects=False,
    )
    conn = get_conn()
    owner_id = models.get_user_by_username(conn, "owner")["id"]
    editor_id = models.get_user_by_username(conn, "editor")["id"]
    models.disable_user(conn, owner_id, editor_id)
    page = owner.get(f"/board/{bid}/log")
    row_html = next(
        piece for piece in _table(page.text).split("<tr>") if "47k" in piece
    )
    assert "editor" in row_html
    assert "（升级前）" not in row_html
    assert "已删除" not in row_html


def test_resolve_route_stamps_actor_on_propagated_row(tmp_path, monkeypatch):
    """冲突确认走路由时，propagated 行的操作人是按下确认的 editor。"""
    from app.main import get_conn
    from tests.helpers import auth_client
    owner = auth_client(tmp_path, monkeypatch, username="owner")
    editor = auth_client(tmp_path, monkeypatch, username="editor", is_admin=False)
    bid = _new_board(owner, uid="4")
    owner.post(f"/board/{bid}/members", data={"username": "editor", "role": "editor"})
    editor.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R1", "op": "modify", "part": "47k"},
        follow_redirects=False,
    )
    editor.post(f"/board/{bid}/commit", data={"message": "改成 47k"}, follow_redirects=False)
    editor.post(
        f"/board/{bid}/workspace/edit",
        data={"reference": "R1", "op": "modify", "part": "22k"},
        follow_redirects=False,
    )
    editor.post(f"/board/{bid}/commit", data={"message": "改成 22k"}, follow_redirects=False)
    nodes = models.list_nodes(get_conn(), int(bid))
    committed = [n for n in nodes if n["is_committed"] and n["parent_id"] is not None]
    first, second = committed[0], committed[1]
    conflict = editor.post(
        f"/board/{bid}/node/{first['id']}/edit",
        data={"reference": "R1", "op": "modify", "part": "5k"},
    )
    assert conflict.status_code == 200, conflict.text
    # 有冲突时第一次请求不写库：这条仍是提交时的 47k，不是弹窗里的 5k。
    assert models.get_change(get_conn(), first["id"], "R1")["part"] == "47k"
    resolved = editor.post(
        f"/board/{bid}/node/{first['id']}/resolve",
        data={
            "edit_reference": "R1", "op": "modify", "part": "5k",
            "downstream_node_id": str(second["id"]), "reference": "R1", "choice": "take",
        },
        follow_redirects=False,
    )
    assert resolved.status_code == 303, resolved.text
    row = get_conn().execute(
        "SELECT actor_user_id FROM edit_log WHERE node_id=? AND source='propagated'",
        (second["id"],),
    ).fetchone()
    editor_id = models.get_user_by_username(get_conn(), "editor")["id"]
    assert row["actor_user_id"] == editor_id
