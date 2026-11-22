"""用户管理页的浏览器测试：创建、停用使旧会话失效、停用清单不链进单板。"""
import re
import uuid

from playwright.sync_api import expect

from app.csv_import import CsvEntry
from app.db import connect
from app.models import create_board, create_bom_version, create_user
from tests.helpers import auth_httpx


def _channels(rgb: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", rgb)][:3]


def _name(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def test_menu_create_disable_and_session(page, live_server, browser):
    page.goto(live_server + "/")
    page.locator(".topnav-menu-btn").click()
    expect(page.locator("a[href='/admin/users']")).to_be_visible()
    page.locator("a[href='/admin/users']").click()
    expect(page.locator("h1")).to_have_text("用户管理")
    expect(page.locator("body")).to_contain_text("不开放注册")
    expect(page.get_by_role("button", name="删除")).to_have_count(0)

    username = _name("clerk")
    password = "clerk-pass-1"
    form = page.locator("form.form-stack")
    form.locator("input[name=username]").fill(username)
    form.locator("input[name=password]").fill(password)
    form.locator("input[name=password2]").fill(password)
    form.locator("button[type=submit]").click()
    row = page.locator("#users-table tr", has_text=username)
    expect(row).to_be_visible()
    expect(row).to_contain_text("普通用户")
    expect(form.locator("input[name=password]")).to_have_value("")
    disable = row.locator("button", has_text="停用")
    expect(disable).to_have_attribute("hx-confirm", re.compile("不能登录"))

    context = browser.new_context()
    other = context.new_page()
    other.goto(live_server + "/login")
    other.locator("input[name=username]").fill(username)
    other.locator("input[type=password]").fill(password)
    other.locator("button[type=submit]").click()
    expect(other).to_have_url(re.compile(r"/$"))
    expect(other.locator("a[href='/admin/users']")).to_have_count(0)
    denied = other.goto(live_server + "/admin/users")
    assert denied.status == 403
    expect(other.locator("body")).to_contain_text("请联系系统管理员")

    page.once("dialog", lambda dialog: dialog.accept())
    disable.click()
    expect(row).to_contain_text("已停用")
    other.goto(live_server + "/")
    expect(other).to_have_url(re.compile(r"/login"))
    other.locator("input[name=username]").fill(username)
    other.locator("input[type=password]").fill(password)
    other.locator("button[type=submit]").click()
    expect(other.locator(".flash-error")).to_contain_text("账号已停用，请联系管理员")
    context.close()


def test_disabled_owner_panel_transfers_without_opening_board(page, live_server, live_db):
    owner = _name("own")
    newbie = _name("new")
    conn = connect(live_db)
    try:
        owner_id = create_user(conn, owner, "password-1", is_admin=False)
        create_user(conn, newbie, "password-2", is_admin=False)
        create_bom_version(
            conn, "AlphaBoard", "pcb-z9", "bom-z9",
            [CsvEntry("ZR9", "secret-4k7")],
            owner_user_id=owner_id,
        )
        board_id = create_board(
            conn, "AlphaBoard", "pcb-z9", "bom-z9", "UID-ORPHAN",
            owner_user_id=owner_id,
        )
    finally:
        conn.close()
    with auth_httpx(live_server) as client:
        disabled = client.post(f"/admin/users/{owner_id}/disable")
        assert disabled.status_code == 200, disabled.text

    page.goto(live_server + "/admin/users")
    boards = page.locator("#orphaned-boards")
    versions = page.locator("#orphaned-versions")
    expect(boards).to_contain_text("UID-ORPHAN")
    expect(boards).to_contain_text("AlphaBoard")
    expect(boards).not_to_contain_text("secret-4k7")
    expect(boards).not_to_contain_text("ZR9")
    expect(versions).to_contain_text("pcb-z9")
    expect(versions).to_contain_text("bom-z9")
    expect(versions).not_to_contain_text("secret-4k7")
    expect(page.locator("a[href^='/board/']")).to_have_count(0)

    boards.locator("input[name=username]").fill(newbie)
    page.once("dialog", lambda dialog: dialog.accept())
    boards.locator("button", has_text="转让").click()
    expect(boards).not_to_contain_text("UID-ORPHAN")
    with auth_httpx(live_server) as client:
        opened = client.get(f"/board/{board_id}")
        assert opened.status_code == 403


def test_admin_users_contrast_and_narrow(page, live_server):
    page.set_viewport_size({"width": 375, "height": 700})
    page.goto(live_server + "/admin/users")
    page.evaluate("localStorage.setItem('theme','light')")
    page.reload()
    field = page.locator("form.form-stack input[name=username]")
    background = field.evaluate("e => getComputedStyle(e).backgroundColor")
    foreground = field.evaluate("e => getComputedStyle(e).color")
    assert sum(_channels(background)) > 600
    assert sum(_channels(foreground)) < 300
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
    page.evaluate("localStorage.setItem('theme','dark')")
    page.reload()
    field = page.locator("form.form-stack input[name=username]")
    background = field.evaluate("e => getComputedStyle(e).backgroundColor")
    foreground = field.evaluate("e => getComputedStyle(e).color")
    assert sum(_channels(background)) < sum(_channels(foreground)), (background, foreground)
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
