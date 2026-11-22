"""只读成员与无权访问的界面。不用 conftest 里已登录的 context。"""
import re

from playwright.sync_api import expect

from app.db import connect
from app.models import create_user, get_user_by_username, list_nodes
from tests.helpers import auth_httpx


def _channels(rgb: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", rgb)][:3]


def _new_board(base, name, bom, uid, csv="Reference,Part\nR1,10k\nC1,100nF\n"):
    with auth_httpx(base, follow_redirects=False) as client:
        response = client.post(
            "/board/new",
            data={"board_name": name, "pcb_version": "v1", "bom_version": bom, "board_uid": uid},
            files={"file": ("bom.csv", csv.encode(), "text/csv")},
        )
    return response.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def test_anonymous_lands_on_login_and_bad_password(live_server, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/")
    expect(page).to_have_url(re.compile(r"/login"))
    page.locator("input[name=username]").fill("admin")
    page.locator("input[type=password]").fill("wrong-password")
    page.locator("button[type=submit]").click()
    expect(page.locator(".flash-error")).to_contain_text("用户名或密码错误")
    context.close()


def test_viewer_sees_parts_without_commit_and_forbidden_board(live_server, live_db, browser):
    mine = _new_board(live_server, "Visible", "bomA", "SEE1")
    other = _new_board(live_server, "Hidden", "bomB", "HIDE1")
    conn = connect(live_db)
    if get_user_by_username(conn, "viewer") is None:
        create_user(conn, "viewer", "test-password", is_admin=False)
    conn.close()
    with auth_httpx(live_server) as client:
        added = client.post(f"/board/{mine}/members", data={"username": "viewer", "role": "viewer"})
        assert added.status_code == 200, added.text
        mine_html = client.get(f"/board/{mine}").text
        other_html = client.get(f"/board/{other}").text
    mine_node = re.search(rf"/board/{mine}/node/(\d+)", mine_html).group(0)
    other_node = re.search(rf"/board/{other}/node/(\d+)", other_html).group(0)

    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/login")
    page.locator("input[name=username]").fill("viewer")
    page.locator("input[type=password]").fill("test-password")
    page.locator("button[type=submit]").click()
    page.goto(live_server + mine_node)
    expect(page.locator("text=R1")).to_be_visible()
    expect(page.locator("form.commit-box")).to_have_count(0)
    expect(page.locator(".topnav")).to_contain_text("退出")
    page.goto(live_server + other_node)
    expect(page.locator("body")).to_contain_text("没有这块单板的访问权限")
    expect(page.locator("table.bom")).to_have_count(0)
    context.close()
    # 节点确实存在，403 不是因为库里没有这条链
    assert list_nodes(connect(live_db), int(other))


def test_login_password_dark_contrast(live_server, browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/login")
    page.evaluate("localStorage.setItem('theme','dark')")
    page.reload()
    field = page.locator("input[type=password]")
    background = field.evaluate("e => getComputedStyle(e).backgroundColor")
    foreground = field.evaluate("e => getComputedStyle(e).color")
    assert sum(_channels(background)) < sum(_channels(foreground)), (background, foreground)
    context.close()
