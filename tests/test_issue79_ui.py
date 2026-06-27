"""issue #79 优化状态演进界面（状态图卡片视觉统一 + ⋯ 菜单）的 UI 检验。"""
import httpx
from playwright.sync_api import Page, expect


_seq = [0]


def _make_chain(base: str):
    """建链：root → c1 → c2 → 草稿，并记一条硬更改。返回 (board_id)。"""
    _seq[0] += 1
    uid = f"SS{_seq[0]}"
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": "SG", "pcb_version": "v1",
                         "bom_version": "bomA", "board_uid": uid},
                   files={"file": ("bom.csv", b"Reference,Part\nR1,10k\n", "text/csv")})
        bid = r.headers["location"].split("?")[0].rsplit("/", 1)[-1]
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "R1", "op": "modify", "part": "47k"})
        c.post(f"/board/{bid}/commit", data={"message": "改 R1"})
        c.post(f"/board/{bid}/workspace/edit",
               data={"reference": "C9", "op": "add", "part": "100nF"})
        c.post(f"/board/{bid}/commit", data={"message": "加 C9"})
        c.post(f"/board/{bid}/hard-change",
               data={"title": "返修 U1", "occurred_at": "2026-06-17T10:00",
                     "description": "演示返修"})
    return bid


def _c1(page):
    return page.locator(".tl-item.node", has_text="改 R1")


def test_bom_card_no_hash_id_prefix_in_title(live_server, page: Page):
    """issue #5：BOM 变更第一行去掉「# xx」编号，改成 emoji 图标。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    title = _c1(page).locator("b").first.inner_text()
    assert title.startswith("📦"), f"应带 emoji 前缀，实际：{title!r}"
    assert "#" not in title, f"不应再含「#」编号，实际：{title!r}"


def test_bom_no_n_changes_badge(live_server, page: Page):
    """issue #3：去掉「x 条修改」文字。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    body = page.locator(".timeline").inner_text()
    assert "条修改" not in body, f"仍出现「x 条修改」：{body}"


def test_hard_card_no_hard_badge(live_server, page: Page):
    """issue #3：去掉第一行的「硬更改」badge。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    hard = page.locator(".tl-item.hard", has_text="返修 U1")
    expect(hard.locator(".badge-yellow")).to_have_count(0)


def test_second_line_is_datetime_plus_hash(live_server, page: Page):
    """issue #2：哈希与复制按钮移到第二行，与时间同行。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    item = _c1(page)
    title = item.locator("b").first
    expect(title.locator("code")).to_have_count(0)
    expect(title.locator(".copy-hash")).to_have_count(0)
    muted = item.locator(".muted").first
    expect(muted.locator("time")).to_have_count(1)
    expect(muted.locator("code")).to_have_count(1)
    expect(muted.locator(".copy-hash")).to_have_count(1)

    hard = page.locator(".tl-item.hard", has_text="返修 U1")
    h_title = hard.locator("b").first
    expect(h_title.locator("code")).to_have_count(0)
    h_muted = hard.locator(".muted").first
    expect(h_muted.locator("time")).to_have_count(1)
    expect(h_muted.locator("code")).to_have_count(1)
    expect(h_muted.locator(".copy-hash")).to_have_count(1)


def test_bom_title_color_not_blue(live_server, page: Page):
    """issue #4：BOM 变更标题改为正文色（不再是蓝色链接色）。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    title = _c1(page).locator("b").first
    color = title.evaluate("el => getComputedStyle(el).color")
    assert "9, 105, 218" not in color, f"标题仍为蓝色：{color}"


def test_bom_card_blue_left_bar(live_server, page: Page):
    """issue #6：BOM 变更卡片左侧加蓝色竖条，与硬更改一致风格。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    card = _c1(page).locator(".tl-card").first
    color = card.evaluate("el => getComputedStyle(el).borderLeftColor")
    width = card.evaluate("el => getComputedStyle(el).borderLeftWidth")
    assert "9, 105, 218" in color, f"左条非蓝色：{color}"
    assert width == "3px", f"左条宽度非 3px：{width}"


def test_no_underline_on_hover(live_server, page: Page):
    """issue #1：BOM 变更悬浮无下划线，与硬更改一致。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    card = _c1(page).locator(".tl-card").first
    card.hover()
    td = card.evaluate("el => getComputedStyle(el).textDecorationLine")
    assert "underline" not in td, f"BOM 变更悬浮有下划线：{td}"


def test_menu_button_visible_on_hover(live_server, page: Page):
    """issue #7：⋯ 按钮默认隐藏，悬浮出现。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    item = _c1(page)
    btn = item.locator(".menu-btn")
    opacity_before = btn.evaluate("el => getComputedStyle(el).opacity")
    assert float(opacity_before) == 0
    item.hover()
    page.wait_for_timeout(250)
    opacity_after = btn.evaluate("el => getComputedStyle(el).opacity")
    assert float(opacity_after) > 0


def test_menu_popover_has_insert_and_delete(live_server, page: Page):
    """issue #7：⋯ 菜单内含「在此后插入」和「删除」两项（已提交中间节点）。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    item = _c1(page)
    item.locator(".menu-btn").click()
    menu = item.locator(".menu-pop")
    expect(menu).to_be_visible()
    expect(menu.locator("button", has_text="在此后插入")).to_have_count(1)
    expect(menu.locator("button", has_text="删除")).to_have_count(1)


def test_menu_popover_hard_change_has_delete(live_server, page: Page):
    """issue #7：硬更改 ⋯ 菜单含「删除」。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    hard = page.locator(".tl-item.hard", has_text="返修 U1")
    hard.locator(".menu-btn").click()
    menu = hard.locator(".menu-pop")
    expect(menu).to_be_visible()
    expect(menu.locator("button", has_text="删除")).to_have_count(1)


def test_root_menu_has_insert_only(live_server, page: Page):
    """根节点可「在此后插入」但不可删除：菜单内只有插入项。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    root = page.locator(".tl-item.node.root")
    root.locator(".menu-btn").click()
    menu = root.locator(".menu-pop")
    expect(menu.locator("button", has_text="删除")).to_have_count(0)
    expect(menu.locator("button", has_text="在此后插入")).to_have_count(1)


def test_no_inline_insert_button(live_server, page: Page):
    """issue #7：原先内嵌的「＋在此后插入」按钮已被收进菜单，不再常驻卡片。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    card = _c1(page).locator(".tl-card")
    expect(card.locator("button.btn-link")).to_have_count(0)