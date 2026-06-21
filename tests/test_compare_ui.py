"""对比入口的浏览器测试。"""
import httpx
from playwright.sync_api import Page, expect


def _make_board(base: str, uid: str = "CMP1") -> str:
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "CmpBoard", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")})
        bid = r.headers["location"].split("?")[0].rsplit("/", 1)[-1]
        # 多提交一个节点，保证至少两个可选节点
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "C9", "op": "add", "part": "100nF"})
        c.post(f"/board/{bid}/commit", data={"message": "加 C9"})
    return bid


def test_compare_mode_select_two_and_go(live_server, page: Page):
    bid = _make_board(live_server)
    page.goto(f"{live_server}/board/{bid}")
    page.click("[data-testid=compare-toggle]")
    checks = page.locator(".cmp-check")
    expect(checks.first).to_be_visible()
    checks.nth(0).click()
    checks.nth(1).click()
    bar = page.locator("[data-testid=compare-bar]")
    expect(bar).to_be_visible()
    go = page.locator("[data-testid=compare-go]")
    href = go.get_attribute("href")
    assert "/compare?left=" in href and "right=" in href
