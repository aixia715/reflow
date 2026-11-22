"""两人同时改同一份草稿：后打开的页面保存时被拒绝，先写入的值还在。"""
import uuid

from playwright.sync_api import expect

from app.db import connect
from app.models import create_user, get_change, get_user_by_username, list_nodes
from tests.helpers import auth_httpx, login_cookies, playwright_cookies


def test_second_editor_sees_stale_revision_message(live_server, live_db, browser):
    suffix = uuid.uuid4().hex[:8]
    with auth_httpx(live_server, follow_redirects=False) as client:
        created = client.post(
            "/board/new",
            data={"board_name": "Rev", "pcb_version": "v1", "bom_version": "bomA",
                  "board_uid": "R" + suffix},
            files={"file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
        )
        assert created.status_code == 303, created.text
        bid = int(created.headers["location"].split("?")[0].rsplit("/", 1)[-1])

    conn = connect(live_db)
    if get_user_by_username(conn, "bao") is None:
        create_user(conn, "bao", "test-password", is_admin=False)
    conn.close()
    with auth_httpx(live_server) as client:
        added = client.post(f"/board/{bid}/members", data={"username": "bao", "role": "editor"})
        assert added.status_code == 200, added.text

    ws = next(n for n in list_nodes(connect(live_db), bid) if not n["is_committed"])
    url = f"{live_server}/board/{bid}/node/{ws['id']}"

    admin = browser.new_context()
    admin.add_cookies(playwright_cookies(live_server, login_cookies(live_server)))
    page_a = admin.new_page()
    other = browser.new_context()
    other.add_cookies(playwright_cookies(
        live_server, login_cookies(live_server, username="bao")))
    page_b = other.new_page()
    page_a.goto(url)
    page_b.goto(url)
    expect(page_a.locator("#node-revision")).to_have_value(str(ws["revision"]))
    expect(page_b.locator("#node-revision")).to_have_value(str(ws["revision"]))

    page_a.locator(".edit-form input[name=reference]").fill("R1")
    page_a.locator(".edit-form input[name=part]").fill("47k")
    page_a.locator(".edit-form button.btn-primary").click()
    expect(page_a.locator(".toast")).to_contain_text("已修改")
    expect(page_a.locator("#bom")).to_contain_text("47k")

    page_b.locator(".edit-form input[name=reference]").fill("R1")
    page_b.locator(".edit-form input[name=part]").fill("22k")
    page_b.locator(".edit-form button.btn-primary").click()
    expect(page_b.locator("body")).to_contain_text("已被 admin 修改，请刷新")
    expect(page_b.locator("#bom")).not_to_contain_text("22k")
    page_b.screenshot(path="/opt/cursor/artifacts/revision-stale.png")

    assert get_change(connect(live_db), ws["id"], "R1")["part"] == "47k"
    admin.close()
    other.close()
