"""issue #105：状态演进页面三点菜单被穿透的 UI 检验。

展开某节点的 ⋯ 菜单后，菜单应覆盖在下方节点的三点按钮之上；
被菜单遮挡的三点按钮在鼠标悬浮时不应穿透显示。
"""
from playwright.sync_api import Page, expect

from tests.test_issue79_ui import _make_chain, _c1


def test_open_menu_covers_lower_item_button(live_server, page: Page):
    """issue #105：展开 ⋯ 菜单后，被菜单遮挡的下方三点按钮不应穿透到菜单之上。"""
    bid = _make_chain(live_server)
    page.goto(f"{live_server}/board/{bid}")
    upper = _c1(page)  # 「改 R1」节点
    upper.locator(".menu-btn").click()
    menu_pop = upper.locator(".menu-pop")
    expect(menu_pop).to_be_visible()

    # 找到「被上方菜单覆盖」的另一个三点按钮的中心点（位于其它时间线项）
    info = page.evaluate("""(mpEl) => {
        const mp = mpEl.getBoundingClientRect();
        const self = mpEl.closest('.tl-item');
        for (const b of document.querySelectorAll('.tl-item .menu-btn')) {
            if (self.contains(b)) continue;  // 跳过展开菜单自身所在项
            const r = b.getBoundingClientRect();
            const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
            if (cx >= mp.x && cx <= mp.x + mp.width
                && cy >= mp.y && cy <= mp.y + mp.height) {
                return { found: true, cx, cy };
            }
        }
        return { found: false };
    }""", menu_pop.element_handle())
    assert info["found"], "未找到被菜单覆盖的其它三点按钮，测试前置不成立"
    cx, cy = info["cx"], info["cy"]

    # 该坐标的最顶层元素应属于上方菜单（.menu-pop），而非穿透上来的下方按钮
    in_menu = page.evaluate(
        "([x, y]) => { const el = document.elementFromPoint(x, y);"
        "return !!(el && el.closest('.menu-pop')); }", [cx, cy])
    assert in_menu, "被菜单遮挡的三点按钮穿透显示在菜单之上"

    # 行为复测：鼠标移到该坐标后，被遮挡的按钮不应进入悬浮显示态
    page.mouse.move(cx, cy)
    page.wait_for_timeout(250)
    opacity = page.evaluate("""([x, y]) => {
        let best = null, bestD = 1e9;
        for (const b of document.querySelectorAll('.menu-btn')) {
            const r = b.getBoundingClientRect();
            const d = (r.x + r.width/2 - x) ** 2 + (r.y + r.height/2 - y) ** 2;
            if (d < bestD) { bestD = d; best = b; }
        }
        return best ? getComputedStyle(best).opacity : null;
    }""", [cx, cy])
    assert float(opacity) == 0, f"被遮挡的三点按钮仍悬浮显示：opacity={opacity}"
