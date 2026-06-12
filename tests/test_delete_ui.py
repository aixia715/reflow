"""
Playwright 浏览器自动化测试——验证删除功能的 UI 交互。

运行：
    pytest tests/test_delete_ui.py -v
    pytest tests/test_delete_ui.py -v --headed   # 可视化模式
"""
import httpx
import pytest
from playwright.sync_api import Page, expect


# ── 工具函数 ─────────────────────────────────────────────────────────

def _api_create_board(base: str, board_name: str, pcb: str, bom: str, uid: str,
                      csv_content: str = "Reference,Part\nR1,10k\nC1,100nF\n") -> str:
    """通过 httpx 直接 POST 创建单板，返回 board_id。"""
    with httpx.Client(base_url=base, follow_redirects=False) as c:
        r = c.post("/board/new",
                   data={"board_name": board_name, "pcb_version": pcb,
                         "bom_version": bom, "board_uid": uid},
                   files={"file": ("bom.csv", csv_content.encode(), "text/csv")})
    loc = r.headers.get("location", "")
    return loc.split("/board/")[-1].split("?")[0]


def _click_accept_and_check(page: Page, btn, gone_text: str, base: str):
    """
    点击删除按钮，接受 confirm 弹窗，等待操作完成后确认数据消失。
    用 page.goto 强制刷新确认服务端数据真正删除。
    """
    dialog_fired = []
    def handle(d):
        dialog_fired.append(True)
        d.accept()

    page.once("dialog", handle)
    btn.click()
    # 等待 dialog 触发和 HTMX 请求完成
    page.wait_for_timeout(2000)

    assert dialog_fired, f"hx-confirm 弹窗未触发（按钮 hx-delete={btn.get_attribute('hx-delete')!r}）"

    # 强制刷新获取服务端最新状态
    page.goto(base + "/")
    page.wait_for_load_state("networkidle")
    assert gone_text not in page.content(), f"删除后 {gone_text!r} 仍出现在页面"


# ── 测试：按钮可见性 ─────────────────────────────────────────────────

def test_delete_buttons_visible_on_home(seeded_server, page: Page):
    """首页加载后，三个层级的删除按钮均可见。"""
    page.goto(seeded_server)
    del_icons = page.locator(".del-icon")
    expect(del_icons.first).to_be_visible()
    count = del_icons.count()
    assert count >= 3, f"期望 ≥3 个删除按钮，实际 {count} 个"


def test_chip_del_button_exists(seeded_server, page: Page):
    """每个单板芯片旁应有 .chip-del（×）按钮。"""
    page.goto(seeded_server)
    chip_del = page.locator(".chip-del")
    expect(chip_del.first).to_be_visible()


def test_del_icon_initial_opacity(seeded_server, page: Page):
    """删除按钮初始应为半透明（opacity < 0.8）。"""
    page.goto(seeded_server)
    btn = page.locator(".del-icon").first
    opacity = float(btn.evaluate("el => parseFloat(getComputedStyle(el).opacity)"))
    assert opacity < 0.8, f"期望初始 opacity < 0.8，实际 {opacity}"


def test_del_icon_hover_turns_red(seeded_server, page: Page):
    """悬停后删除按钮颜色应变为红色系。"""
    page.goto(seeded_server)
    btn = page.locator(".del-icon").first
    btn.hover()
    page.wait_for_timeout(200)  # 等待 CSS transition（150ms）
    color = btn.evaluate("el => getComputedStyle(el).color")
    parts = [int(x) for x in color.replace("rgb(", "").replace(")", "").split(",")]
    assert parts[0] > parts[1] and parts[0] > parts[2], f"期望红色，实际颜色 {color}"


# ── 测试：确认弹窗交互 ────────────────────────────────────────────────

def test_board_delete_cancel_keeps_data(live_server, page: Page):
    """点击单板删除 × → dismiss confirm → 无 DELETE 请求 → 单板芯片仍在。"""
    board_id = _api_create_board(live_server, "CancelBoard", "v1", "bomCancel", "CN001")
    page.goto(live_server)

    chip_del = page.locator(f"[hx-delete='/board/{board_id}']")
    expect(chip_del).to_be_visible()

    page.once("dialog", lambda d: d.dismiss())
    chip_del.click()
    page.wait_for_timeout(500)

    expect(chip_del).to_be_visible()


def test_board_delete_confirm_removes_board(live_server, page: Page):
    """点击单板删除 × → accept → HX-Redirect 触发 → 单板消失。"""
    board_id = _api_create_board(live_server, "DelBoard", "v1", "bomDel", "DB002")
    page.goto(live_server)

    chip_del = page.locator(f"[hx-delete='/board/{board_id}']")
    expect(chip_del).to_be_visible()

    _click_accept_and_check(page, chip_del, "DB002", live_server)


def test_bom_version_delete_confirm(live_server, page: Page):
    """点击 BOM 版本 🗑 → accept → 版本及其单板消失。"""
    _api_create_board(live_server, "BomDelBoard", "v1", "bomDel2", "BD002")
    page.goto(live_server)

    bom_del = page.locator("[hx-delete*='/bom-version'][hx-delete*='BomDelBoard']")
    expect(bom_del).to_be_visible()

    _click_accept_and_check(page, bom_del, "BomDelBoard", live_server)


def test_board_group_delete_confirm(live_server, page: Page):
    """点击组 🗑 → accept → 整组消失。"""
    _api_create_board(live_server, "GroupDelBoard", "v1", "bomG", "GD002")
    page.goto(live_server)

    group_del = page.locator("[hx-delete*='/board-group'][hx-delete*='GroupDelBoard']")
    expect(group_del).to_be_visible()

    _click_accept_and_check(page, group_del, "GroupDelBoard", live_server)
