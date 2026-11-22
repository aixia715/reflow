"""审计日志页能看出是谁改的。沿用 live_server，editor 用自己的浏览器上下文。"""
import re

from playwright.sync_api import expect

from app.db import connect
from app.models import create_user, get_user_by_username
from tests.helpers import auth_httpx


def _channels(rgb: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", rgb)][:3]


def test_editor_sees_own_name_and_legacy_actor_on_log(live_server, live_db, browser):
    with auth_httpx(live_server, follow_redirects=False) as client:
        created = client.post(
            "/board/new",
            data={"board_name": "Audit", "pcb_version": "v1", "bom_version": "bomA",
                  "board_uid": "LOG1"},
            files={"file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n", "text/csv")},
        )
        bid = created.headers["location"].split("?")[0].rsplit("/", 1)[-1]
    conn = connect(live_db)
    if get_user_by_username(conn, "editor") is None:
        create_user(conn, "editor", "test-password", is_admin=False)
    draft_id = conn.execute(
        "SELECT id FROM nodes WHERE board_id=? AND is_committed=0", (int(bid),)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO edit_log(node_id,reference,old_part,new_part,op,source,created_at,note)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (draft_id, "C1", "100nF", "1uF", "modify", "direct",
         "2020-01-01T00:00:00+00:00", "升级前的记录"),
    )
    conn.commit()
    conn.close()
    with auth_httpx(live_server) as client:
        added = client.post(
            f"/board/{bid}/members", data={"username": "editor", "role": "editor"},
        )
        assert added.status_code == 200, added.text

    context = browser.new_context()
    page = context.new_page()
    page.goto(live_server + "/login")
    page.locator("input[name=username]").fill("editor")
    page.locator("input[type=password]").fill("test-password")
    page.locator("button[type=submit]").click()
    page.goto(f"{live_server}/board/{bid}/node/{draft_id}")
    form = page.locator("aside form.edit-form").first
    form.locator("input[name=reference]").fill("R1")
    form.locator("input[name=part]").fill("47k")
    form.locator("button.btn-primary").click()
    expect(page.locator(".toast")).to_contain_text("R1")

    page.goto(f"{live_server}/board/{bid}/log")
    table = page.locator("table.bom")
    expect(table).to_contain_text("操作人")
    expect(table.locator("th").nth(4)).to_have_text("操作人")
    expect(table.locator("th").nth(5)).to_have_text("来源")
    editor_row = table.locator("tr", has_text="47k")
    expect(editor_row).to_contain_text("editor")
    expect(editor_row).not_to_contain_text("（升级前）")
    legacy_row = table.locator("tr", has_text="升级前的记录")
    expect(legacy_row).to_contain_text("（升级前）")
    expect(legacy_row).not_to_contain_text("editor")

    page.screenshot(path="/opt/cursor/artifacts/audit-log-light.png", full_page=True)
    page.evaluate("localStorage.setItem('theme','dark')")
    page.reload()
    header = page.locator("table.bom th", has_text="操作人")
    # 夜间表头文字应是浅色，否则操作人列在深色底上读不清
    foreground = header.evaluate("e => getComputedStyle(e).color")
    assert sum(_channels(foreground)) > 400, foreground
    page.screenshot(path="/opt/cursor/artifacts/audit-log-dark.png", full_page=True)

    page.set_viewport_size({"width": 375, "height": 800})
    page.screenshot(path="/opt/cursor/artifacts/audit-log-mobile.png", full_page=True)
    fits = page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )
    assert fits, "375px 下审计日志页不应让整页横滚"
    context.close()
