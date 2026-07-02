"""issue #84：状态演进界面三点菜单在点击卡片等元素时不会消失的 UI 检验。"""
import httpx
from playwright.sync_api import Page, expect

from tests.test_issue79_ui import _make_chain, _c1


def _enter_cmp(page):
    page.locator('[data-testid="compare-toggle"]').click()


def test_menu_closes_on_clicking_own_card(live_server, page: Page):
    """对比模式下弹出菜单后点击所在卡片，卡片 preventDefault 不跳转，菜单应关闭。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    _enter_cmp(page)
    item = _c1(page)
    item.locator(".menu-btn").click()
    menu = item.locator(".menu-pop")
    expect(menu).to_be_visible()
    # 点击本卡片的标题区（对比模式下 preventDefault，不离开本页）
    item.locator(".tl-card").first.click()
    expect(menu).to_be_hidden()


def test_menu_closes_on_clicking_other_card(live_server, page: Page):
    """对比模式下弹出菜单后点击另一张卡片，原菜单应关闭。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    _enter_cmp(page)
    opener = _c1(page)
    opener.locator(".menu-btn").click()
    menu = opener.locator(".menu-pop")
    expect(menu).to_be_visible()
    # 点击硬更改卡片（对比模式下可点但不跳转）
    hard = page.locator(".tl-item.hard", has_text="返修 U1")
    hard.locator(".tl-card").first.click()
    expect(menu).to_be_hidden()


def test_menu_closes_on_clicking_other_card_menu_btn(live_server, page: Page):
    """复测：弹出菜单后点击另一张卡片的三点按钮（其 @click.stop 会阻止冒泡），原菜单仍应关闭。

    这里改在根节点（链底）上展开菜单：根节点在下，其下拉菜单落入下方空白，
    不会覆盖上方「加 C9」节点的三点按钮——issue #105 修复后，被菜单遮挡的按钮
    不再可点击，故需选一个未被遮挡的「其它卡片」按钮来验证关闭机制。
    """
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    opener = page.locator(".tl-item.node.root")
    opener.locator(".menu-btn").click()
    menu = opener.locator(".menu-pop")
    expect(menu).to_be_visible()
    other = page.locator(".tl-item.node", has_text="加 C9")
    other.locator(".menu-btn").click()
    expect(menu).to_be_hidden()
    # 同时另一张卡片的菜单应已展开
    expect(other.locator(".menu-pop")).to_be_visible()


def test_menu_closes_on_clicking_other_card_copy_btn(live_server, page: Page):
    """复测：弹出菜单后点击另一张卡片的复制按钮（其 stopPropagation 会阻止冒泡），原菜单仍应关闭。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    opener = _c1(page)
    opener.locator(".menu-btn").click()
    menu = opener.locator(".menu-pop")
    expect(menu).to_be_visible()
    other = page.locator(".tl-item.node", has_text="加 C9")
    other.locator(".copy-hash").click()
    expect(menu).to_be_hidden()
