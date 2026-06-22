"""对比入口的浏览器测试。"""
import re
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
    toggle = page.locator("[data-testid=compare-toggle]")
    # 默认按钮文案是「对比节点…」
    assert toggle.inner_text().strip() == "对比节点…"
    toggle.click()
    # 进入对比状态后按钮文案变为「退出对比」，给用户清晰提示
    assert toggle.inner_text().strip() == "退出对比"
    # 不应再出现复选框
    expect(page.locator(".cmp-check")).to_have_count(0)
    # 单击节点卡片选中，选中后卡片高亮
    cards = page.locator(".tl-item.node .tl-card")
    cards.nth(0).click()
    expect(cards.nth(0)).to_have_class(re.compile(r".*\bselected\b.*"))
    cards.nth(1).click()
    expect(cards.nth(1)).to_have_class(re.compile(r".*\bselected\b.*"))
    bar = page.locator("[data-testid=compare-bar]")
    expect(bar).to_be_visible()
    go = page.locator("[data-testid=compare-go]")
    href = go.get_attribute("href")
    assert "/compare?left=" in href and "right=" in href
    # 再次点击已选中节点取消选中
    cards.nth(0).click()
    expect(cards.nth(0)).not_to_have_class(re.compile(r".*\bselected\b.*"))
    # 再次点击「退出对比」按钮退出对比状态，文案恢复
    toggle.click()
    assert toggle.inner_text().strip() == "对比节点…"


def test_compare_mode_click_node_does_not_navigate(live_server, page: Page):
    """对比模式下单击节点只选中、不跳转到节点页。"""
    bid = _make_board(live_server, uid="CMP2")
    page.goto(f"{live_server}/board/{bid}")
    page.click("[data-testid=compare-toggle]")
    cards = page.locator(".tl-item.node .tl-card")
    cards.nth(0).click()
    # 仍停留在状态图页
    assert page.url.endswith(f"/board/{bid}")


def test_local_dt_rendered_to_local(live_server, page: Page):
    bid = _make_board(live_server, uid="LDT1")
    page.goto(f"{live_server}/board/{bid}")
    # 节点提交时间已是 UTC（含 +00:00）；渲染后文本不应再带 'T...+00:00'
    el = page.locator("time.local-dt").first
    expect(el).to_be_visible()
    text = el.inner_text()
    assert "+00:00" not in text and "T" not in text
