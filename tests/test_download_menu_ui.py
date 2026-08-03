"""Playwright 测试：⋯ 菜单里「⬇ 下载 CSV」展开成二选一（issue #134）。

这一段的交互全压在「菜单里套菜单」上：承载 ctxlinks 的 .menu-pop 挂了
@click="nav=false"，不给展开容器加 @click.stop 的话，点父项时整个菜单先关了，
子项根本没机会露出来——模板级的字符串断言看不出这个，必须真点。
"""
import httpx
from playwright.sync_api import Page, expect


def _api_create_board(base, uid):
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "DLBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\nC1,100nF\n",
                                   "text/csv")})
        bid = r.headers.get("location", "").split("/board/")[-1].split("?")[0]
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "R1", "op": "modify", "part": "22k"})
    return bid


def _open_workspace(page: Page, base: str, bid: str):
    page.goto(f"{base}/board/{bid}")
    page.locator("a.tl-card", has_text="工作区草稿").click()
    page.wait_for_load_state("networkidle")


def _open_menu(page: Page):
    page.locator(".topnav-menu-btn").click()
    expect(page.locator(".topnav-actions")).to_be_visible()


def test_download_expands_without_closing_menu(page: Page, live_server):
    """点「⬇ 下载 CSV」露出两条子项，且整个 ⋯ 菜单不能被这一点关掉。"""
    bid = _api_create_board(live_server, "DL1")
    _open_workspace(page, live_server, bid)
    _open_menu(page)

    sub = page.locator(".menu-sub")
    expect(sub).to_be_hidden()
    page.locator(".topnav-actions button", has_text="下载 CSV").click()
    expect(page.locator(".topnav-actions")).to_be_visible()
    expect(sub.get_by_text("完整 BOM")).to_be_visible()
    expect(sub.get_by_text("本节点修改项")).to_be_visible()


def test_download_links_carry_scope(page: Page, live_server):
    bid = _api_create_board(live_server, "DL2")
    _open_workspace(page, live_server, bid)
    node_id = page.url.rstrip("/").rsplit("/", 1)[-1]
    _open_menu(page)
    page.locator(".topnav-actions button", has_text="下载 CSV").click()

    full = page.locator(".menu-sub a", has_text="完整 BOM")
    changes = page.locator(".menu-sub a", has_text="本节点修改项")
    assert full.get_attribute("href") == \
        f"/board/{bid}/node/{node_id}/download?scope=full"
    assert changes.get_attribute("href") == \
        f"/board/{bid}/node/{node_id}/download?scope=changes"


def test_download_changes_downloads_change_csv(page: Page, live_server):
    """点「本节点修改项」真的下到 Reference,Part,OP 三列的 CSV。"""
    bid = _api_create_board(live_server, "DL3")
    _open_workspace(page, live_server, bid)
    _open_menu(page)
    page.locator(".topnav-actions button", has_text="下载 CSV").click()

    with page.expect_download() as dl:
        page.locator(".menu-sub a", has_text="本节点修改项").click()
    text = open(dl.value.path(), encoding="utf-8-sig").read()
    lines = text.splitlines()
    assert lines[0] == "Reference,Part,OP"
    assert "R1,22k,modify" in lines
    assert dl.value.suggested_filename.endswith(".csv")


def test_escape_closes_submenu(page: Page, live_server):
    bid = _api_create_board(live_server, "DL4")
    _open_workspace(page, live_server, bid)
    _open_menu(page)
    page.locator(".topnav-actions button", has_text="下载 CSV").click()
    expect(page.locator(".menu-sub")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".menu-sub")).to_be_hidden()


def test_click_outside_closes_submenu(page: Page, live_server):
    """@click.stop 拦了冒泡，关闭只能靠 base.html 捕获阶段广播的 close-menus。"""
    bid = _api_create_board(live_server, "DL5")
    _open_workspace(page, live_server, bid)
    _open_menu(page)
    page.locator(".topnav-actions button", has_text="下载 CSV").click()
    expect(page.locator(".menu-sub")).to_be_visible()
    page.locator("h1").click()
    expect(page.locator(".menu-sub")).to_be_hidden()
    expect(page.locator(".topnav-actions")).to_be_hidden()
