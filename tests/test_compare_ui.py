"""对比入口的浏览器测试。"""
import re
import httpx
from playwright.sync_api import Page, expect


def _open_menu(page):
    """功能入口收在 header ⋯ 菜单里（2026-07-18 设计），点击前先展开。"""
    page.click(".topnav-menu-btn")


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
    # ⋯ 菜单里的入口只负责进入对比，文案恒为「对比节点」
    assert toggle.inner_text().strip() == "对比节点"
    _open_menu(page)
    toggle.click()
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
    # 点底部「退出对比」按钮退出对比状态，底部条随之消失
    page.click("[data-testid=compare-exit]")
    expect(bar).not_to_be_visible()
    expect(cards.nth(1)).not_to_have_class(re.compile(r".*\bselected\b.*"))


def test_compare_mode_click_node_does_not_navigate(live_server, page: Page):
    """对比模式下单击节点只选中、不跳转到节点页。"""
    bid = _make_board(live_server, uid="CMP2")
    page.goto(f"{live_server}/board/{bid}")
    _open_menu(page)
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


def test_exit_button_sits_last_in_bar_and_is_danger_styled(live_server, page: Page):
    """「退出对比」是底部条最右侧的红色按钮，⋯ 菜单里不再有该项（issue #132）。"""
    bid = _make_board(live_server, uid="CMP3")
    page.goto(f"{live_server}/board/{bid}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    exit_btn = page.locator("[data-testid=compare-exit]")
    expect(exit_btn).to_be_visible()
    expect(exit_btn).to_have_class(re.compile(r".*\bdanger\b.*"))
    # 红色（--red 在两套主题下都是红系；这里断言不是默认的绿色描边按钮）
    assert exit_btn.evaluate("el => getComputedStyle(el).color") != \
        page.locator("[data-testid=compare-go]").evaluate(
            "el => getComputedStyle(el).color")
    # 是底部条里最后一个按钮，且贴最右
    bar = page.locator("[data-testid=compare-bar]")
    assert bar.locator(".btn").last.get_attribute("data-testid") == "compare-exit"
    bar_right = bar.bounding_box()["x"] + bar.bounding_box()["width"]
    btn_box = exit_btn.bounding_box()
    go_box = page.locator("[data-testid=compare-go]").bounding_box()
    assert btn_box["x"] > go_box["x"]                      # 在「开始对比」右边
    assert bar_right - (btn_box["x"] + btn_box["width"]) < 24  # 紧贴右边缘
    # ⋯ 菜单里不再提供「退出对比」
    _open_menu(page)
    assert "退出对比" not in page.locator(".topnav-actions").inner_text()


def test_escape_key_exits_compare_mode(live_server, page: Page):
    """对比状态下按 ESC 退出，并清空已选节点（issue #132）。"""
    bid = _make_board(live_server, uid="CMP7")
    page.goto(f"{live_server}/board/{bid}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    bar = page.locator("[data-testid=compare-bar]")
    expect(bar).to_be_visible()
    cards = page.locator(".tl-item.node .tl-card")
    cards.nth(0).click()
    expect(bar).to_contain_text("已选择 1/2 个节点")
    page.keyboard.press("Escape")
    expect(bar).not_to_be_visible()
    expect(cards.nth(0)).not_to_have_class(re.compile(r".*\bselected\b.*"))
    # 退出后节点卡片恢复跳转
    cards.nth(0).click()
    assert "/node/" in page.url
    # 再进入对比状态，计数从 0 开始
    page.go_back()
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    expect(bar).to_contain_text("已选择 0/2 个节点")


def test_escape_closes_open_menu_before_exiting_compare(live_server, page: Page):
    """ESC 分层生效：有展开的 ⋯ 菜单时先关菜单，菜单都关了才退出对比。"""
    bid = _make_board(live_server, uid="CMP8")
    page.goto(f"{live_server}/board/{bid}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    bar = page.locator("[data-testid=compare-bar]")
    pop = page.locator(".tl-item.node .menu-pop").first
    # 节点 ⋯ 菜单：ESC 只关菜单，对比状态保留
    page.locator(".tl-item.node .menu-btn").first.click()
    expect(pop).to_be_visible()
    page.keyboard.press("Escape")
    expect(pop).not_to_be_visible()
    expect(bar).to_be_visible()
    # header ⋯ 菜单：同样先关菜单，不退出对比
    _open_menu(page)
    nav = page.locator(".topnav-actions")
    expect(nav).to_be_visible()
    page.keyboard.press("Escape")
    expect(nav).not_to_be_visible()
    expect(bar).to_be_visible()
    # 没有菜单展开时，ESC 才退出对比
    page.keyboard.press("Escape")
    expect(bar).not_to_be_visible()


def test_compare_bar_shows_immediately_with_count_and_disabled_go(live_server, page: Page):
    """进入对比状态即显示「已选择 x/2 个节点」，选满 2 个前「开始对比」不可用。"""
    bid = _make_board(live_server, uid="CMP4")
    page.goto(f"{live_server}/board/{bid}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    bar = page.locator("[data-testid=compare-bar]")
    go = page.locator("[data-testid=compare-go]")
    # 未选任何节点时也应显示
    expect(bar).to_be_visible()
    expect(bar).to_contain_text("已选择 0/2 个节点")
    expect(go).to_have_class(re.compile(r".*\bdisabled\b.*"))
    cards = page.locator(".tl-item.node .tl-card")
    cards.nth(0).click()
    expect(bar).to_contain_text("已选择 1/2 个节点")
    expect(go).to_have_class(re.compile(r".*\bdisabled\b.*"))
    cards.nth(1).click()
    expect(bar).to_contain_text("已选择 2/2 个节点")
    expect(go).not_to_have_class(re.compile(r".*\bdisabled\b.*"))


def test_compare_go_href_and_aria_disabled_before_two_selected(live_server, page: Page):
    """选满 2 个节点前，「开始对比」href 不应含 undefined，且带 aria-disabled。"""
    bid = _make_board(live_server, uid="CMP6")
    page.goto(f"{live_server}/board/{bid}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    go = page.locator("[data-testid=compare-go]")
    assert go.get_attribute("href") == "#"
    assert go.get_attribute("aria-disabled") == "true"
    assert go.get_attribute("tabindex") == "-1"
    cards = page.locator(".tl-item.node .tl-card")
    cards.nth(0).click()
    cards.nth(1).click()
    href = go.get_attribute("href")
    assert "/compare?left=" in href and "right=" in href
    # Alpine 对绑定 false 的非布尔属性会直接移除，而非写 "false"
    assert go.get_attribute("aria-disabled") is None
    assert go.get_attribute("tabindex") == "0"


def test_hard_change_disabled_in_compare_mode(live_server, page: Page):
    """进入对比状态后硬更改卡片置灰不可选、⋯菜单不可用，退出后恢复。"""
    bid = _make_board(live_server, uid="CMP5")
    with httpx.Client(base_url=live_server, follow_redirects=False) as c:
        c.post(f"/board/{bid}/hard-change",
               data={"title": "返修 U1", "occurred_at": "2026-06-17T10:00",
                     "description": "演示返修"})
    page.goto(f"{live_server}/board/{bid}")
    hard = page.locator(".tl-item.hard", has_text="返修 U1")
    expect(hard).not_to_have_class(re.compile(r".*\bdisabled\b.*"))
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    expect(hard).to_have_class(re.compile(r".*\bdisabled\b.*"))
    # 置灰后点击不应跳转到硬更改详情页
    hard.locator(".tl-card").first.click()
    assert page.url.endswith(f"/board/{bid}")
    # ⋯ 菜单按钮在对比模式下也应禁用
    expect(hard.locator(".menu-btn")).to_be_disabled()
