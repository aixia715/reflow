"""issue #116 跨板对比的浏览器测试：按钮状态、对比页下拉切换。"""
import re
import httpx
from playwright.sync_api import Page, expect


def _open_menu(page):
    """功能入口收在 header ⋯ 菜单里（2026-07-18 设计），点击前先展开。"""
    page.click(".topnav-menu-btn")


def _new_board(base, name, bom_version, uid, csv):
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": name, "pcb_version": "v1",
                         "bom_version": bom_version, "board_uid": uid},
                   files={"file": ("bom.csv", csv, "text/csv")})
        return r.headers["location"].split("?")[0].rsplit("/", 1)[-1]


def _make_pair(base, prefix):
    """两块兄弟单板：{prefix}A(bomA, R1=10k)、{prefix}B(bomB, R1=10k 且已提交加 C9）。

    live_server 是 session 级共享库；兄弟范围按「单板名称 + PCB版本」划定，
    每个测试用独立 board_name 隔离，避免互相成为兄弟板。
    """
    name = f"XBoard{prefix}"
    ba = _new_board(base, name, "bomA", f"{prefix}A", b"Reference,Part\nR1,10k\n")
    bb = _new_board(base, name, "bomB", f"{prefix}B", b"Reference,Part\nR1,10k\n")
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        c.post(f"/board/{ba}/workspace/edit",
               data={"reference": "R9", "op": "add", "part": "1k"})
        c.post(f"/board/{ba}/commit", data={"message": "A板加R9"})
        c.post(f"/board/{bb}/workspace/edit",
               data={"reference": "C9", "op": "add", "part": "100nF"})
        c.post(f"/board/{bb}/commit", data={"message": "B板加C9"})
    return ba, bb


def test_cross_button_enabled_only_with_exactly_one_selected(live_server, page: Page):
    ba, _ = _make_pair(live_server, "X1")
    page.goto(f"{live_server}/board/{ba}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    cross = page.locator("[data-testid=cross-compare]")
    # 0 个：置灰
    expect(cross).to_be_visible()
    expect(cross).to_have_class(re.compile(r".*\bdisabled\b.*"))
    assert cross.get_attribute("href") == "#"
    cards = page.locator(".tl-item.node .tl-card")
    # 1 个：可用，href 带 left
    cards.nth(0).click()
    expect(cross).not_to_have_class(re.compile(r".*\bdisabled\b.*"))
    assert "/compare?left=" in cross.get_attribute("href")
    # 2 个：再次置灰
    cards.nth(1).click()
    expect(cross).to_have_class(re.compile(r".*\bdisabled\b.*"))
    assert cross.get_attribute("href") == "#"


def test_cross_button_absent_without_sibling(live_server, page: Page):
    with httpx.Client(base_url=live_server, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "LoneBoard", "pcb_version": "v9",
                         "bom_version": "bomA", "board_uid": "L1"},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n",
                                   "text/csv")})
        bid = r.headers["location"].split("?")[0].rsplit("/", 1)[-1]
    page.goto(f"{live_server}/board/{bid}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    expect(page.locator("[data-testid=cross-compare]")).to_have_count(0)


def test_cross_flow_lands_on_compare_with_sibling_default(live_server, page: Page):
    ba, bb = _make_pair(live_server, "X2")
    page.goto(f"{live_server}/board/{ba}")
    _open_menu(page)
    page.click("[data-testid=compare-toggle]")
    page.locator(".tl-item.node .tl-card").nth(0).click()
    page.click("[data-testid=cross-compare]")
    # 落在对比页，右侧默认 = 兄弟板 B1 的最新已提交节点（含 C9）
    assert f"/board/{ba}/compare?left=" in page.url
    expect(page.locator("[data-testid=cmp-board-right]")).to_have_value(str(bb))
    expect(page.locator("table.bom")).to_contain_text("C9")


def test_compare_page_node_dropdown_navigates(live_server, page: Page):
    ba, bb = _make_pair(live_server, "X3")
    with httpx.Client(base_url=live_server) as c:
        r = c.get(f"{live_server}/board/{ba}")
        left = sorted({int(x) for x in
                       re.findall(rf"/board/{ba}/node/(\d+)", r.text)})[0]
    page.goto(f"{live_server}/board/{ba}/compare?left={left}")
    # 右侧节点切到「初始状态」：URL 变化，两板初始 BOM 相同 → 完全一致
    page.select_option("[data-testid=cmp-node-right]", label="初始状态")
    page.wait_for_url(re.compile(r".*right=\d+.*"))
    expect(page.locator(".flash")).to_contain_text("两节点 BOM 完全一致")


def test_compare_page_board_dropdown_navigates_with_default_node(live_server, page: Page):
    ba, bb = _make_pair(live_server, "X4")
    with httpx.Client(base_url=live_server) as c:
        r = c.get(f"{live_server}/board/{ba}")
        left = sorted({int(x) for x in
                       re.findall(rf"/board/{ba}/node/(\d+)", r.text)})[0]
    # 进入时右侧默认 = B 板最新已提交（含 C9）
    page.goto(f"{live_server}/board/{ba}/compare?left={left}")
    expect(page.locator("table.bom")).to_contain_text("C9")
    # 右侧换板到 A 板：默认节点 = A 板最新已提交（含 R9），回到同板对比
    page.select_option("[data-testid=cmp-board-right]", str(ba))
    page.wait_for_url(re.compile(r".*right=\d+.*"))
    expect(page.locator("[data-testid=cmp-board-right]")).to_have_value(str(ba))
    expect(page.locator("table.bom")).to_contain_text("R9")
    # 再换回 B 板，默认节点应为其最新已提交节点（含 C9）
    page.select_option("[data-testid=cmp-board-right]", str(bb))
    page.wait_for_url(re.compile(r".*left=\d+&right=\d+.*"))
    expect(page.locator("table.bom")).to_contain_text("C9")
